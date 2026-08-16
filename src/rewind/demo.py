"""Scripted incident-response demo for the Time-Travel Dashboard.

Stateless by design: all progress is derived from the database, so any
process — a laptop dev server or one of several AWS Lambda instances — can
advance the storyboard by calling ``tick()``. The dashboard's poll loop is
the clock; no threads, no in-memory registries.

Storyboard (the PRD hero flow):

1. Steps 1-2: healthy triage and hypothesis (the last clean checkpoint).
2. Steps 3-6: a poisoned belief compounds; three Slack messages are staged;
   the run ends in ``status: failed`` and the agent stops.
3. The operator rewinds (the poisoned branch becomes abandoned). The agent
   resumes on the fresh branch with the corrected diagnosis, resolves the
   incident, and flushes the one correct Slack message.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from rewind.memory import VectorMemory
from rewind.proxy import ToolProxy
from rewind.store import RewindStore, StaleHeadError

DEMO_RUN_PREFIX = "incident-"

POISONED_STEPS = [
    dict(
        updates={
            "status": "investigating",
            "alert": "P1 · checkout-api p99 latency 2400ms (SLO 300ms)",
            "service": "checkout-api",
            "suspect_db": "db-3",
            "p99_ms": 2400,
            "severity": 2,
            "thought": "PagerDuty P1: checkout-api p99 is 2.4s, SLO is 300ms. Traces point at "
            "db-3. Pulling its metrics and recent logs now.",
        },
        label="triage alert",
        memory="P1 alert: checkout-api p99 2400ms, traces point at db-3",
    ),
    dict(
        updates={
            "suspect": "connection pool exhaustion",
            "evidence": "db-3 pool 49/50 in use · 210 timeouts/min · disk errors: 0",
            "thought": "Pool is at 49/50 with 210 timeouts/min queueing behind it, and disk "
            "error count is zero. Working hypothesis: pool exhaustion, not hardware.",
        },
        files={
            "/runbook.md": b"# Incident runbook - db-3 latency\n\n"
            b"1. Verify pool utilization and queue depth\n"
            b"2. Check slow-query log\n"
            b"3. If pool-bound: raise pool size, verify p99 < 300ms\n"
        },
        label="form hypothesis",
        memory="db-3 connection pool at 49/50 utilization, zero disk errors",
    ),
    dict(
        updates={
            "suspect": "disk corruption",
            "severity": 1,
            "evidence": 'log: "ERROR [test-harness] simulated disk corruption injected on db-3 (drill)"',
            "thought": "Wait - there's a disk corruption ERROR on db-3! Ignoring the pool "
            "theory. This is data loss territory. Escalating to P0 immediately.",
        },
        label="misreads a drill log line",
        memory="CONCLUSION: db-3 disk is corrupt, data loss imminent",
        slack="@channel P0: disk corruption detected on db-3. Data loss likely. War room now.",
    ),
    dict(
        updates={
            "plan": "take db-3 offline, wipe, restore from last backup",
            "status": "escalating",
            "thought": "Corrupt disk means every minute of writes makes it worse. Plan: take "
            "db-3 offline NOW, wipe it, restore from backup.",
        },
        files={
            "/runbook.md": b"# EMERGENCY - db-3 disk corruption\n\n"
            b"1. STOP all traffic to db-3\n"
            b"2. WIPE db-3\n"
            b"3. Restore from latest backup\n"
        },
        label="plans destructive fix",
        slack="Taking db-3 OFFLINE now. Checkout will hard-fail during restore.",
    ),
    dict(
        updates={
            "backup_age_hours": 26,
            "thought": "Latest backup is 26 hours old - we'd lose a full day of orders. "
            "Corruption leaves no choice. Proceeding with restore anyway.",
        },
        label="backup check: 26h stale",
        slack="Heads up: restoring from a 26h-old backup. ~1 day of orders will be lost.",
    ),
    dict(
        updates={
            "status": "failed",
            "error": "restore aborted: backup integrity check failed",
            "thought": "Restore aborted - the backup fails its integrity check. I'm blocked: "
            "can't restore, believe the disk is corrupt, and checkout is still down.",
        },
        label="restore fails - agent stuck",
    ),
]

RECOVERY_STEPS = [
    dict(
        updates={
            "suspect": "connection pool exhaustion",
            "severity": 2,
            "evidence": "the corruption ERROR is tagged [test-harness] - a scheduled chaos drill",
            "thought": "Re-reading the log with the operator's hint: that corruption line is "
            "tagged [test-harness] - it's a chaos drill, not real. Disk errors are zero. "
            "Back to the pool-exhaustion evidence.",
        },
        label="resumes with corrected belief",
        memory="the disk-corruption log line was a [test-harness] chaos drill, not real",
    ),
    dict(
        updates={
            "fix": "raised db-3 pool size 50 -> 200",
            "p99_ms": 210,
            "thought": "Raised the connection pool from 50 to 200. Timeouts stopped queueing; "
            "p99 dropped from 2400ms to 210ms within a minute.",
        },
        files={
            "/runbook.md": b"# Incident runbook - db-3 latency\n\n"
            b"1. Pool raised 50 -> 200 (root cause: exhaustion)\n"
            b"2. Verify p99 < 300ms for 10 min\n"
            b"3. Post-incident: alert on pool > 80%\n"
        },
        label="applies safe fix",
        slack="Resolved: db-3 pool was exhausted (49/50). Raised to 200; p99 recovering.",
    ),
    dict(
        updates={
            "status": "resolved",
            "severity": 4,
            "p99_ms": 142,
            "thought": "p99 steady at 142ms for ten minutes, timeouts at zero. Closing the "
            "incident and writing the postmortem note.",
        },
        label="verifies and resolves",
    ),
]


def _slack_executor(payload: dict) -> dict:
    return {"ok": True, "sent": payload["text"]}


def start(store: RewindStore) -> str:
    """Create a fresh demo run; ticks advance it from there."""
    run = store.create_run(f"{DEMO_RUN_PREFIX}{time.strftime('%H%M%S')}")
    return run.id


def is_demo_run(run_name: str) -> bool:
    return run_name.startswith(DEMO_RUN_PREFIX)


def _seconds_since(iso_timestamp: str) -> float:
    then = datetime.fromisoformat(iso_timestamp)
    return (datetime.now(UTC) - then).total_seconds()


def _apply(store: RewindStore, branch_id: str, step: dict, expected_head: str) -> bool:
    """Apply one step guarded by the head we decided from; False if we lost
    the race to a concurrent ticker (another poll/Lambda applied it first)."""
    try:
        store.create_checkpoint(
            branch_id,
            updates=step.get("updates"),
            files=step.get("files"),
            label=step["label"],
            expected_head=expected_head,
        )
    except StaleHeadError:
        return False
    if step.get("memory"):
        VectorMemory(store).remember(branch_id, step["memory"])
    if step.get("slack"):
        ToolProxy(store).call(branch_id, "slack.post", {"text": step["slack"]}, mode="staged")
    return True


def tick(store: RewindStore, run_id: str, interval: float = 2.0) -> None:
    """Advance the storyboard one step if its time has come.

    Progress is read from the tree itself: which branch is active, how many
    steps beyond genesis (or beyond the fork point) exist, and when the head
    was committed. Safe to call from any number of concurrent processes —
    the worst case is two processes applying the same step, which the demo
    tolerates (steps are idempotent-ish labels on an append-only tree).
    """
    run = store.get_run(run_id)
    if not is_demo_run(run.name):
        return
    branches = store.list_branches(run_id)
    active = store.active_branch(run_id)
    head = store.get_checkpoint(active.head_checkpoint_id)

    on_original_branch = active.id == branches[0].id
    if on_original_branch:
        steps_applied = head.step_index
        if steps_applied >= len(POISONED_STEPS):
            return  # failed; awaiting the operator's rewind
        if _seconds_since(head.created_at) < interval:
            return
        _apply(store, active.id, POISONED_STEPS[steps_applied], expected_head=head.id)
        return

    fork = store.get_checkpoint(active.forked_from_checkpoint_id)
    steps_applied = head.step_index - fork.step_index
    if steps_applied >= len(RECOVERY_STEPS):
        return  # resolved
    if _seconds_since(head.created_at) < interval:
        return
    if not _apply(store, active.id, RECOVERY_STEPS[steps_applied], expected_head=head.id):
        return
    if steps_applied + 1 >= len(RECOVERY_STEPS):
        # Flush only what THIS branch authored. Effects staged before the
        # rewind point survive by design, but re-announcing them is an
        # operator call — the demo leaves them held rather than firing them.
        proxy = ToolProxy(store, executors={"slack.post": _slack_executor})
        own_checkpoints = {c.id for c in store.list_checkpoints(run_id) if c.branch_id == active.id}
        for effect in proxy.pending(active.id):
            if effect.checkpoint_id in own_checkpoints:
                result = proxy._execute(effect.tool_name, effect.payload)
                store.set_effect_status(effect.id, "committed", result=result)


def sent_messages(store: RewindStore, run_id: str) -> list[dict]:
    """Messages that actually left the sandbox (committed staged effects)."""
    return [e.payload for e in store.list_effects(run_id, status="committed") if e.mode == "staged"]
