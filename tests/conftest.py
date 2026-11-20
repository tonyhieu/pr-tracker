import json

import pytest
from starlette.testclient import TestClient

from prt import db
from prt.app import create_app


def seed(path, statuses: dict[str, str], deps: dict[str, list[str]] | None = None) -> None:
    """Insert plan `p1` with one PR per id in `statuses`."""
    conn = db.connect(path)
    with conn:
        conn.execute(
            "INSERT INTO plans (id, path, title, created_at) VALUES ('p1', '/x/spec.md', 'T', ?)",
            (db.now(),),
        )
        for pos, (pr_id, status) in enumerate(statuses.items()):
            conn.execute(
                "INSERT INTO prs (id, plan_id, number, title, depends_on, position, status)"
                " VALUES (?, 'p1', ?, ?, ?, ?, ?)",
                (
                    pr_id,
                    str(pos + 1),
                    f"PR {pos + 1}",
                    json.dumps((deps or {}).get(pr_id, [])),
                    pos,
                    status,
                ),
            )
    conn.close()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "prt.db"


@pytest.fixture
def make_client(db_path):
    clients = []

    def make(statuses, deps=None):
        seed(db_path, statuses, deps)
        c = TestClient(create_app(db_path))
        c.__enter__()
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)
