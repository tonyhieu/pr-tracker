import asyncio
import json
import socket
import threading
import time

import httpx2 as httpx
import pytest
import uvicorn

from prt.app import create_app
from prt.events import Broker
from tests.conftest import seed


@pytest.fixture
def server(db_path):
    """A real server: TestClient buffers whole responses, so it cannot read an SSE stream."""
    seed(db_path, {"pr1": "TBD"})
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(create_app(db_path), port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.01)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(5)


def test_sse_client_receives_plan_id_after_patch(server):
    with httpx.Client(base_url=server, timeout=5) as client:
        with client.stream("GET", "/api/events") as stream:
            assert stream.headers["content-type"].startswith("text/event-stream")
            lines = stream.iter_lines()
            assert next(lines) == ": connected"
            r = client.patch("/api/prs/pr1", json={"status": "Implementing", "source": "agent"})
            assert r.status_code == 200
            data = next(line for line in lines if line.startswith("data: "))
            assert json.loads(data.removeprefix("data: ")) == {"plan_id": "p1"}
            r = client.patch("/api/prs/pr1", json={"url": "u", "source": "human"})
            assert r.status_code == 200
            data = next(line for line in lines if line.startswith("data: "))
            assert json.loads(data.removeprefix("data: ")) == {"plan_id": "p1"}


def test_broker_keepalive_publish_and_unsubscribe():
    async def run():
        broker = Broker()
        stream = broker.stream(keepalive=0.05)
        assert await anext(stream) == ": connected\n\n"
        assert await anext(stream) == ": keepalive\n\n"
        broker.publish("p1")
        assert await anext(stream) == 'data: {"plan_id": "p1"}\n\n'
        await stream.aclose()
        assert broker.subscribers == set()

    asyncio.run(run())
