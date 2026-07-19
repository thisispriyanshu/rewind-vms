from rewind import RewindStore, demo


def tick_n(store, run_id, n):
    for _ in range(n):
        demo.tick(store, run_id, interval=0.0)


def test_scripted_demo_full_arc():
    store = RewindStore.open()
    run_id = demo.start(store)

    # Phase 1: tick to failure; the demo then waits for the operator.
    tick_n(store, run_id, len(demo.POISONED_STEPS) + 3)
    poisoned = store.active_branch(run_id)
    head_state = store.get_state(poisoned.head_checkpoint_id)
    assert head_state.get("status") == "failed"

    from rewind.proxy import ToolProxy

    assert len(ToolProxy(store).pending(poisoned.id)) == 3
    assert demo.sent_messages(store, run_id) == []
    # Extra ticks while waiting change nothing.
    checkpoints_before = len(store.list_checkpoints(run_id))
    demo.tick(store, run_id, interval=0.0)
    assert len(store.list_checkpoints(run_id)) == checkpoints_before

    # Operator rewinds to the last clean checkpoint (step 2).
    clean = store.lineage(poisoned.head_checkpoint_id)[2]
    result = store.rewind(poisoned.id, clean.id, new_branch_name="recovery")
    assert len(result.discarded_effects) == 3

    # Phase 2: the agent notices and resolves on the new branch.
    tick_n(store, run_id, len(demo.RECOVERY_STEPS) + 2)
    head = store.active_branch(run_id).head_checkpoint_id
    assert store.get_state(head).get("status") == "resolved"

    # Only the corrected message ever left the sandbox.
    assert [m["text"] for m in demo.sent_messages(store, run_id)] == [
        "db-3 latency resolved: pool exhausted, size raised"
    ]

    total_steps = len(demo.POISONED_STEPS) + len(demo.RECOVERY_STEPS)
    assert len(store.list_checkpoints(run_id)) == total_steps + 1  # + genesis
    store.close()


def test_tick_ignores_non_demo_runs():
    store = RewindStore.open()
    run = store.create_run("not-a-demo")
    demo.tick(store, run.id, interval=0.0)
    assert len(store.list_checkpoints(run.id)) == 1  # genesis only
    store.close()
