"""End-to-end walkthrough of the Rewind hero flow — fully offline.

An incident-response agent investigates a simulated outage, forms a poisoned
belief mid-run, and compounds the error. The operator rewinds to the last
clean checkpoint: the poisoned branch is abandoned (not deleted), staged
Slack messages are discarded before they ever fire, and the agent recovers
on a fresh branch.

Run it:  python examples/incident_demo.py
"""

from __future__ import annotations

from rewind import RewindStore
from rewind.memory import VectorMemory
from rewind.proxy import ToolProxy
from rewind.vfs import VFS

sent_messages: list[dict] = []


def main() -> None:
    store = RewindStore.open()  # in-memory; use REWIND_DATABASE_URL in prod
    vfs = VFS(store)
    memory = VectorMemory(store)
    proxy = ToolProxy(
        store, executors={"slack.post": lambda p: sent_messages.append(p) or {"ok": True}}
    )

    run = store.create_run("incident-2026-07-19-db-latency")
    branch = store.active_branch(run.id)
    print(f"▶ run started: {run.name}")

    # -- Steps 1-2: healthy investigation ------------------------------------
    store.create_checkpoint(
        branch.id,
        updates={"status": "investigating", "alerts": ["db-latency-high"]},
        label="step 1: triage alert",
    )
    memory.remember(branch.id, "alert db-latency-high fired for db-3, p99 at 2.4s")

    clean = store.create_checkpoint(
        branch.id,
        updates={"suspect": "connection pool exhaustion"},
        files={"/notes/plan.md": b"1. check pool metrics\n2. check slow queries\n"},
        label="step 2: form hypothesis",
    )
    memory.remember(branch.id, "db-3 connection pool at 98% utilization")
    print(f"✔ clean checkpoint at step {clean.step_index}: {clean.label}")

    # -- Steps 3-4: poisoned belief compounds --------------------------------
    store.create_checkpoint(
        branch.id,
        updates={"suspect": "disk corruption", "status": "escalating"},  # WRONG
        label="step 3: misreads a log line",
    )
    memory.remember(branch.id, "conclusion: db-3 disk is corrupt, data loss imminent")
    proxy.call(branch.id, "slack.post", {"text": "@channel db-3 disk corrupt!"}, mode="staged")

    store.create_checkpoint(
        branch.id,
        updates={"plan": "wipe and restore db-3 from backup"},
        files={"/notes/plan.md": b"1. WIPE db-3\n2. restore from backup\n"},
        label="step 4: plans destructive fix",
    )
    proxy.call(branch.id, "slack.post", {"text": "taking db-3 offline NOW"}, mode="staged")
    proxy.call(branch.id, "slack.post", {"text": "expect 2h downtime"}, mode="staged")

    head = store.get_branch(branch.id).head_checkpoint_id
    print(f"✘ poisoned state: {store.get_state(head)}")
    print(f"  pending side effects: {len(proxy.pending(branch.id))} staged Slack messages")

    # -- The operator hits Rewind --------------------------------------------
    print(f"\n⏪ REWIND to step {clean.step_index} ({clean.label!r})")
    result = store.rewind(branch.id, clean.id, new_branch_name="recovery")
    print(f"  branch {result.abandoned_branch.name!r} abandoned (history preserved)")
    print(f"  🗑 {len(result.discarded_effects)} pending Slack messages discarded")
    assert sent_messages == []  # nothing ever left the sandbox

    # -- Recovery on the fresh branch ----------------------------------------
    recovery = result.new_branch
    print(f"\n▶ resuming on branch {recovery.name!r}")
    print(f"  state: {store.get_state(recovery.head_checkpoint_id)}")
    recalled = memory.recall_on_branch(recovery.id, "what do we know about db-3", k=3)
    print("  memory after rewind (poisoned conclusion is gone):")
    for hit in recalled:
        print(f"    - {hit.record.content}")

    fixed = store.create_checkpoint(
        recovery.id,
        updates={"status": "resolved", "fix": "raised pool size 50 -> 200"},
        files={"/notes/plan.md": b"1. raise pool size\n2. verify p99 < 200ms\n"},
        label="step 3': correct fix",
    )
    proxy.call(recovery.id, "slack.post", {"text": "db-3 latency resolved (pool)"}, mode="staged")
    proxy.flush(recovery.id)
    print(f"\n✔ resolved at step {fixed.step_index}: {store.get_state(fixed.id)}")
    print(f"  1 real Slack message sent after commit: {sent_messages}")

    diff = store.diff(head, fixed.id)
    print(f"\nΔ poisoned head vs. resolved head: {diff}")
    store.close()


if __name__ == "__main__":
    main()
