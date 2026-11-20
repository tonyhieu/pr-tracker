"""Sync PR status from GitHub by polling `gh pr list`."""

import asyncio
import json
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path

from prt import db, ingest, states

log = logging.getLogger(__name__)

SYNC_SECONDS = 120.0
GH_STATUS = {"OPEN": "In-review", "MERGED": "Merged", "CLOSED": "Abandoned"}


async def fetch(gh_repo: str) -> list[dict] | None:
    """The repo's 100 most recent PRs from `gh`, or None on failure."""
    out = await ingest.run(
        "gh", "pr", "list", "--repo", gh_repo, "--state", "all", "--limit", "100",
        "--json", "number,url,state,headRefName",
        cwd=Path.home(),
    )  # fmt: skip
    if out is None:
        return None
    try:
        prs = json.loads(out)
    except ValueError:
        return None
    return prs if isinstance(prs, list) else None


def _steps(current: str, target: str) -> list[str]:
    """Statuses to apply to reach `target`; chains through In-review when Merged is not direct."""
    if target == current or target in states.allowed(current):
        return [target]
    if target == "Merged" and "In-review" in states.allowed(current):
        return ["In-review", "Merged"]
    return []


def apply(conn: sqlite3.Connection, gh_repo: str, gh_prs: list[dict]) -> set[str]:
    """Apply GitHub's PR states to tracked PRs of `gh_repo`; returns changed plan ids."""
    by_url = {p["url"]: p for p in gh_prs}
    by_branch: dict[str, dict] = {}
    for p in gh_prs:  # `gh` lists newest first, so the first match is the most recent
        by_branch.setdefault(p["headRefName"], p)

    rows = conn.execute(
        "SELECT prs.* FROM prs JOIN plans ON plans.id = prs.plan_id"
        " WHERE plans.gh_repo = ? AND prs.removed = 0"
        " AND (prs.url IS NOT NULL OR prs.branch IS NOT NULL)",
        (gh_repo,),
    ).fetchall()
    changed = set()
    for pr in map(db.pr_dict, rows):
        gh = by_url.get(pr["url"]) if pr["url"] else by_branch.get(pr["branch"])
        if gh is None or gh["state"] not in GH_STATUS:
            continue
        fill_url = not pr["url"]
        if gh["state"] == pr["gh_state"] and not fill_url:
            continue
        with conn:
            if fill_url:
                conn.execute(
                    "UPDATE prs SET url = ?, updated_at = ? WHERE id = ?",
                    (gh["url"], db.now(), pr["id"]),
                )
            # Transition only when GitHub's state changed, so human overrides stick.
            if gh["state"] != pr["gh_state"]:
                steps = _steps(pr["status"], GH_STATUS[gh["state"]])
                if not steps:
                    log.info(
                        "%s: GitHub %s does not apply to %s", pr["id"], gh["state"], pr["status"]
                    )
                for to in steps:
                    states.transition(conn, pr, to, "github")
                # Record GitHub's state even when no transition applies, so it is not retried.
                conn.execute("UPDATE prs SET gh_state = ? WHERE id = ?", (gh["state"], pr["id"]))
        changed.add(pr["plan_id"])
    return changed


async def sync(conn: sqlite3.Connection, publish: Callable[[str], None]) -> dict:
    """One sync cycle over repos with PRs in Implementing or In-review."""
    repos = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT plans.gh_repo FROM plans JOIN prs ON prs.plan_id = plans.id"
            " WHERE plans.gh_repo IS NOT NULL AND prs.removed = 0"
            " AND prs.status IN ('Implementing', 'In-review') ORDER BY plans.gh_repo"
        )
    ]
    changed: set[str] = set()
    failed = []
    for repo in repos:
        gh_prs = await fetch(repo)
        if gh_prs is None:
            log.warning("gh pr list failed for %s; skipping this cycle", repo)
            failed.append(repo)
            continue
        changed |= apply(conn, repo, gh_prs)
    for plan_id in sorted(changed):
        publish(plan_id)
    return {"changed": sorted(changed), "failed": failed}


async def poll(
    conn: sqlite3.Connection, publish: Callable[[str], None], interval: float = SYNC_SECONDS
) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await sync(conn, publish)
        except Exception:
            log.exception("GitHub sync cycle failed")
