import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from prt import db, ingest, parser
from prt.app import create_app

FIXTURES = Path(__file__).parent / "fixtures"
PLAN = """# Demo

## PR plan

| # | PR | Scope | Depends on |
|---|---|---|---|
| 1 | First | Do one. | - |
| 2 | Second | Do two. | 1 |
| 3 | Third | Do three. | 1, 2 |

Notes here.
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A git repo under a fake `~/GitHub` root, with a fake `gh` on PATH."""
    root = tmp_path / "GitHub"
    repo = root / "proj"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text("#!/bin/sh\necho owner/proj\n")
    gh.chmod(0o755)
    monkeypatch.setenv("PRT_ROOT", str(root))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return repo


@pytest.fixture
def client(db_path):
    with TestClient(create_app(db_path, watch_interval=0.05)) as c:
        yield c


def write(path, text):
    """Write and bump the mtime, so back-to-back writes always look changed to the watcher."""
    old = path.stat().st_mtime if path.exists() else 0
    path.write_text(text)
    os.utime(path, (old + 1, old + 1))


def register(client, path):
    r = client.post("/api/plans", json={"path": str(path)})
    assert r.status_code == 200, r.text
    return r.json()


def by_number(plan):
    return {p["number"]: p for p in plan["prs"] if not p["removed"]}


