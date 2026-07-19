"""Integration smoke test against a live CockroachDB (or Postgres) cluster.

Runs only when REWIND_DATABASE_URL is set (e.g. via .env); skipped otherwise
so the suite needs no infrastructure by default. Test data is cleaned up by
run_id at the end — the append-only rule applies to agent history, not to
test residue in a shared cluster.
"""

import os

import pytest

from rewind import RewindStore
from rewind.memory import VectorMemory
from rewind.proxy import ToolProxy
from rewind.vfs import VFS

DATABASE_URL = os.environ.get("REWIND_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith(("postgres", "cockroachdb")),
    reason="REWIND_DATABASE_URL not set to a live cluster",
)

TABLES = [
    "staged_effects",
    "agent_memory",
    "vfs_objects",
    "state_kv",
    "checkpoints",
    "branches",
    "runs",
]


@pytest.fixture(scope="module")
def store():
    s = RewindStore.open(DATABASE_URL)
    created_runs: list[str] = []
    original_create_run = s.create_run

    def tracking_create_run(name):
        run = original_create_run(name)
        created_runs.append(run.id)
        return run

    s.create_run = tracking_create_run
    yield s
    for run_id in created_runs:
        for table in TABLES:
            column = "id" if table == "runs" else "run_id"
            s.db.execute(f"DELETE FROM {table} WHERE {column} = ?", (run_id,))  # noqa: S608
    s.close()


def test_full_hero_flow_on_live_cluster(store):
    sent: list[dict] = []
    vfs = VFS(store)
    memory = VectorMemory(store)
    proxy = ToolProxy(store, executors={"slack.post": lambda p: sent.append(p) or {"ok": True}})

    run = store.create_run("integration-smoke")
    branch = store.active_branch(run.id)

    clean = store.create_checkpoint(
        branch.id,
        updates={"status": "investigating"},
        files={"/plan.md": b"check pool metrics"},
        label="clean",
    )
    memory.remember(branch.id, "db-3 connection pool at 98 percent")

    store.create_checkpoint(branch.id, updates={"status": "escalating"}, label="poisoned")
    memory.remember(branch.id, "conclusion: disk corrupt")
    proxy.call(branch.id, "slack.post", {"text": "wrong alarm"}, mode="staged")

    # State, files, and memory all resolve on the live cluster.
    head = store.get_branch(branch.id).head_checkpoint_id
    assert store.get_state(head)["status"] == "escalating"
    assert vfs.read_text(head, "/plan.md") == "check pool metrics"
    assert len(memory.memories_at(head)) == 2

    # The rewind discards the staged effect and resets state + memory.
    result = store.rewind(branch.id, clean.id, new_branch_name="recovery")
    assert len(result.discarded_effects) == 1
    new_head = result.new_branch.head_checkpoint_id
    assert store.get_state(new_head)["status"] == "investigating"
    assert [m.content for m in memory.memories_at(new_head)] == [
        "db-3 connection pool at 98 percent"
    ]
    assert proxy.flush(result.new_branch.id) == []
    assert sent == []


def test_idempotent_replay_on_live_cluster(store):
    charges: list[dict] = []
    proxy = ToolProxy(store, executors={"charge.card": lambda p: charges.append(p) or {"ok": 1}})

    run = store.create_run("integration-idem")
    branch = store.active_branch(run.id)
    clean = store.create_checkpoint(branch.id, {"step": 1})

    proxy.call(branch.id, "charge.card", {"amt": 9}, mode="idempotent", idempotency_key="c-1")
    new_branch = store.rewind(branch.id, clean.id).new_branch
    replay = proxy.call(
        new_branch.id, "charge.card", {"amt": 9}, mode="idempotent", idempotency_key="c-1"
    )

    assert replay.executed is False
    assert len(charges) == 1  # charged exactly once across the rewind


def test_vector_indexed_recall_on_live_cluster(store):
    """Semantic recall runs inside CockroachDB via the distributed vector
    index (cosine distance), and rewinding still rewinds what is recallable."""
    memory = VectorMemory(store)
    run = store.create_run("integration-vector")
    branch = store.active_branch(run.id)

    clean = store.create_checkpoint(branch.id, {"step": 1})
    memory.remember(branch.id, "db-3 connection pool is at 98 percent utilization")
    memory.remember(branch.id, "customer emailed asking about the quarterly invoice")

    store.create_checkpoint(branch.id, {"step": 2})
    memory.remember(branch.id, "conclusion: db-3 disk corruption, wipe required")

    # The DB-side ANN search must rank pool memory above billing memory.
    hits = memory._recall_indexed(
        store.get_branch(branch.id).head_checkpoint_id,
        memory.embedder("why is the database connection pool exhausted"),
        k=2,
    )
    assert hits, "vector index query returned no rows"
    assert "connection pool" in hits[0].record.content

    # After a rewind, the poisoned memory is out of ANN reach on the new branch.
    new_branch = store.rewind(branch.id, clean.id).new_branch
    contents = [
        h.record.content
        for h in memory.recall(new_branch.head_checkpoint_id, "what about db-3", k=5)
    ]
    assert all("wipe" not in c for c in contents)
    assert any("connection pool" in c for c in contents)


def test_as_of_system_time_reads(store):
    """CockroachDB MVCC time travel: read the DB exactly as it was earlier."""
    if store.db.dialect != "postgres":
        pytest.skip("AS OF SYSTEM TIME requires CockroachDB")

    run = store.create_run("integration-asof")
    branch = store.active_branch(run.id)
    store.create_checkpoint(branch.id, {"phase": "before"})
    ts = store.db.query("SELECT cluster_logical_timestamp()")[0][0]

    store.create_checkpoint(branch.id, {"phase": "after"})

    now_count = store.db.query("SELECT count(*) FROM checkpoints WHERE run_id = ?", (run.id,))[0][0]
    past_count = store.db.query(
        f"SELECT count(*) FROM checkpoints AS OF SYSTEM TIME {ts} WHERE run_id = ?",
        (run.id,),
    )[0][0]
    assert now_count == 3  # genesis + 2
    assert past_count == 2  # the third didn't exist yet at ts
