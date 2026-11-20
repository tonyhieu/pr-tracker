"""Render a plan's spec file to HTML with `mth` and cache it."""

import asyncio
import logging
import os
import shutil
import signal
import sqlite3
import tempfile
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

log = logging.getLogger(__name__)

RENDER_TIMEOUT = 30.0


def html_path(html_dir: Path, plan_id: str) -> Path:
    return html_dir / f"{plan_id}.html"


def spec_url(html_dir: Path, plan_id: str) -> str | None:
    """URL of the cached HTML, versioned by its mtime; None if it has never rendered."""
    try:
        version = html_path(html_dir, plan_id).stat().st_mtime_ns
    except FileNotFoundError:
        return None
    return f"/api/plans/{plan_id}/spec?v={version}"


async def render(spec: Path, out: Path, timeout: float = RENDER_TIMEOUT) -> bool:
    """Render `spec` into `out` with `mth`; on any failure returns False and leaves `out` as is."""
    out.parent.mkdir(parents=True, exist_ok=True)
    # `mth` writes <name>.html next to its input, so render a copy in a temp dir beside `out`
    # (same filesystem, so the final rename is atomic).
    with tempfile.TemporaryDirectory(dir=out.parent, prefix=".render-") as tmp:
        src = Path(tmp) / f"{out.stem}.md"
        try:
            shutil.copyfile(spec, src)
            proc = await asyncio.create_subprocess_exec(
                "mth", "-n", str(src),
                cwd=spec.parent,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,  # own process group, so a kill also stops pandoc
            )  # fmt: skip
        except OSError as e:
            log.warning("render of %s failed: %s", spec, e)
            return False
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            log.warning("render of %s timed out", spec)
            return False
        finally:
            if proc.returncode is None:  # timed out or cancelled
                with suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                await proc.communicate()
        if proc.returncode != 0:
            log.warning("render of %s failed: %s", spec, err.decode(errors="replace").strip())
            return False
        try:
            os.replace(src.with_suffix(".html"), out)
        except OSError as e:
            log.warning("render of %s produced no HTML: %s", spec, e)
            return False
    return True


class Renderer:
    """Runs renders in the background and publishes the plan id after each success."""

    def __init__(
        self, conn: sqlite3.Connection, html_dir: Path, publish: Callable[[str], None]
    ) -> None:
        self.conn = conn
        self.html_dir = html_dir
        self.publish = publish
        self.tasks: dict[str, asyncio.Task] = {}  # in-flight render per plan id

    def schedule(self, plan_id: str) -> None:
        # Cancel an older render of this plan so it cannot finish last with stale content.
        if (old := self.tasks.get(plan_id)) is not None:
            old.cancel()
        task = self.tasks[plan_id] = asyncio.create_task(self.run(plan_id))

        def done(t: asyncio.Task) -> None:
            if self.tasks.get(plan_id) is t:
                del self.tasks[plan_id]

        task.add_done_callback(done)

    async def run(self, plan_id: str) -> None:
        row = self.conn.execute(
            "SELECT path, missing FROM plans WHERE id = ?", (plan_id,)
        ).fetchone()
        if row is None or row["missing"]:
            return
        if await render(Path(row["path"]), html_path(self.html_dir, plan_id)):
            self.publish(plan_id)

    def schedule_unrendered(self) -> None:
        """Startup: render each plan that has no cached HTML yet."""
        for (plan_id,) in self.conn.execute("SELECT id FROM plans WHERE missing = 0").fetchall():
            if not html_path(self.html_dir, plan_id).exists():
                self.schedule(plan_id)

    async def close(self) -> None:
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