def wait_for(fn, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fn():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


def test_register_records_plan_and_writes_ids(client, repo):
    spec = repo / "spec.md"
    write(spec, PLAN)
    plan = register(client, spec)
    assert plan["title"] == "Demo"
    assert plan["notes_md"] == "Notes here."
    assert plan["path"] == str(spec.resolve())
    assert plan["repo_dir"] == str(repo.resolve())
    assert plan["gh_repo"] == "owner/proj"
    prs = by_number(plan)
    assert prs["3"]["depends_on"] == [prs["1"]["id"], prs["2"]["id"]]
    assert prs["2"]["ready"] is False and prs["1"]["ready"] is True
    assert [p.id for p in parser.parse(spec.read_text()).prs] == [prs[n]["id"] for n in "123"]
    # Re-registering changes nothing.
    before = spec.read_bytes()
    assert register(client, spec)["id"] == plan["id"]
    assert spec.read_bytes() == before
    assert len(client.get("/api/plans").json()) == 1


def test_desk_memo_fixture_registers(client, repo):
    spec = repo / "desk.md"
    shutil.copy(FIXTURES / "desk_memo_plan.md", spec)
    prs = by_number(register(client, spec))
    assert len(prs) == 9
    assert prs["6"]["depends_on"] == [prs["3"]["id"], prs["5"]["id"]]


def test_renumbering_keeps_status(client, repo):
    spec = repo / "spec.md"
    write(spec, PLAN)
    pr_id = by_number(register(client, spec))["2"]["id"]
    client.patch(f"/api/prs/{pr_id}", json={"status": "Implementing", "source": "agent"})
    text = spec.read_text().replace("| 2 | Second", "| 7 | Second")
    write(spec, text)
    prs = by_number(register(client, spec))
    assert prs["7"]["id"] == pr_id
    assert prs["7"]["status"] == "Implementing"


def test_retitled_row_with_id_keeps_status(client, repo):
    spec = repo / "spec.md"
    write(spec, PLAN)
    pr_id = by_number(register(client, spec))["1"]["id"]
    client.patch(f"/api/prs/{pr_id}", json={"status": "Implementing", "source": "agent"})
    write(spec, spec.read_text().replace("| First <!--", "| Renamed first <!--"))
    pr = by_number(register(client, spec))["1"]
    assert (pr["id"], pr["title"], pr["status"]) == (pr_id, "Renamed first", "Implementing")


def test_stripped_id_matches_by_title_and_is_restored(client, repo):
    spec = repo / "spec.md"
    write(spec, PLAN)
    pr_id = by_number(register(client, spec))["2"]["id"]
    client.patch(f"/api/prs/{pr_id}", json={"status": "Implementing", "source": "agent"})
    write(spec, spec.read_text().replace(f" <!-- prt:{pr_id} -->", ""))
    pr = by_number(register(client, spec))["2"]
    assert (pr["id"], pr["status"]) == (pr_id, "Implementing")
    assert f"Second <!-- prt:{pr_id} -->" in spec.read_text()


def test_deleted_row_becomes_removed_and_comes_back(client, repo):
    spec = repo / "spec.md"
    write(spec, PLAN)
    register(client, spec)
    text = spec.read_text()
    row3 = next(ln for ln in text.splitlines(keepends=True) if ln.startswith("| 3 |"))
    write(spec, text.replace(row3, ""))
    plan = register(client, spec)
    removed = [p for p in plan["prs"] if p["removed"]]
    assert [p["number"] for p in removed] == ["3"]
    assert plan["counts"]["TBD"] == 2
    # Restoring the row (with its id) un-removes the same PR.
    write(spec, text)
    prs = by_number(register(client, spec))
    assert prs["3"]["id"] == removed[0]["id"]


def test_foreign_and_duplicate_ids_are_replaced(client, repo):
    first = repo / "a.md"
    write(first, PLAN)
    ids = [p["id"] for p in register(client, first)["prs"]]
    # A copied spec carries the first plan's ids, and one row repeats another's id.
    copy = repo / "b.md"
    write(copy, first.read_text().replace(f"prt:{ids[1]}", f"prt:{ids[0]}"))
    other = register(client, copy)["prs"]
    assert not {p["id"] for p in other} & set(ids)
    assert len({p["id"] for p in other}) == 3
    assert [p.id for p in parser.parse(copy.read_text()).prs] == [p["id"] for p in other]


@pytest.mark.parametrize(
    ("make_path", "code"),
    [
        (lambda repo: repo.parent.parent / "outside.md", 403),
        (lambda repo: repo / ".." / ".." / "outside.md", 403),
        (lambda repo: "proj/spec.md", 403),
        (lambda repo: repo / "nope.md", 404),
        (lambda repo: repo / "notes.md", 422),
    ],
)
def test_register_rejects(client, repo, make_path, code):
    (repo.parent.parent / "outside.md").write_text(PLAN)
    (repo / "notes.md").write_text("# Just notes\n")
    r = client.post("/api/plans", json={"path": str(make_path(repo))})
    assert r.status_code == code, r.text
    assert client.get("/api/plans").json() == []


def test_register_validation(client, repo):
    assert client.post("/api/plans", json={"path": 5}).status_code == 422
    assert client.post("/api/plans", json={"paths": "x"}).status_code == 422
    assert client.post("/api/plans", content=b"nope").status_code == 422


def test_watcher_reparses_edited_file(client, repo):
    spec = repo / "spec.md"
    write(spec, PLAN)
    plan_id = register(client, spec)["id"]
    write(spec, spec.read_text().replace("| 3 | Third", "| 3 | Third, edited"))

    def edited():
        return by_number(client.get(f"/api/plans/{plan_id}").json())["3"]["title"] == (
            "Third, edited"
        )

    wait_for(edited)


def test_watcher_flags_missing_then_recovers(client, repo):
    spec = repo / "spec.md"
    write(spec, PLAN)
    plan_id = register(client, spec)["id"]
    text = spec.read_text()
    spec.unlink()
    wait_for(lambda: client.get(f"/api/plans/{plan_id}").json()["missing"])
    plan = client.get(f"/api/plans/{plan_id}").json()
    assert len(by_number(plan)) == 3
    write(spec, text)
    wait_for(lambda: not client.get(f"/api/plans/{plan_id}").json()["missing"])


def test_watcher_skips_archived(client, repo):
    spec = repo / "spec.md"
    write(spec, PLAN)
    plan_id = register(client, spec)["id"]
    r = client.patch(f"/api/plans/{plan_id}", json={"archived": True})
    assert r.status_code == 200 and r.json()["archived"] is True
    spec.unlink()
    time.sleep(0.3)
    assert client.get(f"/api/plans/{plan_id}").json()["missing"] is False
    assert client.patch(f"/api/plans/{plan_id}", json={"archived": "y"}).status_code == 422
    assert client.patch("/api/plans/nope", json={"archived": True}).status_code == 404


def test_refresh_ignores_stale_write(db_path, repo, monkeypatch):
    """A file edited between parse and id write-back leaves the DB untouched."""
    conn = db.connect(db_path)
    spec = repo / "spec.md"
    write(spec, PLAN)

    def stale(*args):
        raise parser.StaleFile(str(spec))

    monkeypatch.setattr(parser, "write_back", stale)
    with pytest.raises(parser.StaleFile):
        ingest.ingest(conn, spec)
    assert conn.execute("SELECT count(*) FROM plans").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM prs").fetchone()[0] == 0


def test_lookup_pr_by_path_and_number(client, repo):
    spec = repo / "spec.md"
    write(spec, PLAN)
    prs = by_number(register(client, spec))
    r = client.get("/api/prs", params={"path": str(spec), "number": "2"})
    assert r.status_code == 200
    assert r.json()["id"] == prs["2"]["id"]
    assert re.fullmatch(r"[0-9a-f]{8}", r.json()["id"])
    assert client.get("/api/prs", params={"path": str(spec), "number": "9"}).status_code == 404
    assert client.get("/api/prs", params={"path": str(spec)}).status_code == 422
