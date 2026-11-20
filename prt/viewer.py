"""Viewer-only app for the tailnet: read routes plus status changes, nothing else."""

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from prt import states
from prt.app import STATIC_DIR, error, find_pr, get_plan, get_pr, json_object, list_plans, sse

ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


async def get_mode(request: Request) -> JSONResponse:
    return JSONResponse({"mode": "viewer"})


async def patch_status(request: Request) -> JSONResponse:
    conn = request.app.state.db
    pr = find_pr(conn, request.path_params["id"])
    if pr is None:
        return error(404, "pr not found")
    body = await json_object(request)
    if isinstance(body, JSONResponse):
        return body
    unknown = set(body) - {"status", "force"}
    if unknown:
        return error(422, f"unknown fields: {', '.join(sorted(unknown))}")
    status, force = body.get("status"), body.get("force", False)
    if status not in states.STATES:
        return error(422, f"status must be one of {', '.join(states.STATES)}")
    if not isinstance(force, bool):
        return error(422, "force must be a boolean")
    actor = request.headers.get("tailscale-user-login")
    try:
        with conn:
            changed = states.transition(conn, pr, status, "human", force, actor)
    except states.InvalidTransition as e:
        return error(409, str(e), status=e.current, allowed=e.allowed)
    if changed:
        request.app.state.broker.publish(pr["plan_id"])
    return JSONResponse(find_pr(conn, pr["id"]))


async def not_found(request: Request) -> JSONResponse:
    return error(404, "not found")


def create_viewer(app: Starlette) -> Starlette:
    """Serve `app`'s DB and SSE broker; `app`'s lifespan must be running before requests."""
    routes = [
        Route("/api/mode", get_mode),
        Route("/api/plans", list_plans),
        Route("/api/plans/{id}", get_plan),
        Route("/api/prs/{id}", get_pr),
        Route("/api/prs/{id}", patch_status, methods=["PATCH"]),
        Route("/api/events", sse),
        # Without this, unmounted /api routes fall through to StaticFiles (405 or 404).
        Route("/api/{rest:path}", not_found, methods=ALL_METHODS),
        Mount("/", StaticFiles(directory=STATIC_DIR, html=True)),
    ]
    viewer = Starlette(routes=routes)
    # Share the State object, so handlers reading request.app.state see the full app's DB.
    viewer.state = app.state
    return viewer
