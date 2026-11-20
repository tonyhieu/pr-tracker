import json
import os
import time

import pytest
from starlette.testclient import TestClient

from prt import db
from prt.app import create_app

URL = "https://github.com/o/r/pull/12"

# Fake `gh`: prints $GH_DIR/<owner>_<name>.json for the --repo argument, or fails if missing.
FAKE_GH = """#!/bin/sh
while [ $# -gt 0 ]; do
  if [ "$1" = --repo ]; then repo=$(echo "$2" | tr / _); fi
  shift
done
f="$GH_DIR/$repo.json"
[ -f "$f" ] || { echo "no such repo" >&2; exit 1; }
cat "$f"
"""


@pytest.fixture
def gh_dir(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    out = tmp_path / "gh"
    out.mkdir()
    monkeypatch.setenv("GH_DIR", str(out))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return out


def gh_says(gh_dir, repo, prs):
    (gh_dir / f"{repo.replace('/', '_')}.json").write_text(json.dumps(prs))


def gh_pr(state, url=URL, branch="feat"):
    return {"number": int(url.rsplit("/", 1)[1]), "url": url, "state": state, "headRefName": branch}


@pytest.fixture
def client(db_path):
    with TestClient(create_app(db_path)) as c:
        yield c


def seed(db_path, plan_id, gh_repo, prs):
    """Insert a plan with PRs given as (id, status, url, branch)."""
    conn = db.connect(db_path)
    with conn:
        conn.execute(
            "INSERT INTO plans (id, path, gh_repo, title, created_at) VALUES (?, ?, ?, 'T', ?)",
            (plan_id, f"/x/{plan_id}.md", gh_repo, db.now()),
        )
        for pos, (pr_id, status, url, branch) in enumerate(prs):
            conn.execute(
                "INSERT INTO prs (id, plan_id, number, title, position, status, url, branch)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (pr_id, plan_id, str(pos + 1), f"PR {pos + 1}", pos, status, url, branch),
            )
    conn.close()


def sync(client):
    r = client.post("/api/sync")
    assert r.status_code == 200, r.text
    return r.json()


def pr(client, pr_id):
    return client.get(f"/api/prs/{pr_id}").json()


def moves(p):
    return [(e["from_status"], e["to_status"], e["source"]) for e in p["events"]]


def test_open_moves_implementing_to_in_review_and_fills_url(db_path, gh_dir, client):
    seed(db_path, "p1", "o/r", [("aaaa0001", "Implementing", None, "feat")])
    gh_says(gh_dir, "o/r", [gh_pr("OPEN")])
    assert sync(client) == {"changed": ["p1"], "failed": []}
    p = pr(client, "aaaa0001")
    assert (p["status"], p["url"], p["gh_state"]) == ("In-review", URL, "OPEN")
    assert moves(p) == [("Implementing", "In-review", "github")]


def test_merged_from_implementing_records_both_events(db_path, gh_dir, client):
    seed(db_path, "p1", "o/r", [("aaaa0001", "Implementing", URL, None)])
    gh_says(gh_dir, "o/r", [gh_pr("MERGED")])
    sync(client)
    p = pr(client, "aaaa0001")
    assert p["status"] == "Merged"
    assert moves(p) == [
        ("Implementing", "In-review", "github"),
        ("In-review", "Merged", "github"),
    ]


def test_human_abandoned_survives_while_github_stays_open(db_path, gh_dir, client):
    # A second PR keeps the repo in the sync set after the first is Abandoned.
    seed(
        db_path,
        "p1",
        "o/r",
        [("aaaa0001", "In-review", URL, "feat"), ("aaaa0002", "Implementing", None, "other")],
    )
    gh_says(gh_dir, "o/r", [gh_pr("OPEN")])
    sync(client)
    r = client.patch("/api/prs/aaaa0001", json={"status": "Abandoned", "source": "human"})
    assert r.status_code == 200
    assert sync(client)["changed"] == []
    assert pr(client, "aaaa0001")["status"] == "Abandoned"

    # Once GitHub changes, the poller applies it again.
    gh_says(gh_dir, "o/r", [gh_pr("MERGED")])
    sync(client)
    assert pr(client, "aaaa0001")["status"] == "Merged"


def test_gh_failure_skips_the_repo(db_path, gh_dir, client):
    seed(db_path, "p1", "o/broken", [("aaaa0001", "Implementing", URL, None)])
    seed(db_path, "p2", "o/r", [("bbbb0001", "Implementing", URL, None)])
    gh_says(gh_dir, "o/r", [gh_pr("OPEN")])
    assert sync(client) == {"changed": ["p2"], "failed": ["o/broken"]}
    assert pr(client, "aaaa0001")["status"] == "Implementing"
    assert pr(client, "bbbb0001")["status"] == "In-review"


def test_branch_match_takes_most_recent(db_path, gh_dir, client):
    new = "https://github.com/o/r/pull/13"
    seed(db_path, "p1", "o/r", [("aaaa0001", "Implementing", None, "feat")])
    gh_says(gh_dir, "o/r", [gh_pr("OPEN", url=new), gh_pr("CLOSED")])
    sync(client)
    p = pr(client, "aaaa0001")
    assert (p["status"], p["url"]) == ("In-review", new)


def test_repos_without_active_prs_are_not_polled(db_path, gh_dir, client):
    seed(db_path, "p1", "o/r", [("aaaa0001", "TBD", None, "feat")])
    gh_says(gh_dir, "o/r", [gh_pr("OPEN")])
    assert sync(client) == {"changed": [], "failed": []}
    assert pr(client, "aaaa0001")["status"] == "TBD"


def test_sync_publishes_changed_plan(db_path, gh_dir, client):
    seed(db_path, "p1", "o/r", [("aaaa0001", "Implementing", URL, None)])
    gh_says(gh_dir, "o/r", [gh_pr("OPEN")])
    published = []
    client.app.state.broker.publish = published.append
    sync(client)
    assert published == ["p1"]


def test_poller_syncs_in_background(db_path, gh_dir):
    seed(db_path, "p1", "o/r", [("aaaa0001", "Implementing", URL, None)])
    gh_says(gh_dir, "o/r", [gh_pr("OPEN")])
    with TestClient(create_app(db_path, sync_interval=0.05)) as c:
        deadline = time.monotonic() + 2
        while pr(c, "aaaa0001")["status"] != "In-review":
            assert time.monotonic() < deadline, "poller did not sync"
            time.sleep(0.02)


def test_closed_moves_in_review_to_abandoned(db_path, gh_dir, client):
    seed(db_path, "p1", "o/r", [("aaaa0001", "In-review", URL, None)])
    gh_says(gh_dir, "o/r", [gh_pr("CLOSED")])
    sync(client)
    p = pr(client, "aaaa0001")
    assert (p["status"], p["gh_state"]) == ("Abandoned", "CLOSED")
    assert moves(p) == [("In-review", "Abandoned", "github")]


def test_state_with_no_transition_only_records_gh_state(db_path, gh_dir, client):
    # The Implementing PR keeps the repo in the sync set; Merged cannot move to Abandoned.
    seed(
        db_path,
        "p1",
        "o/r",
        [("aaaa0001", "Merged", URL, None), ("aaaa0002", "Implementing", None, "other")],
    )
    gh_says(gh_dir, "o/r", [gh_pr("CLOSED")])
    sync(client)
    p = pr(client, "aaaa0001")
    assert (p["status"], p["gh_state"], p["events"]) == ("Merged", "CLOSED", [])
