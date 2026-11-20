"""HTTP routes."""

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from prt import db, states
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


async def get_plan(request: Request) -> JSONResponse:
    conn = request.app.state.db
    row = conn.execute("SELECT * FROM plans WHERE id = ?", (request.path_params["id"],)).fetchone()
    if row is None:
        return error(404, "plan not found")
    plan, prs = plan_summary(conn, row)
    plan["prs"] = prs
    return JSONResponse(plan)


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
    try:
        body = await request.json()
    except ValueError:
        return error(422, "body must be JSON")
    if not isinstance(body, dict):
        return error(422, "body must be a JSON object")
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


async def sse(request: Request) -> StreamingResponse:
    return StreamingResponse(
        request.app.state.broker.stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


def create_app(db_path: Path | str = db.DEFAULT_DB_PATH) -> Starlette:
    @asynccontextmanager
    async def lifespan(app: Starlette):
        app.state.db = db.connect(db_path)
        app.state.broker = Broker()
        try:
            yield
        finally:
            app.state.db.close()

    # StaticFiles fails on requests if the directory is missing, e.g. before `make build`.
    STATIC_DIR.mkdir(exist_ok=True)
    routes = [
        Route("/api/plans", list_plans),
        Route("/api/plans/{id}", get_plan),
        Route("/api/prs/{id}", get_pr),
        Route("/api/prs/{id}", patch_pr, methods=["PATCH"]),
        Route("/api/events", sse),
        Mount("/", StaticFiles(directory=STATIC_DIR, html=True)),
    ]
    return Starlette(routes=routes, lifespan=lifespan)
