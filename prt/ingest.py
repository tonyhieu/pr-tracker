"""Register plans from spec files and keep them in sync with the files."""

import asyncio
import json
import logging
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path

from prt import db, parser

log = logging.getLogger(__name__)

WATCH_SECONDS = 5.0
SUBPROCESS_TIMEOUT = 10.0


class NotAPlan(Exception):
    """The file has no `## PR plan` or `## PR order` section."""


class OutsideRoot(Exception):
    """The path does not resolve under the plans root."""


def root() -> Path:
    return Path(os.environ.get("PRT_ROOT", Path.home() / "GitHub")).expanduser().resolve()


def resolve(path: str) -> Path:
    """Absolute, symlink-free spec path; raises OutsideRoot unless it is under `root()`."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise OutsideRoot(f"{path} is not an absolute path")
    p = p.resolve()
    if not p.is_relative_to(root()):
        raise OutsideRoot(f"{p} is not under {root()}")
    return p


async def run(*cmd: str, cwd: Path) -> str | None:
    """Stripped stdout of `cmd`, or None on any failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as e:
        log.warning("%s failed: %s", cmd[0], e)
        return None
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), SUBPROCESS_TIMEOUT)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        log.warning("%s timed out", cmd[0])
        return None
    if proc.returncode != 0:
        return None
    return out.decode().strip() or None


async def repo_info(spec: Path) -> tuple[str | None, str | None]:
    """(repo_dir, gh_repo) for the spec's directory; None where unknown."""
    repo_dir = await run("git", "rev-parse", "--show-toplevel", cwd=spec.parent)
    if repo_dir is None:
        return None, None
    gh_repo = await run(
        "gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner", cwd=Path(repo_dir)
    )
    return repo_dir, gh_repo


def _exists(conn: sqlite3.Connection, table: str) -> Callable[[str], bool]:
    return lambda i: (
        conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (i,)).fetchone() is not None
    )


