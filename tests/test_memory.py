import pytest

from rewind import RewindStore
from rewind.memory import HashingEmbedder, VectorMemory, cosine


@pytest.fixture
def store():
    s = RewindStore.open()
    yield s
    s.close()


@pytest.fixture
def env(store):
    run = store.create_run("memory-run")
    branch = store.active_branch(run.id)
    return store, run, branch, VectorMemory(store)


def test_hashing_embedder_is_deterministic_and_normalized():
    embed = HashingEmbedder()
    a, b = embed("database latency spike"), embed("database latency spike")
    assert a == b
    assert abs(sum(x * x for x in a) - 1.0) < 1e-9


def test_related_text_scores_higher():
    embed = HashingEmbedder()
    base = embed("database connection pool exhausted")
    related = embed("the database connection pool is exhausted again")
    unrelated = embed("marketing newsletter draft for q3")
    assert cosine(base, related) > cosine(base, unrelated)


def test_remember_and_recall(env):
    store, run, branch, memory = env
    store.create_checkpoint(branch.id, {"step": 1})
    memory.remember(branch.id, "alert 7 fired: database latency spike on db-3")
    memory.remember(branch.id, "customer emailed about billing")

    hits = memory.recall_on_branch(branch.id, "why is the database slow", k=1)
    assert "latency" in hits[0].record.content


def test_recall_is_scoped_to_checkpoint_lineage(env):
    store, run, branch, memory = env
    early = store.create_checkpoint(branch.id, {"step": 1})
    memory.remember(branch.id, "first fact")
    later = store.create_checkpoint(branch.id, {"step": 2})
    memory.remember(branch.id, "second fact")

    # As of the early checkpoint, the second memory doesn't exist yet.
    early_contents = {m.content for m in memory.memories_at(early.id)}
    later_contents = {m.content for m in memory.memories_at(later.id)}
    assert early_contents == {"first fact"}
    assert later_contents == {"first fact", "second fact"}


def test_rewind_resets_what_the_agent_remembers(env):
    store, run, branch, memory = env
    clean = store.create_checkpoint(branch.id, {"step": 1})
    memory.remember(branch.id, "db-3 is the primary database")

    store.create_checkpoint(branch.id, {"step": 2})
    memory.remember(branch.id, "conclusion: db-3 must be wiped")  # poisoned belief

    new_branch = store.rewind(branch.id, clean.id).new_branch

    contents = {m.content for m in memory.memories_at(new_branch.head_checkpoint_id)}
    assert contents == {"db-3 is the primary database"}

    # The poisoned memory is still auditable on the abandoned branch's history.
    old_head = store.get_branch(branch.id)
    assert old_head.status == "abandoned"


def test_recall_k_limits_results(env):
    store, run, branch, memory = env
    store.create_checkpoint(branch.id, {"step": 1})
    for i in range(10):
        memory.remember(branch.id, f"note number {i} about incident response")
    assert len(memory.recall_on_branch(branch.id, "incident", k=3)) == 3
