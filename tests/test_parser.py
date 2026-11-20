import os
import re
import shutil
from pathlib import Path

import pytest

from prt import parser

FIXTURES = Path(__file__).parent / "fixtures"
ID_COMMENT = re.compile(r" <!-- prt:[0-9a-f]{8} -->")


def test_desk_memo_fixture():
    plan = parser.parse((FIXTURES / "desk_memo_plan.md").read_text())
    assert plan.title == "Desk memo editor and commented export"
    assert [pr.number for pr in plan.prs] == [str(n) for n in range(1, 10)]
    assert {pr.number: pr.depends_on for pr in plan.prs} == {
        "1": [],
        "2": ["1"],
        "3": ["2"],
        "4": ["3"],
        "5": ["2"],
        "6": ["3", "5"],
        "7": ["6"],
        "8": ["7"],
        "9": ["7"],
    }
    first = plan.prs[0]
    assert first.title == "Resolve each LEAD's data through a scope id"
    assert first.scope_md.startswith("`Lead.scope_id` (= `id`), `app/scope.py`")
    assert first.id is None
    assert plan.notes_md.startswith("PR 4 runs in parallel with 5 and 6")
    assert plan.notes_md.endswith("model-drafted memo content.")
    assert "[`desk-memos.md`](https://github.com/" in plan.notes_md


def test_column_mapping_unescape_and_ids():
    text = """# T

## PR order

| Depends on | Title | # | Description |
|:--|--|--:|--|
| 1, 2 | Add `a\\|b` <!-- prt:3f9a1c2e --> | 3 | Uses `x \\| y` |
| - | Second | 4 | |

## Next
"""
    plan = parser.parse(text)
    [a, b] = plan.prs
    assert (a.number, a.title, a.scope_md, a.depends_on, a.id) == (
        "3",
        "Add `a|b`",
        "Uses `x | y`",
        ["1", "2"],
        "3f9a1c2e",
    )
    assert (b.number, b.title, b.scope_md, b.depends_on, b.id) == ("4", "Second", "", [], None)
    assert plan.notes_md == ""


def test_heading_fallback():
    text = """# Heading plan

## PR plan

Some notes.

### PR 1: First <!-- prt:0000abcd -->

Do the first thing.

- with a list

### PR 2: Second

Do the second.

## After

### PR 3: Not in the section
"""
    plan = parser.parse(text)
    assert plan.notes_md == "Some notes."
    assert [(p.number, p.title, p.id) for p in plan.prs] == [
        ("1", "First", "0000abcd"),
        ("2", "Second", None),
    ]
    assert plan.prs[0].scope_md == "Do the first thing.\n\n- with a list"
    assert plan.prs[1].scope_md == "Do the second."


def test_not_a_plan_and_fenced_headings(tmp_path):
    assert parser.parse("# T\n\n## Other\n") is None
    other = tmp_path / "other.md"
    other.write_text("# T\n")
    assert parser.assign_ids(other) is None
    assert other.read_text() == "# T\n"
    # A ~~~ line inside a ``` block does not close it.
    assert parser.parse("````\n~~~\n## PR plan\n```\n````\n") is None
    text = (
        "```\n# not title\n## PR plan\n```\n# Real\n\n## PR plan\n\n| # | PR |\n|-|-|\n| 1 | A |\n"
    )
    plan = parser.parse(text)
    assert plan.title == "Real"
    assert [p.title for p in plan.prs] == ["A"]


@pytest.fixture
def spec(tmp_path):
    p = tmp_path / "spec.md"
    shutil.copy(FIXTURES / "desk_memo_plan.md", p)
    return p


def test_write_back_adds_ids_only_where_missing(spec):
    original = spec.read_text().splitlines(keepends=True)
    # Give PR 2 an id up front; its row must stay byte-identical.
    row2 = next(i for i, ln in enumerate(original) if ln.startswith("| 2 |"))
    original[row2] = original[row2].replace(
        "own their LEAD's data |", "own their LEAD's data <!-- prt:aaaaaaaa --> |"
    )
    spec.write_text("".join(original))

    plan, mtime = parser.assign_ids(spec)
    assert mtime == spec.stat().st_mtime
    ids = [pr.id for pr in plan.prs]
    assert ids[1] == "aaaaaaaa"
    assert all(re.fullmatch(r"[0-9a-f]{8}", i) for i in ids)
    assert len(set(ids)) == 9

    updated = spec.read_text().splitlines(keepends=True)
    assert len(updated) == len(original)
    for i, (old, new) in enumerate(zip(original, updated, strict=True)):
        if i == row2 or not old.startswith("| ") or old.startswith("| # "):
            assert new == old
        else:
            assert ID_COMMENT.sub("", new) == old
            assert new.count("<!-- prt:") == 1

    # Re-parsing reads the same ids, and a second pass writes nothing.
    assert [pr.id for pr in parser.parse(spec.read_text()).prs] == ids
    before = spec.read_bytes()
    parser.assign_ids(spec)
    assert spec.read_bytes() == before


def test_new_ids_avoid_taken(spec, monkeypatch):
    hexes = iter(["11111111", "22222222"] + [f"{n:08x}" for n in range(100, 200)])
    monkeypatch.setattr(parser.uuid, "uuid4", lambda: type("U", (), {"hex": next(hexes)}))
    plan, _ = parser.assign_ids(spec, taken={"11111111"})
    assert plan.prs[0].id == "22222222"
    assert "11111111" not in [pr.id for pr in plan.prs]


def test_changed_mtime_aborts_write(spec):
    text = spec.read_text()
    plan = parser.parse(text)
    mtime = spec.stat().st_mtime
    os.utime(spec, (mtime + 5, mtime + 5))
    with pytest.raises(parser.StaleFile):
        parser.write_back(spec, parser.add_ids(text, plan, {0: "abcdef01"}), mtime)
    assert spec.read_text() == text
    assert list(spec.parent.iterdir()) == [spec]


def test_write_back_keeps_mode(spec):
    spec.chmod(0o640)
    parser.assign_ids(spec)
    assert spec.stat().st_mode & 0o777 == 0o640


def test_heading_write_back(tmp_path):
    spec = tmp_path / "spec.md"
    spec.write_text("# T\n\n## PR plan\n\n| # | PR |\n|-|-|\n\n### PR 1: First  \r\nBody.\n")
    plan, _ = parser.assign_ids(spec)
    [pr] = plan.prs
    assert spec.read_bytes().decode().endswith(f"### PR 1: First <!-- prt:{pr.id} -->  \r\nBody.\n")
    assert parser.parse(spec.read_text()).prs[0].id == pr.id
