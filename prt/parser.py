"""Parse PR plans from spec markdown and write PR ids back into the file."""

import os
import re
import stat
import tempfile
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

SECTION_RE = re.compile(r"^##\s+PR\s+(plan|order)\s*$", re.IGNORECASE)
H2_RE = re.compile(r"^##\s")
H3_RE = re.compile(r"^###\s")
TITLE_RE = re.compile(r"^#\s+(.*?)\s*$")
PR_HEADING_RE = re.compile(r"^###\s+PR\s+(\S+?):\s*(.*?)\s*$")
ID_RE = re.compile(r"\s*<!--\s*prt:([0-9a-f]{8})\s*-->")
SEPARATOR_RE = re.compile(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
CELL_SEP_RE = re.compile(r"(?<!\\)\|")


class StaleFile(Exception):
    """The spec changed on disk between reading it and writing ids back."""


@dataclass
class ParsedPR:
    number: str
    title: str
    scope_md: str
    depends_on: list[str]  # PR numbers, as written in the plan
    id: str | None
    line: int  # index of the line holding the title, for id write-back
    title_end: int  # offset in that line where an id comment is inserted


@dataclass
class ParsedPlan:
    title: str
    notes_md: str
    prs: list[ParsedPR] = field(default_factory=list)


def _unescape(cell: str) -> str:
    return cell.replace("\\|", "|")


def _cells(line: str) -> list[tuple[int, int]]:
    """(start, end) spans of each cell's stripped content in a table row."""
    bounds = [m.start() for m in CELL_SEP_RE.finditer(line)]
    body = line.rstrip("\r\n")
    edges = [-1, *bounds, len(body)]
    spans = []
    for a, b in zip(edges, edges[1:], strict=False):
        start, end = a + 1, b
        while start < end and body[start].isspace():
            start += 1
        while end > start and body[end - 1].isspace():
            end -= 1
        spans.append((start, end))
    # Leading and trailing pipes leave empty edge cells.
    if body.lstrip().startswith("|"):
        spans = spans[1:]
    if body.rstrip().endswith("|") and not body.rstrip().endswith("\\|"):
        spans = spans[:-1]
    return spans


def _column(header: str) -> str | None:
    h = header.strip().lower()
    if h == "#":
        return "number"
    if h in ("pr", "title"):
        return "title"
    if h.startswith("scope") or h == "description":
        return "scope"
    if h == "depends on":
        return "depends"
    return None


def _split_title(raw: str) -> tuple[str, str | None]:
    m = ID_RE.search(raw)
    if not m:
        return raw.strip(), None
    return (raw[: m.start()] + raw[m.end() :]).strip(), m.group(1)


def _deps(cell: str) -> list[str]:
    return [d.strip() for d in cell.split(",") if d.strip() and d.strip() != "-"]


def _live_lines(lines: list[str]) -> list[bool]:
    """True for lines outside fenced code blocks."""
    out, fence = [], None
    for line in lines:
        m = FENCE_RE.match(line)
        if fence is None and m:
            fence = m.group(1)
            out.append(False)
        elif fence is not None:
            # A fence closes only with the same character, at least as long, and nothing after.
            closer = m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence)
            if closer and not line[m.end() :].strip():
                fence = None
            out.append(False)
        else:
            out.append(True)
    return out


def _parse_table(lines: list[str], start: int, end: int) -> tuple[list[ParsedPR], int] | None:
    """PRs from the first table in lines[start:end], and the index after the table."""
    for i in range(start, end - 1):
        if lines[i].lstrip().startswith("|") and SEPARATOR_RE.match(lines[i + 1].strip()):
            break
    else:
        return None
    header = lines[i]
    cols = [_column(header[a:b]) for a, b in _cells(header)]
    if "number" not in cols or "title" not in cols:
        return None
    prs, j = [], i + 2
    while j < end and lines[j].lstrip().startswith("|"):
        line = lines[j]
        spans = _cells(line)
        vals = {c: (line[a:b], b) for c, (a, b) in zip(cols, spans, strict=False) if c}
        if "number" in vals and "title" in vals:
            raw_title, title_end = vals["title"]
            title, pr_id = _split_title(raw_title)
            prs.append(
                ParsedPR(
                    number=_unescape(vals["number"][0]).strip(),
                    title=_unescape(title),
                    scope_md=_unescape(vals.get("scope", ("", 0))[0]),
                    depends_on=_deps(_unescape(vals.get("depends", ("", 0))[0])),
                    id=pr_id,
                    line=j,
                    title_end=title_end,
                )
            )
        j += 1
    return prs, j


