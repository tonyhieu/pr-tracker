import pytest

from prt.states import STATES, TRANSITIONS


def test_table_covers_every_state():
    assert set(TRANSITIONS) == set(STATES)
    assert TRANSITIONS["Merged"] == ()


ALLOWED = [(a, b) for a in STATES for b in TRANSITIONS[a]]
REJECTED = [(a, b) for a in STATES for b in STATES if a != b and b not in TRANSITIONS[a]]


@pytest.mark.parametrize(("start", "to"), ALLOWED)
def test_allowed_transition(make_client, start, to):
    c = make_client({"pr1": start})
    r = c.patch("/api/prs/pr1", json={"status": to, "source": "agent"})
    assert r.status_code == 200
    assert r.json()["status"] == to
    events = c.get("/api/prs/pr1").json()["events"]
    assert [(e["from_status"], e["to_status"], e["source"], e["forced"]) for e in events] == [
        (start, to, "agent", 0)
    ]


@pytest.mark.parametrize(("start", "to"), REJECTED)
def test_rejected_transition(make_client, start, to):
    c = make_client({"pr1": start})
    r = c.patch("/api/prs/pr1", json={"status": to, "source": "agent"})
    assert r.status_code == 409
    assert r.json()["status"] == start
    assert r.json()["allowed"] == list(TRANSITIONS[start])
    pr = c.get("/api/prs/pr1").json()
    assert pr["status"] == start
    assert pr["events"] == []


@pytest.mark.parametrize("status", STATES)
def test_same_status_is_noop(make_client, status):
    c = make_client({"pr1": status})
    r = c.patch("/api/prs/pr1", json={"status": status, "source": "github"})
    assert r.status_code == 200
    assert r.json()["status"] == status
    assert c.get("/api/prs/pr1").json()["events"] == []


def test_force_with_human(make_client):
    c = make_client({"pr1": "Merged"})
    r = c.patch("/api/prs/pr1", json={"status": "TBD", "source": "human", "force": True})
    assert r.status_code == 200
    assert r.json()["status"] == "TBD"
    [event] = c.get("/api/prs/pr1").json()["events"]
    assert (event["from_status"], event["to_status"], event["forced"]) == ("Merged", "TBD", 1)


@pytest.mark.parametrize("source", ["agent", "github"])
def test_force_rejected_without_human(make_client, source):
    c = make_client({"pr1": "Merged"})
    r = c.patch("/api/prs/pr1", json={"status": "TBD", "source": source, "force": True})
    assert r.status_code == 422
    pr = c.get("/api/prs/pr1").json()
    assert pr["status"] == "Merged"
    assert pr["events"] == []
