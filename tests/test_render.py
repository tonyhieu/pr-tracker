import asyncio
import time

import pytest
from starlette.testclient import TestClient

from prt import db, render
from prt.app import create_app
from tests.test_ingest import PLAN, register, wait_for, write

FAIL_MTH = '#!/bin/sh\necho "pandoc: boom" >&2\ntouch "$(dirname "$0")/ran"\nexit 1\n'


@pytest.fixture
def specs(tmp_path, monkeypatch):
    """A project directory under a fake `~/GitHub` root."""
    proj = tmp_path / "GitHub" / "proj"
    proj.mkdir(parents=True)
    monkeypatch.setenv("PRT_ROOT", str(tmp_path / "GitHub"))
    return proj


@pytest.fixture
def client(db_path):
    with TestClient(create_app(db_path, watch_interval=0.05)) as c:
        yield c


def spec_url(client, plan_id):
    return client.get(f"/api/plans/{plan_id}").json()["spec_url"]


def test_register_renders_and_serves_html(client, specs, db_path):
    spec = specs / "spec.md"
    write(spec, PLAN)
    plan_id = register(client, spec)["id"]
    wait_for(lambda: spec_url(client, plan_id))
    url = spec_url(client, plan_id)
    html = db_path.parent / "html" / f"{plan_id}.html"
    assert url == f"/api/plans/{plan_id}/spec?v={html.stat().st_mtime_ns}"
    r = client.get(url)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["content-security-policy"].startswith("sandbox ")
    # Rendered from the spec's directory, after the id write-back.
    cwd, body = r.text.split("\n", 1)
    assert cwd == str(specs.resolve())
    assert body == spec.read_text() and "<!-- prt:" in body


def test_nothing_is_written_next_to_the_spec(client, specs, db_path):
    spec = specs / "spec.md"
    write(spec, PLAN)
    plan_id = register(client, spec)["id"]
    wait_for(lambda: spec_url(client, plan_id))
    assert [p.name for p in specs.iterdir()] == ["spec.md"]
    assert [p.name for p in (db_path.parent / "html").iterdir()] == [f"{plan_id}.html"]


def test_spec_404s_before_first_render(client, specs, fake_mth):
    fake_mth.write_text(FAIL_MTH)
    spec = specs / "spec.md"
    write(spec, PLAN)
    plan = register(client, spec)
    assert plan["spec_url"] is None
    wait_for((fake_mth.parent / "ran").exists)
    assert spec_url(client, plan["id"]) is None
    assert client.get(f"/api/plans/{plan['id']}/spec").status_code == 404
    assert client.get("/api/plans/nope/spec").status_code == 404


def test_editing_the_file_changes_spec_url(client, specs):
    spec = specs / "spec.md"
    write(spec, PLAN)
    plan_id = register(client, spec)["id"]
    wait_for(lambda: spec_url(client, plan_id))
    before = spec_url(client, plan_id)
    write(spec, spec.read_text() + "\nMore notes.\n")
    wait_for(lambda: spec_url(client, plan_id) != before)
    assert "More notes." in client.get(spec_url(client, plan_id)).text


def test_failed_render_keeps_previous_html(client, specs, fake_mth, db_path):
    spec = specs / "spec.md"
    write(spec, PLAN)
    plan_id = register(client, spec)["id"]
    wait_for(lambda: spec_url(client, plan_id))
    before = (spec_url(client, plan_id), client.get(spec_url(client, plan_id)).text)
    fake_mth.write_text(FAIL_MTH)
    write(spec, spec.read_text() + "\nMore notes.\n")
    wait_for((fake_mth.parent / "ran").exists)
    time.sleep(0.1)
    assert (spec_url(client, plan_id), client.get(spec_url(client, plan_id)).text) == before
    assert [p.name for p in (db_path.parent / "html").iterdir()] == [f"{plan_id}.html"]


def test_startup_renders_plans_without_html(specs, db_path):
    spec = specs / "spec.md"
    write(spec, PLAN)
    with TestClient(create_app(db_path)) as c:
        plan_id = register(c, spec)["id"]
        wait_for(lambda: spec_url(c, plan_id))
    (db_path.parent / "html" / f"{plan_id}.html").unlink()
    with TestClient(create_app(db_path)) as c:
        wait_for(lambda: spec_url(c, plan_id))


def test_render_failures_return_false(tmp_path, fake_mth, monkeypatch):
    spec = tmp_path / "spec.md"
    spec.write_text("# Hi\n")
    out = tmp_path / "html" / "p1.html"
    out.parent.mkdir()
    out.write_text("old")
    fake_mth.write_text(FAIL_MTH)
    assert asyncio.run(render.render(spec, out)) is False
    fake_mth.write_text("#!/bin/sh\nsleep 5\n")
    assert asyncio.run(render.render(spec, out, timeout=0.1)) is False
    fake_mth.write_text("#!/bin/sh\nexit 0\n")  # exits 0 without writing HTML
    assert asyncio.run(render.render(spec, out)) is False
    monkeypatch.setenv("PATH", "/nonexistent")
    assert asyncio.run(render.render(spec, out)) is False
    assert asyncio.run(render.render(tmp_path / "gone.md", out)) is False
    assert out.read_text() == "old"
    assert [p.name for p in out.parent.iterdir()] == ["p1.html"]


def test_renderer_publishes_after_success_only(tmp_path, fake_mth):
    conn = db.connect(":memory:")
    spec = tmp_path / "spec.md"
    spec.write_text("# Hi\n")
    with conn:
        for plan_id, missing in (("p1", 0), ("p2", 1)):
            conn.execute(
                "INSERT INTO plans (id, path, missing) VALUES (?, ?, ?)",
                (plan_id, str(spec) if plan_id == "p1" else str(tmp_path / "gone.md"), missing),
            )
    published = []
    renderer = render.Renderer(conn, tmp_path / "html", published.append)
    asyncio.run(renderer.run("p1"))
    asyncio.run(renderer.run("p2"))  # missing file: skipped
    assert published == ["p1"]
    fake_mth.write_text(FAIL_MTH)
    asyncio.run(renderer.run("p1"))
    assert published == ["p1"]


def test_new_render_cancels_an_older_one(tmp_path, fake_mth):
    # Renders of a spec containing "slow" hang, so the older render would finish last.
    fake_mth.write_text('#!/bin/sh\ngrep -q slow "$2" && sleep 5\ncat "$2" > "${2%.md}.html"\n')
    conn = db.connect(":memory:")
    spec = tmp_path / "spec.md"
    spec.write_text("slow\n")
    with conn:
        conn.execute("INSERT INTO plans (id, path) VALUES ('p1', ?)", (str(spec),))
    published = []
    renderer = render.Renderer(conn, tmp_path / "html", published.append)

    async def go():
        renderer.schedule("p1")
        await asyncio.sleep(0.2)  # the slow render is running
        spec.write_text("fast\n")
        renderer.schedule("p1")
        await asyncio.wait_for(asyncio.gather(*renderer.tasks.values()), 2)

    asyncio.run(go())
    assert (tmp_path / "html" / "p1.html").read_text() == "fast\n"
    assert published == ["p1"] and renderer.tasks == {}