def _parse_headings(
    lines: list[str], live: list[bool], start: int, end: int
) -> tuple[list[ParsedPR], int]:
    """PRs from `### PR <n>: <title>` headings, and the index of the first one."""
    heads = [i for i in range(start, end) if live[i] and PR_HEADING_RE.match(lines[i])]
    prs = []
    for i in heads:
        m = PR_HEADING_RE.match(lines[i].rstrip("\r\n"))
        body_end = next((k for k in range(i + 1, end) if live[k] and H3_RE.match(lines[k])), end)
        title, pr_id = _split_title(m.group(2))
        prs.append(
            ParsedPR(
                number=m.group(1),
                title=title,
                scope_md="".join(lines[i + 1 : body_end]).strip(),
                depends_on=[],
                id=pr_id,
                line=i,
                title_end=len(lines[i].rstrip()),
            )
        )
    return prs, heads[0] if heads else end


def parse(text: str) -> ParsedPlan | None:
    """Parse a spec; None when it has no `## PR plan` or `## PR order` section."""
    lines = text.splitlines(keepends=True)
    live = _live_lines(lines)
    title = next(
        (m.group(1) for i, ln in enumerate(lines) if live[i] and (m := TITLE_RE.match(ln))), ""
    )
    start = next((i for i, ln in enumerate(lines) if live[i] and SECTION_RE.match(ln)), None)
    if start is None:
        return None
    start += 1
    end = next((i for i in range(start, len(lines)) if live[i] and H2_RE.match(lines[i])), None)
    end = len(lines) if end is None else end

    table = _parse_table(lines, start, end)
    if table and table[0]:
        prs, after = table
        notes = "".join(lines[after:end])
    else:
        prs, first = _parse_headings(lines, live, start, end)
        notes = "".join(lines[start:first])
    return ParsedPlan(title=title, notes_md=notes.strip(), prs=prs)


Taken = Iterable[str] | Callable[[str], bool]


def _predicate(taken: Taken) -> Callable[[str], bool]:
    return taken if callable(taken) else set(taken).__contains__


def new_id(taken: Taken = ()) -> str:
    """An 8-hex id not in `taken` (a collection or a predicate)."""
    is_taken = _predicate(taken)
    while True:
        pr_id = uuid.uuid4().hex[:8]
        if not is_taken(pr_id):
            return pr_id


def add_ids(text: str, plan: ParsedPlan, ids: dict[int, str]) -> str:
    """Insert `<!-- prt:<id> -->` after the title of each PR index in `ids`."""
    lines = text.splitlines(keepends=True)
    for idx, pr_id in ids.items():
        pr = plan.prs[idx]
        line = lines[pr.line]
        lines[pr.line] = f"{line[: pr.title_end]} <!-- prt:{pr_id} -->{line[pr.title_end :]}"
    return "".join(lines)


def write_back(path: Path, text: str, mtime: float) -> float:
    """Atomically replace `path` with `text` if its mtime is still `mtime`; returns the new one."""
    st = path.stat()
    if st.st_mtime != mtime:
        raise StaleFile(str(path))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode())
        os.chmod(tmp, stat.S_IMODE(st.st_mode))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path.stat().st_mtime


def assign_ids(path: Path, taken: Taken = ()) -> tuple[ParsedPlan, float] | None:
    """Parse `path`, give rows without an id a new one, and write the ids back.

    Returns the plan (every PR with an id) and the file's mtime, or None for a non-plan.
    Raises StaleFile when the file changes before the write.
    """
    path = Path(path)
    mtime = path.stat().st_mtime
    text = path.read_bytes().decode()
    plan = parse(text)
    if plan is None:
        return None
    is_taken = _predicate(taken)
    used = {pr.id for pr in plan.prs if pr.id}
    ids: dict[int, str] = {}
    for idx, pr in enumerate(plan.prs):
        if pr.id is None:
            ids[idx] = new_id(lambda i: i in used or is_taken(i))
            used.add(ids[idx])
    if ids:
        mtime = write_back(path, add_ids(text, plan, ids), mtime)
        for idx, pr_id in ids.items():
            plan.prs[idx].id = pr_id
    return plan, mtime
