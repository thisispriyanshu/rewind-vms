import time

from rewind import RewindStore
from rewind.demo import POISONED_STEPS, RECOVERY_STEPS, IncidentDemo


def wait_until(predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_scripted_demo_full_arc():
    store = RewindStore.open()
    demo = IncidentDemo(store, interval=0.05)
    run_id = demo.start()

    # Phase 1: the poisoned branch runs to failure and stops.
    def failed():
        head = store.active_branch(run_id).head_checkpoint_id
        return store.get_state(head).get("status") == "failed"

    assert wait_until(failed)
    poisoned = store.active_branch(run_id)
    assert len(demo.proxy.pending(poisoned.id)) == 3
    assert demo.sent == []

    # Operator rewinds to the last clean checkpoint (step 2).
    clean = store.lineage(poisoned.head_checkpoint_id)[2]
    result = store.rewind(poisoned.id, clean.id, new_branch_name="recovery")
    assert len(result.discarded_effects) == 3

    # Phase 2: the agent notices and resolves on the new branch.
    def resolved():
        head = store.active_branch(run_id).head_checkpoint_id
        return store.get_state(head).get("status") == "resolved"

    assert wait_until(resolved)
    # Only the corrected message ever left the sandbox.
    assert [m["text"] for m in demo.sent] == ["db-3 latency resolved: pool exhausted, size raised"]

    total_steps = len(POISONED_STEPS) + len(RECOVERY_STEPS)
    assert len(store.list_checkpoints(run_id)) == total_steps + 1  # + genesis
    store.close()
