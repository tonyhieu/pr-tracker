"""Entry point: `uv run python -m prt [--seed]`."""

import asyncio
import os
import sys

import uvicorn
from starlette.applications import Starlette

from prt import db
from prt.app import create_app
from prt.viewer import create_viewer


def make_server(app: Starlette, port: int) -> uvicorn.Server:
    return uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, workers=1))


async def serve(full: uvicorn.Server, viewer: uvicorn.Server | None = None) -> None:
    """Run the full API and, if given, the viewer in the same event loop."""
    if viewer is None:
        return await full.serve()
    full_task = asyncio.create_task(full.serve())
    # The viewer uses the DB and broker that the full app's lifespan opens.
    while not full.started:
        if full_task.done():
            return await full_task
        await asyncio.sleep(0.05)
    full_task.add_done_callback(lambda _: setattr(viewer, "should_exit", True))
    try:
        # Installed last, the viewer's signal handlers run first and re-raise for the full server.
        await viewer.serve()
    finally:
        full.should_exit = True
        await full_task


def main() -> None:
    if "--seed" in sys.argv[1:]:
        conn = db.connect()
        db.seed_demo(conn)
        conn.close()
    port = int(os.environ.get("PRT_PORT", "4747"))
    viewer_port = os.environ.get("PRT_VIEWER_PORT")
    app = create_app()
    viewer = make_server(create_viewer(app), int(viewer_port)) if viewer_port else None
    asyncio.run(serve(make_server(app, port), viewer))


if __name__ == "__main__":
    main()
