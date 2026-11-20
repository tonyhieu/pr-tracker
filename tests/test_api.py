import pytest


def test_list_plans_counts_and_done(make_client):
    c = make_client({"a": "Merged", "b": "Abandoned"})
    [plan] = c.get("/api/plans").json()
    assert plan["id"] == "p1"
    assert plan["done"] is True
    assert plan["counts"]["Merged"] == 1
    assert plan["counts"]["Abandoned"] == 1
    assert plan["missing"] is False and plan["archived"] is False


def test_plan_not_done_with_open_pr(make_client):
    c = make_client({"a": "Merged", "b": "In-review"})
    assert c.get("/api/plans").json()[0]["done"] is False


def test_removed_prs_do_not_count(make_client):
    c = make_client({"a": "Merged", "b": "TBD"})
    with c.app.state.db as conn:
        conn.execute("UPDATE prs SET removed = 1 WHERE id = 'b'")
    plan = c.get("/api/plans").json()[0]
    assert plan["done"] is True
    assert plan["counts"]["TBD"] == 0


def test_get_plan_with_prs(make_client):
    c = make_client({"a": "Merged", "b": "TBD", "c": "TBD"}, deps={"b": ["a"], "c": ["a", "b"]})
    plan = c.get("/api/plans/p1").json()
    prs = {p["id"]: p for p in plan["prs"]}
    assert [p["id"] for p in plan["prs"]] == ["a", "b", "c"]
    assert prs["b"]["ready"] is True
    assert prs["c"]["ready"] is False
    assert prs["c"]["depends_on"] == ["a", "b"]
    assert prs["a"]["allowed"] == []
    assert prs["b"]["allowed"] == ["Implementing", "In-review", "Abandoned"]


def test_unknown_ids_404(make_client):
    c = make_client({"a": "TBD"})
    assert c.get("/api/plans/nope").status_code == 404
    assert c.get("/api/prs/nope").status_code == 404
    assert c.patch("/api/prs/nope", json={"source": "agent"}).status_code == 404


def test_patch_url_and_branch_with_status(make_client):
    c = make_client({"a": "Implementing"})
    r = c.patch(
        "/api/prs/a",
        json={
            "url": "https://github.com/o/r/pull/12",
            "branch": "feat",
            "status": "In-review",
            "source": "agent",
        },
    )
    assert r.status_code == 200
    pr = r.json()
    assert (pr["url"], pr["branch"], pr["status"]) == (
        "https://github.com/o/r/pull/12",
        "feat",
        "In-review",
    )


def test_patch_clears_url_with_null(make_client):
    c = make_client({"a": "TBD"})
    c.patch("/api/prs/a", json={"url": "u", "source": "human"})
    r = c.patch("/api/prs/a", json={"url": None, "source": "human"})
    assert r.status_code == 200
    assert r.json()["url"] is None


def test_invalid_transition_does_not_apply_url(make_client):
    c = make_client({"a": "Merged"})
    r = c.patch("/api/prs/a", json={"url": "u", "status": "TBD", "source": "agent"})
    assert r.status_code == 409
    assert c.get("/api/prs/a").json()["url"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"status": "In-review"},
        {"status": "In-review", "source": "robot"},
        {"status": "Done", "source": "agent"},
        {"source": "human", "force": "yes"},
        {"source": "agent", "url": 5},
        {"source": "agent", "title": "x"},
    ],
)
def test_patch_validation(make_client, body):
    c = make_client({"a": "TBD"})
    assert c.patch("/api/prs/a", json=body).status_code == 422


def test_static_mount(make_client):
    c = make_client({"a": "TBD"})
    assert c.get("/missing.js").status_code == 404
