import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from rewind import RewindStore  # noqa: E402
from rewind.api import create_app  # noqa: E402


@pytest.fixture
def client():
    store = RewindStore.open()
    with TestClient(create_app(store)) as c:
        yield c, store
    store.close()


def _setup_run(client):
    c, store = client
    run = c.post("/api/runs", json={"name": "api-run"}).json()
    branch = store.active_branch(run["id"])
    return c, store, run, branch


def test_create_and_list_runs(client):
    c, store, run, branch = _setup_run(client)
    runs = c.get("/api/runs").json()
    assert [r["id"] for r in runs] == [run["id"]]


def test_checkpoint_state_and_diff_endpoints(client):
    c, store, run, branch = _setup_run(client)
    r1 = c.post(
        f"/api/branches/{branch.id}/checkpoints",
        json={"updates": {"x": 1, "y": "keep"}, "label": "step 1"},
    )
    r2 = c.post(
        f"/api/branches/{branch.id}/checkpoints",
        json={"updates": {"x": 2}, "deletes": [], "label": "step 2"},
    )
    a, b = r1.json()["id"], r2.json()["id"]

    assert c.get(f"/api/checkpoints/{b}/state").json() == {"x": 2, "y": "keep"}
    assert c.get("/api/diff", params={"a": a, "b": b}).json() == {"x": {"a": 1, "b": 2}}


def test_tree_and_rewind_endpoints(client):
    c, store, run, branch = _setup_run(client)
    good = c.post(
        f"/api/branches/{branch.id}/checkpoints", json={"updates": {"belief": "ok"}}
    ).json()
    c.post(f"/api/branches/{branch.id}/checkpoints", json={"updates": {"belief": "poisoned"}})
    store.record_effect(branch.id, "slack.post", {"text": "oops"}, mode="staged")

    resp = c.post(
        "/api/rewind", json={"branch_id": branch.id, "to_checkpoint_id": good["id"]}
    ).json()
    assert resp["discarded_count"] == 1
    assert resp["new_branch"]["status"] == "active"
    assert resp["abandoned_branch"]["status"] == "abandoned"

    tree = c.get(f"/api/runs/{run['id']}/tree").json()
    assert len(tree["branches"]) == 2
    assert len(tree["checkpoints"]) == 3


def test_missing_resources_404(client):
    c, store, run, branch = _setup_run(client)
    assert c.get("/api/runs/nope/tree").status_code == 404
    assert c.get("/api/checkpoints/nope/state").status_code == 404


def test_checkpoint_on_abandoned_branch_409(client):
    c, store, run, branch = _setup_run(client)
    good = c.post(f"/api/branches/{branch.id}/checkpoints", json={"updates": {"x": 1}}).json()
    c.post("/api/rewind", json={"branch_id": branch.id, "to_checkpoint_id": good["id"]})
    resp = c.post(f"/api/branches/{branch.id}/checkpoints", json={"updates": {"x": 2}})
    assert resp.status_code == 409
