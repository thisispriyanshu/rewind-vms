from rewind import RewindStore
from rewind.demo import POISONED_STEPS, RECOVERY_STEPS, IncidentDemo


def test_scripted_demo_full_arc():
    store = RewindStore.open()
    demo = IncidentDemo(store, interval=0.0)  # every tick applies one step
    run_id = demo.start()

    # Phase 1: tick to failure; the demo then waits for the operator.
    for _ in range(len(POISONED_STEPS) + 3):
        demo.tick()
    poisoned = store.active_branch(run_id)
    head_state = store.get_state(poisoned.head_checkpoint_id)
    assert head_state.get("status") == "failed"
    assert len(demo.proxy.pending(poisoned.id)) == 3
    assert demo.sent == []
    # Extra ticks while waiting change nothing.
    checkpoints_before = len(store.list_checkpoints(run_id))
    demo.tick()
    assert len(store.list_checkpoints(run_id)) == checkpoints_before

    # Operator rewinds to the last clean checkpoint (step 2).
    clean = store.lineage(poisoned.head_checkpoint_id)[2]
    result = store.rewind(poisoned.id, clean.id, new_branch_name="recovery")
    assert len(result.discarded_effects) == 3

    # Phase 2: the agent notices and resolves on the new branch.
    for _ in range(len(RECOVERY_STEPS) + 2):
        demo.tick()
    head = store.active_branch(run_id).head_checkpoint_id
    assert store.get_state(head).get("status") == "resolved"

    # Only the corrected message ever left the sandbox.
    assert [m["text"] for m in demo.sent] == ["db-3 latency resolved: pool exhausted, size raised"]

    total_steps = len(POISONED_STEPS) + len(RECOVERY_STEPS)
    assert len(store.list_checkpoints(run_id)) == total_steps + 1  # + genesis
    store.close()
