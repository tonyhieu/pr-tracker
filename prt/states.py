"""PR status state machine. The only place transitions are defined."""

import sqlite3

from prt.db import now

STATES = ("TBD", "Implementing", "In-review", "Merged", "Abandoned")
SOURCES = ("human", "agent", "github")

TRANSITIONS: dict[str, tuple[str, ...]] = {
    "TBD": ("Implementing", "In-review", "Abandoned"),
    "Implementing": ("TBD", "In-review", "Abandoned"),
    "In-review": ("Implementing", "Merged", "Abandoned"),
    "Merged": (),
    "Abandoned": ("TBD", "In-review"),
}


class InvalidTransition(Exception):
    def __init__(self, current: str, to: str):
        self.current = current
        self.to = to
        self.allowed = list(TRANSITIONS[current])
        super().__init__(f"cannot move from {current} to {to}")


def allowed(status: str) -> list[str]:
    return list(TRANSITIONS[status])


def transition(
    conn: sqlite3.Connection,
    pr: dict,
    to: str,
    source: str,
    force: bool = False,
    actor: str | None = None,
) -> bool:
    """Move `pr` to `to` and record an event; returns False for a same-status no-op.

    Does not commit, so callers can group it with other writes.
    """
    # Routes validate these first; the checks guard other callers (e.g. the GitHub poller).
    if to not in STATES:
        raise ValueError(f"unknown status {to!r}")
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}")
    if force and source != "human":
        raise ValueError("force is only allowed with source human")
    current = pr["status"]
    if to == current:
        return False
    if not force and to not in TRANSITIONS[current]:
        raise InvalidTransition(current, to)
    ts = now()
    conn.execute("UPDATE prs SET status = ?, updated_at = ? WHERE id = ?", (to, ts, pr["id"]))
    conn.execute(
        "INSERT INTO events (pr_id, from_status, to_status, source, actor, forced, at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (pr["id"], current, to, source, actor, int(force), ts),
    )
    pr["status"] = to
    pr["updated_at"] = ts
    return True