def ingest(
    conn: sqlite3.Connection,
    path: Path,
    repo: tuple[str | None, str | None] | None = None,
) -> str:
    """Parse `path` into the plan stored for it and write new PR ids back; returns the plan id.

    `repo` is (repo_dir, gh_repo) from `repo_info`; None keeps the stored values.
    Raises NotAPlan, FileNotFoundError, or parser.StaleFile (nothing is committed then).
    """
    mtime = path.stat().st_mtime
    text = path.read_bytes().decode()
    plan = parser.parse(text)
    if plan is None:
        raise NotAPlan(f"{path} has no PR plan section")

    row = conn.execute("SELECT * FROM plans WHERE path = ?", (str(path),)).fetchone()
    plan_id = row["id"] if row else parser.new_id(_exists(conn, "plans"))
    existing = [
        db.pr_dict(r) for r in conn.execute("SELECT * FROM prs WHERE plan_id = ?", (plan_id,))
    ]
    by_id = {p["id"]: p for p in existing}

    # Match by id, then by exact title among PRs not matched yet.
    matched: dict[int, dict] = {}
    for idx, pr in enumerate(plan.prs):
        if pr.id in by_id and by_id[pr.id] not in matched.values():
            matched[idx] = by_id[pr.id]
    for idx, pr in enumerate(plan.prs):
        if pr.id is None:
            free = (p for p in existing if p not in matched.values() and p["title"] == pr.title)
            if (p := next(free, None)) is not None:
                matched[idx] = p

    # Final id per row; ids copied from another plan or repeated in this one are replaced.
    pr_taken = _exists(conn, "prs")
    used = {p["id"] for p in matched.values()}
    ids: dict[int, str] = {}
    insert: dict[int, str] = {}  # rows with no id comment yet
    replace: dict[int, str] = {}  # rows whose id comment must change
    for idx, pr in enumerate(plan.prs):
        if idx in matched:
            ids[idx] = matched[idx]["id"]
        elif pr.id is not None and pr.id not in used and not pr_taken(pr.id):
            ids[idx] = pr.id
        else:
            ids[idx] = parser.new_id(lambda i: i in used or pr_taken(i))
        used.add(ids[idx])
        if ids[idx] != pr.id:
            (insert if pr.id is None else replace)[idx] = ids[idx]

    number_to_id: dict[str, str] = {}
    for idx, pr in enumerate(plan.prs):
        number_to_id.setdefault(pr.number, ids[idx])

    ts = db.now()
    with conn:
        if row is None:
            repo_dir, gh_repo = repo or (None, None)
            conn.execute(
                "INSERT INTO plans (id, path, repo_dir, gh_repo, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (plan_id, str(path), repo_dir, gh_repo, ts),
            )
        elif repo is not None:
            conn.execute(
                "UPDATE plans SET repo_dir = ?, gh_repo = ? WHERE id = ?", (*repo, plan_id)
            )
        for idx, pr in enumerate(plan.prs):
            deps = [number_to_id[n] for n in pr.depends_on if n in number_to_id]
            content = (pr.number, pr.title, pr.scope_md, json.dumps(deps), idx)
            if idx in matched:
                conn.execute(
                    "UPDATE prs SET number = ?, title = ?, scope_md = ?, depends_on = ?,"
                    " position = ?, removed = 0 WHERE id = ?",
                    (*content, ids[idx]),
                )
            else:
                conn.execute(
                    "INSERT INTO prs (id, plan_id, number, title, scope_md, depends_on, position,"
                    " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (ids[idx], plan_id, *content, ts),
                )
        kept = set(ids.values())
        for p in existing:
            if p["id"] not in kept and not p["removed"]:
                conn.execute("UPDATE prs SET removed = 1 WHERE id = ?", (p["id"],))

        # Write ids back last, so a stale file rolls back the DB changes above.
        if insert or replace:
            lines = text.splitlines(keepends=True)
            for idx, pr_id in replace.items():
                pr = plan.prs[idx]
                lines[pr.line] = lines[pr.line].replace(f"prt:{pr.id}", f"prt:{pr_id}", 1)
            mtime = parser.write_back(path, parser.add_ids("".join(lines), plan, insert), mtime)
        conn.execute(
            "UPDATE plans SET title = ?, notes_md = ?, mtime = ?, missing = 0 WHERE id = ?",
            (plan.title, plan.notes_md, mtime, plan_id),
        )
    return plan_id


def refresh(conn: sqlite3.Connection) -> list[str]:
    """One watch cycle: re-parse changed plan files and flag deleted ones; returns changed ids."""
    changed = []
    plans = conn.execute("SELECT id, path, mtime, missing FROM plans WHERE archived = 0").fetchall()
    for p in plans:
        path = Path(p["path"])
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            if not p["missing"]:
                with conn:
                    conn.execute("UPDATE plans SET missing = 1 WHERE id = ?", (p["id"],))
                changed.append(p["id"])
            continue
        if mtime == p["mtime"] and not p["missing"]:
            continue
        try:
            ingest(conn, path)
        except NotAPlan as e:
            # Keep the last parsed state; store the mtime so it is not retried every cycle.
            log.warning("%s", e)
            with conn:
                conn.execute(
                    "UPDATE plans SET mtime = ?, missing = 0 WHERE id = ?", (mtime, p["id"])
                )
            changed.append(p["id"])
            continue
        except (parser.StaleFile, OSError, UnicodeDecodeError) as e:
            log.warning("re-parse of %s skipped, retrying next cycle: %s", path, e)
            continue
        changed.append(p["id"])
    return changed


async def watch(
    conn: sqlite3.Connection, publish: Callable[[str], None], interval: float = WATCH_SECONDS
) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            for plan_id in refresh(conn):
                publish(plan_id)
        except Exception:
            log.exception("plan watcher cycle failed")
