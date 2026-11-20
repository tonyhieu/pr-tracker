"""Server-Sent Events: one queue per client, fed with changed plan ids."""

import asyncio
import json
from collections.abc import AsyncIterator

KEEPALIVE_SECONDS = 15.0


class Broker:
    def __init__(self) -> None:
        self.subscribers: set[asyncio.Queue[str]] = set()

    def publish(self, plan_id: str) -> None:
        """Call after the DB write has committed."""
        for q in self.subscribers:
            q.put_nowait(plan_id)

    async def stream(self, keepalive: float = KEEPALIVE_SECONDS) -> AsyncIterator[str]:
        q: asyncio.Queue[str] = asyncio.Queue()
        self.subscribers.add(q)
        try:
            # Flush headers right away so clients see the connection open.
            yield ": connected\n\n"
            while True:
                try:
                    plan_id = await asyncio.wait_for(q.get(), keepalive)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps({'plan_id': plan_id})}\n\n"
        finally:
            self.subscribers.discard(q)
