"""HTTP routes."""

import asyncio
import sqlite3
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from prt import db, github, ingest, parser, render, states
from prt.events import Broker

STATIC_DIR = Path(__file__).parent / "static"


def error(code: int, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": message, **extra}, status_code=code)


def load_prs(conn: sqlite3.Connection, plan_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM prs WHERE plan_id = ? ORDER BY position", (plan_id,)
    ).fetchall()
    prs = [db.pr_dict(r) for r in rows]
    status_by_id = {p["id"]: p["status"] for p in prs}
    for p in prs:
        p["allowed"] = states.allowed(p["status"])
        p["ready"] = p["status"] == "TBD" and all(
            status_by_id.get(d) == "Merged" for d in p["depends_on"]
        )
    return prs


def plan_summary(conn: sqlite3.Connection, row: sqlite3.Row) -> tuple[dict, list[dict]]:
    plan = db.plan_dict(row)
    prs = load_prs(conn, plan["id"])
    active = [p for p in prs if not p["removed"]]
    plan["counts"] = {s: sum(p["status"] == s for p in active) for s in states.STATES}
    plan["done"] = bool(active) and all(p["status"] in ("Merged", "Abandoned") for p in active)
    return plan, prs


def find_pr(conn: sqlite3.Connection, pr_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM prs WHERE id = ?", (pr_id,)).fetchone()
    if row is None:
        return None
    # Recompute derived fields from the whole plan so `ready` sees dependency statuses.
    return next(p for p in load_prs(conn, row["plan_id"]) if p["id"] == pr_id)


async def list_plans(request: Request) -> JSONResponse:
    conn = request.app.state.db
    rows = conn.execute("SELECT * FROM plans ORDER BY created_at, id").fetchall()
    return JSONResponse([plan_summary(conn, r)[0] for r in rows])


def plan_detail(conn: sqlite3.Connection, html_dir: Path, plan_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    if row is None:
        return None
    plan, prs = plan_summary(conn, row)
    plan["prs"] = prs
    plan["spec_url"] = render.spec_url(html_dir, plan_id)
    return plan


async def json_object(request: Request) -> dict | JSONResponse:
    """The request body as a dict, or a 422 response."""
    try:
        body = await request.json()
    except ValueError:
        return error(422, "body must be JSON")
    if not isinstance(body, dict):
        return error(422, "body must be a JSON object")
    return body


async def get_plan(request: Request) -> JSONResponse:
    state = request.app.state
    plan = plan_detail(state.db, state.html_dir, request.path_params["id"])
    if plan is None:
        return error(404, "plan not found")
    return JSONResponse(plan)


async def get_spec(request: Request) -> Response:
    state = request.app.state
    plan_id = request.path_params["id"]
    if state.db.execute("SELECT 1 FROM plans WHERE id = ?", (plan_id,)).fetchone() is None:
        return error(404, "plan not found")
    path = render.html_path(state.html_dir, plan_id)
    if not path.is_file():
        return error(404, "plan has not rendered yet")
    # Spec HTML can carry scripts; sandbox it to an opaque origin so they cannot reach the app.
    csp = {"Content-Security-Policy": "sandbox allow-scripts allow-popups"}
    return FileResponse(path, media_type="text/html", headers=csp)


async def register_plan(request: Request) -> JSONResponse:
    conn = request.app.state.db
    body = await json_object(request)
    if isinstance(body, JSONResponse):
        return body
    if set(body) != {"path"} or not isinstance(body["path"], str):
        return error(422, "body must be {path: string}")
    try:
        path = ingest.resolve(body["path"])
    except ingest.OutsideRoot as e:
        return error(403, str(e))
    if not path.is_file():
        return error(404, f"{path} is not a file")
    repo = await ingest.repo_info(path)
    try:
        plan_id = ingest.ingest(conn, path, repo)
    except FileNotFoundError:
        # Deleted after the is_file() check, e.g. during repo_info.
        return error(404, f"{path} is not a file")
    except (ingest.NotAPlan, UnicodeDecodeError) as e:
        return error(422, str(e))
    except parser.StaleFile:
        return error(409, f"{path} changed while registering; retry")
    request.app.state.broker.publish(plan_id)
    request.app.state.renderer.schedule(plan_id)
    return JSONResponse(plan_detail(conn, request.app.state.html_dir, plan_id))


async def patch_plan(request: Request) -> JSONResponse:
    conn = request.app.state.db
    html_dir = request.app.state.html_dir
    plan_id = request.path_params["id"]
    if plan_detail(conn, html_dir, plan_id) is None:
        return error(404, "plan not found")
    body = await json_object(request)
    if isinstance(body, JSONResponse):
        return body
    if set(body) != {"archived"} or not isinstance(body["archived"], bool):
        return error(422, "body must be {archived: boolean}")
    with conn:
        conn.execute("UPDATE plans SET archived = ? WHERE id = ?", (int(body["archived"]), plan_id))
    request.app.state.broker.publish(plan_id)
    return JSONResponse(plan_detail(conn, html_dir, plan_id))


async def lookup_pr(request: Request) -> JSONResponse:
    conn = request.app.state.db
    path, number = request.query_params.get("path"), request.query_params.get("number")
    if not path or not number:
        return error(422, "path and number are required")
    try:
        spec = ingest.resolve(path)
    except ingest.OutsideRoot:
        return error(404, "pr not found")
    row = conn.execute(
        "SELECT prs.id FROM prs JOIN plans ON plans.id = prs.plan_id"
        " WHERE plans.path = ? AND prs.number = ? AND prs.removed = 0 ORDER BY prs.position",
        (str(spec), number),
    ).fetchone()
    if row is None:
        return error(404, "pr not found")
    return JSONResponse(find_pr(conn, row["id"]))


async def get_pr(request: Request) -> JSONResponse:
    conn = request.app.state.db
    pr = find_pr(conn, request.path_params["id"])
    if pr is None:
        return error(404, "pr not found")
    pr["events"] = [
        dict(r)
        for r in conn.execute("SELECT * FROM events WHERE pr_id = ? ORDER BY id", (pr["id"],))
    ]
    return JSONResponse(pr)


async def patch_pr(request: Request) -> JSONResponse:
    conn = request.app.state.db
    pr = find_pr(conn, request.path_params["id"])
    if pr is None:
        return error(404, "pr not found")
    body = await json_object(request)
    if isinstance(body, JSONResponse):
        return body
    unknown = set(body) - {"status", "url", "branch", "source", "force"}
    if unknown:
        return error(422, f"unknown fields: {', '.join(sorted(unknown))}")
    source = body.get("source")
    status = body.get("status")
    force = body.get("force", False)
    if source not in states.SOURCES:
        return error(422, f"source must be one of {', '.join(states.SOURCES)}")
    if status is not None and status not in states.STATES:
        return error(422, f"status must be one of {', '.join(states.STATES)}")
    if not isinstance(force, bool):
        return error(422, "force must be a boolean")
    if force and source != "human":
        return error(422, "force is only allowed with source human")
    for field in ("url", "branch"):
        if field in body and not (body[field] is None or isinstance(body[field], str)):
            return error(422, f"{field} must be a string or null")

    changed = False
    try:
        with conn:
            for field in ("url", "branch"):
                if field in body and body[field] != pr[field]:
                    conn.execute(
                        f"UPDATE prs SET {field} = ?, updated_at = ? WHERE id = ?",
                        (body[field], db.now(), pr["id"]),
                    )
                    changed = True
            if status is not None:
                changed = states.transition(conn, pr, status, source, force) or changed
    except states.InvalidTransition as e:
        return error(409, str(e), status=e.current, allowed=e.allowed)
    if changed:
        request.app.state.broker.publish(pr["plan_id"])
    return JSONResponse(find_pr(conn, pr["id"]))


async def run_sync(request: Request) -> JSONResponse:
    app = request.app
    return JSONResponse(await github.sync(app.state.db, app.state.broker.publish))


async def sse(request: Request) -> StreamingResponse:
    return StreamingResponse(
        request.app.state.broker.stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


def create_app(
    db_path: Path | str = db.DEFAULT_DB_PATH,
    watch_interval: float = ingest.WATCH_SECONDS,
    sync_interval: float = github.SYNC_SECONDS,
) -> Starlette:
    @asynccontextmanager
    async def lifespan(app: Starlette):
        app.state.db = db.connect(db_path)
        app.state.broker = Broker()
        # Rendered plans are cached beside the DB, e.g. ~/.local/share/pr-tracker/html/.
        app.state.html_dir = Path(db_path).parent / "html"
        renderer = app.state.renderer = render.Renderer(
            app.state.db, app.state.html_dir, app.state.broker.publish
        )
        renderer.schedule_unrendered()

        def on_file_change(plan_id: str) -> None:
            app.state.broker.publish(plan_id)
            renderer.schedule(plan_id)

        tasks = [
            asyncio.create_task(ingest.watch(app.state.db, on_file_change, watch_interval)),
            asyncio.create_task(github.poll(app.state.db, app.state.broker.publish, sync_interval)),
        ]
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await renderer.close()
            app.state.db.close()

    # StaticFiles fails on requests if the directory is missing, e.g. before `make build`.
    STATIC_DIR.mkdir(exist_ok=True)
    routes = [
        Route("/api/plans", list_plans),
        Route("/api/plans", register_plan, methods=["POST"]),
        Route("/api/plans/{id}", get_plan),
        Route("/api/plans/{id}", patch_plan, methods=["PATCH"]),
        Route("/api/plans/{id}/spec", get_spec),
        Route("/api/prs", lookup_pr),
        Route("/api/prs/{id}", get_pr),
        Route("/api/prs/{id}", patch_pr, methods=["PATCH"]),
        Route("/api/sync", run_sync, methods=["POST"]),
        Route("/api/events", sse),
        Mount("/", StaticFiles(directory=STATIC_DIR, html=True)),
    ]
    return Starlette(routes=routes, lifespan=lifespan)
