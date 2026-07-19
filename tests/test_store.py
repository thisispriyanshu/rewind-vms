import pytest

from rewind import RewindStore


@pytest.fixture
def store():
    s = RewindStore.open()  # in-memory SQLite
    yield s
    s.close()


@pytest.fixture
def run(store):
    return store.create_run("incident-42")


def head(store, run):
    return store.active_branch(run.id).head_checkpoint_id


def test_create_run_has_main_branch_and_genesis(store, run):
    branch = store.active_branch(run.id)
    assert branch.name == "main"
    genesis = store.get_checkpoint(branch.head_checkpoint_id)
    assert genesis.step_index == 0
    assert genesis.parent_checkpoint_id is None
    assert store.get_state(genesis.id) == {}


def test_checkpoints_accumulate_state(store, run):
    branch = store.active_branch(run.id)
    c1 = store.create_checkpoint(branch.id, {"status": "investigating", "severity": 2})
    c2 = store.create_checkpoint(branch.id, {"severity": 1, "assignee": "agent"})

    assert store.get_state(c1.id) == {"status": "investigating", "severity": 2}
    assert store.get_state(c2.id) == {
        "status": "investigating",
        "severity": 1,
        "assignee": "agent",
    }
    assert c2.parent_checkpoint_id == c1.id
    assert c2.step_index == c1.step_index + 1


def test_deletes_are_tombstones_not_destruction(store, run):
    branch = store.active_branch(run.id)
    c1 = store.create_checkpoint(branch.id, {"temp": "scratch", "keep": 1})
    c2 = store.create_checkpoint(branch.id, deletes=["temp"])

    assert store.get_state(c2.id) == {"keep": 1}
    # History is intact: the old checkpoint still sees the deleted key.
    assert store.get_state(c1.id) == {"temp": "scratch", "keep": 1}


def test_json_values_round_trip(store, run):
    branch = store.active_branch(run.id)
    value = {"nested": [1, 2, {"a": None}], "flag": True}
    c = store.create_checkpoint(branch.id, {"doc": value})
    assert store.get_value(c.id, "doc") == value


def test_rewind_forks_branch_and_preserves_history(store, run):
    branch = store.active_branch(run.id)
    good = store.create_checkpoint(branch.id, {"belief": "db is healthy"})
    bad = store.create_checkpoint(branch.id, {"belief": "db is corrupt"})  # poisoned
    worse = store.create_checkpoint(branch.id, {"action": "drop tables"})

    result = store.rewind(branch.id, good.id)

    assert result.abandoned_branch.status == "abandoned"
    new = result.new_branch
    assert new.status == "active"
    assert new.head_checkpoint_id == good.id
    assert new.forked_from_checkpoint_id == good.id

    # State at the new head is the clean state.
    assert store.get_state(new.head_checkpoint_id) == {"belief": "db is healthy"}
    # Poisoned history is preserved for audit, not deleted.
    assert store.get_state(worse.id) == {"belief": "db is corrupt", "action": "drop tables"}
    assert store.get_checkpoint(bad.id).label == bad.label


def test_new_branch_continues_from_clean_checkpoint(store, run):
    branch = store.active_branch(run.id)
    good = store.create_checkpoint(branch.id, {"x": 1})
    store.create_checkpoint(branch.id, {"x": 999})

    new = store.rewind(branch.id, good.id).new_branch
    fixed = store.create_checkpoint(new.id, {"x": 2, "hint": "ignore alert 7"})

    assert store.get_state(fixed.id) == {"x": 2, "hint": "ignore alert 7"}
    # Lineage crosses the fork: genesis -> good -> fixed.
    chain = [c.id for c in store.lineage(fixed.id)]
    assert good.id in chain
    assert fixed.id == chain[-1]


def test_cannot_checkpoint_abandoned_branch(store, run):
    branch = store.active_branch(run.id)
    good = store.create_checkpoint(branch.id, {"x": 1})
    store.rewind(branch.id, good.id)
    with pytest.raises(ValueError, match="abandoned"):
        store.create_checkpoint(branch.id, {"x": 2})


def test_rewind_to_other_run_rejected(store):
    r1, r2 = store.create_run("a"), store.create_run("b")
    b1 = store.active_branch(r1.id)
    with pytest.raises(ValueError, match="different run"):
        store.rewind(b1.id, head(store, r2))


def test_expected_head_guard_rejects_stale_writer(store, run):
    from rewind.store import StaleHeadError

    branch = store.active_branch(run.id)
    stale_head = branch.head_checkpoint_id
    store.create_checkpoint(branch.id, {"x": 1})  # another writer advances head

    with pytest.raises(StaleHeadError):
        store.create_checkpoint(branch.id, {"x": 2}, expected_head=stale_head)
    # Without the guard the same write succeeds.
    store.create_checkpoint(branch.id, {"x": 2})


def test_diff_between_checkpoints(store, run):
    branch = store.active_branch(run.id)
    a = store.create_checkpoint(branch.id, {"x": 1, "y": "same", "gone": True})
    b = store.create_checkpoint(branch.id, {"x": 2, "new": "hi"}, deletes=["gone"])

    assert store.diff(a.id, b.id) == {
        "x": (1, 2),
        "gone": (True, None),
        "new": (None, "hi"),
    }


def test_rewind_discards_staged_effects_beyond_rewind_point(store, run):
    branch = store.active_branch(run.id)
    clean = store.create_checkpoint(branch.id, {"step": 1})
    early = store.record_effect(branch.id, "slack.post", {"text": "before"}, mode="staged")

    store.create_checkpoint(branch.id, {"step": 2})
    late1 = store.record_effect(branch.id, "slack.post", {"text": "oops 1"}, mode="staged")
    store.create_checkpoint(branch.id, {"step": 3})
    late2 = store.record_effect(branch.id, "slack.post", {"text": "oops 2"}, mode="staged")

    result = store.rewind(branch.id, clean.id)

    discarded_ids = {e.id for e in result.discarded_effects}
    assert discarded_ids == {late1.id, late2.id}
    statuses = {e.id: e.status for e in store.list_effects(run.id)}
    assert statuses[late1.id] == "discarded"
    assert statuses[late2.id] == "discarded"
    # The effect staged before the rewind point survives.
    assert statuses[early.id] == "staged"


def test_tree_contains_all_branches_and_checkpoints(store, run):
    branch = store.active_branch(run.id)
    good = store.create_checkpoint(branch.id, {"x": 1})
    store.create_checkpoint(branch.id, {"x": 2})
    store.rewind(branch.id, good.id)

    tree = store.get_tree(run.id)
    assert len(tree["branches"]) == 2
    assert len(tree["checkpoints"]) == 3  # genesis + 2
    assert {b["status"] for b in tree["branches"]} == {"active", "abandoned"}
