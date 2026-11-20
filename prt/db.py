"""SQLite schema, connection, and row serialization."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_DB_PATH = Path.home() / ".local/share/pr-tracker/prt.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    path TEXT UNIQUE,
    repo_dir TEXT,
    gh_repo TEXT,
    title TEXT,
    notes_md TEXT,
    mtime REAL,
    missing INT NOT NULL DEFAULT 0,
    archived INT NOT NULL DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS prs (
    id TEXT PRIMARY KEY,
    plan_id TEXT,
    number TEXT,
    title TEXT,
    scope_md TEXT,
    depends_on TEXT NOT NULL DEFAULT '[]', -- JSON list of pr ids
    position INT,
    status TEXT NOT NULL DEFAULT 'TBD',
    url TEXT,
    branch TEXT,
    gh_state TEXT,
    removed INT NOT NULL DEFAULT 0,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    pr_id TEXT,
    from_status TEXT,
    to_status TEXT,
    source TEXT, -- human | agent | github
    actor TEXT, -- Tailscale login, viewer only
    forced INT NOT NULL DEFAULT 0,
    at TEXT
);
CREATE INDEX IF NOT EXISTS prs_plan_id ON prs (plan_id);
CREATE INDEX IF NOT EXISTS events_pr_id ON events (pr_id);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # One worker on one event loop, so a single shared connection is safe.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def pr_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["depends_on"] = json.loads(d["depends_on"] or "[]")
    d["removed"] = bool(d["removed"])
    return d


def plan_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["missing"] = bool(d["missing"])
    d["archived"] = bool(d["archived"])
    return d


def seed_demo(conn: sqlite3.Connection) -> None:
    """Insert a demo plan when the DB has none; used for UI work until ingestion lands."""
    if conn.execute("SELECT 1 FROM plans LIMIT 1").fetchone():
        return
    ts = now()
    with conn:
        conn.execute(
            "INSERT INTO plans (id, path, repo_dir, gh_repo, title, notes_md, mtime, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "demo",
                str(Path.home() / "GitHub/demo/spec.md"),
                str(Path.home() / "GitHub/demo"),
                "demo/demo",
                "Demo plan",
                "PR 2 runs in parallel with 1.",
                0.0,
                ts,
            ),
        )
        rows = [
            ("a1b2c3d4", "1", "Add the core", "Core `server`.", [], "Merged"),
            ("b2c3d4e5", "2", "Add the parser", "Parse tables.", [], "In-review"),
            ("c3d4e5f6", "3", "Wire them up", "Ingest plans.", ["a1b2c3d4", "b2c3d4e5"], "TBD"),
            ("d4e5f6a7", "4", "Build the UI", "React app.", ["a1b2c3d4"], "TBD"),
        ]
        for pos, (pr_id, number, title, scope, deps, status) in enumerate(rows):
            conn.execute(
                "INSERT INTO prs (id, plan_id, number, title, scope_md, depends_on, position,"
                " status, updated_at) VALUES (?, 'demo', ?, ?, ?, ?, ?, ?, ?)",
                (pr_id, number, title, scope, json.dumps(deps), pos, status, ts),
            )
