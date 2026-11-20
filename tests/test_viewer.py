import asyncio
import json
import socket
import sqlite3
import threading
import time

import httpx2 as httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

from prt import backup
from prt.__main__ import serve
from prt.app import create_app
from prt.viewer import create_viewer
from tests.conftest import seed

LOGIN = "me@example.com"


@pytest.fixture
def clients(db_path):
    """(full, viewer) clients over one DB; the full client runs the shared lifespan."""
    seed(db_path, {"a": "TBD", "b": "Merged"})
    with TestClient(create_app(db_path)) as full:
        with TestClient(create_viewer(full.app)) as viewer:
            yield full, viewer


def test_mode_on_both_apps(clients):
    full, viewer = clients
    assert full.get("/api/mode").json() == {"mode": "full"}
    assert viewer.get("/api/mode").json() == {"mode": "viewer"}


def test_viewer_read_routes_match_full(clients):
    full, viewer = clients
    for path in ("/api/plans", "/api/plans/p1", "/api/prs/a"):
        r = viewer.get(path)
        assert r.status_code == 200, path
        assert r.json() == full.get(path).json()
    assert viewer.get("/api/plans/nope").status_code == 404
    assert viewer.get("/api/prs/nope").status_code == 404


def test_viewer_patch_records_human_and_actor(clients):
    full, viewer = clients
    r = viewer.patch(
        "/api/prs/a", json={"status": "Implementing"}, headers={"Tailscale-User-Login": LOGIN}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "Implementing"
    [event] = full.get("/api/prs/a").json()["events"]
    assert (event["source"], event["actor"], event["forced"]) == ("human", LOGIN, 0)


def test_viewer_patch_without_login_and_forced(clients):
    full, viewer = clients
    assert viewer.patch("/api/prs/b", json={"status": "TBD"}).status_code == 409
    r = viewer.patch("/api/prs/b", json={"status": "TBD", "force": True})
    assert r.status_code == 200
    [event] = full.get("/api/prs/b").json()["events"]
    assert (event["source"], event["actor"], event["forced"]) == ("human", None, 1)


@pytest.mark.parametrize(
    "body",
    [
        {"status": "Implementing", "url": "u"},
        {"status": "Implementing", "branch": "x"},
        {"status": "Implementing", "source": "human"},
        {"url": "u"},
        {"status": "Nope"},
        {"status": "Implementing", "force": "yes"},
    ],
)
def test_viewer_patch_rejects_other_fields(clients, body):
    full, viewer = clients
    assert viewer.patch("/api/prs/a", json=body).status_code == 422
    pr = full.get("/api/prs/a").json()
    assert (pr["status"], pr["url"], pr["branch"], pr["events"]) == ("TBD", None, None, [])


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/plans"),
        ("PATCH", "/api/plans/p1"),
        ("GET", "/api/prs?path=/x/spec.md&number=1"),
        ("POST", "/api/sync"),
        ("GET", "/api/nope"),
    ],
)
def test_viewer_does_not_mount_other_routes(clients, method, path):
    _, viewer = clients
    assert viewer.request(method, path, json={}).status_code == 404


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def servers(db_path):
    """Both apps served by `prt.__main__.serve` on one event loop, like production."""
    seed(db_path, {"a": "TBD"})
    app = create_app(db_path)
    full_port, viewer_port = free_port(), free_port()
    full = uvicorn.Server(uvicorn.Config(app, port=full_port, log_level="warning"))
    viewer = uvicorn.Server(
        uvicorn.Config(create_viewer(app), port=viewer_port, log_level="warning")
    )
    thread = threading.Thread(target=asyncio.run, args=(serve(full, viewer),), daemon=True)
    thread.start()
    while not viewer.started:
        time.sleep(0.01)
    yield f"http://127.0.0.1:{full_port}", f"http://127.0.0.1:{viewer_port}"
    # Stopping the viewer must also stop the full server.
    viewer.should_exit = True
    thread.join(5)
    assert not thread.is_alive()


def next_plan_id(lines) -> str:
    data = next(line for line in lines if line.startswith("data: "))
    return json.loads(data.removeprefix("data: "))["plan_id"]


@pytest.mark.parametrize("listen,write", [(1, 0), (0, 1)])
def test_sse_crosses_apps(servers, listen, write):
    with httpx.Client(base_url=servers[listen], timeout=5) as sub:
        with httpx.Client(base_url=servers[write], timeout=5) as pub:
            with sub.stream("GET", "/api/events") as stream:
                lines = stream.iter_lines()
                assert next(lines) == ": connected"
                body = {"status": "Implementing"} | ({"source": "agent"} if write == 0 else {})
                assert pub.patch("/api/prs/a", json=body).status_code == 200
                assert next_plan_id(lines) == "p1"


def test_backup_keeps_newest_copies(db_path, tmp_path):
    seed(db_path, {"a": "TBD"})
    out = tmp_path / "backups"
    out.mkdir()
    for day in range(1, 20):
        (out / f"prt-2020-01-{day:02}.db").write_text("")
    target = backup.backup(db_path, out, keep=14)
    names = sorted(p.name for p in out.glob("prt-*.db"))
    assert len(names) == 14 and names[-1] == target.name
    assert names[0] == "prt-2020-01-07.db"
    conn = sqlite3.connect(target)
    assert conn.execute("SELECT id FROM prs").fetchall() == [("a",)]
    conn.close()


def test_backup_of_missing_db_fails(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        backup.backup(tmp_path / "nope.db", tmp_path / "backups")
    assert not (tmp_path / "nope.db").exists()
