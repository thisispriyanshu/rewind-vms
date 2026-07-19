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
from rewind.store import RewindStore

DEMO_RUN_PREFIX = "incident-"

POISONED_STEPS = [
    dict(
        updates={"status": "investigating", "alerts": ["db-latency-high"], "severity": 2},
        label="triage alert",
        memory="alert db-latency-high fired for db-3, p99 latency 2.4s",
    ),
    dict(
        updates={"suspect": "connection pool exhaustion"},
        files={"/notes/plan.md": b"1. check pool metrics\n2. check slow queries\n"},
        label="form hypothesis",
        memory="db-3 connection pool at 98% utilization",
    ),
    dict(
        updates={"suspect": "disk corruption", "severity": 1},
        label="misreads log line",
        memory="conclusion: db-3 disk is corrupt, data loss imminent",
        slack="@channel db-3 disk corruption detected!",
    ),
    dict(
        updates={"plan": "wipe db-3 and restore from backup", "status": "escalating"},
        files={"/notes/plan.md": b"1. WIPE db-3\n2. restore from backup\n"},
        label="plans destructive fix",
        slack="taking db-3 offline NOW",
    ),
    dict(
        updates={"backup_check": "backup is 26h stale"},
        label="backup check fails",
        slack="expect major data loss, restoring anyway",
    ),
    dict(
        updates={"status": "failed", "error": "restore aborted: backup integrity check failed"},
        label="failure surfaces",
    ),
]

RECOVERY_STEPS = [
    dict(
        updates={"suspect": "connection pool exhaustion", "hint": "logs re-checked by operator"},
        label="resumes with corrected belief",
        memory="operator hint: the corruption log line was from a test harness",
    ),
    dict(
        updates={"fix": "raised pool size 50 -> 200", "severity": 3},
        files={"/notes/plan.md": b"1. raise pool size\n2. verify p99 < 200ms\n"},
        label="applies safe fix",
        slack="db-3 latency resolved: pool exhausted, size raised",
    ),
    dict(
        updates={"status": "resolved", "p99_ms": 142},
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


def _apply(store: RewindStore, branch_id: str, step: dict) -> None:
    store.create_checkpoint(
        branch_id,
        updates=step.get("updates"),
        files=step.get("files"),
        label=step["label"],
    )
    if step.get("memory"):
        VectorMemory(store).remember(branch_id, step["memory"])
    if step.get("slack"):
        ToolProxy(store).call(branch_id, "slack.post", {"text": step["slack"]}, mode="staged")


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
        _apply(store, active.id, POISONED_STEPS[steps_applied])
        return

    fork = store.get_checkpoint(active.forked_from_checkpoint_id)
    steps_applied = head.step_index - fork.step_index
    if steps_applied >= len(RECOVERY_STEPS):
        return  # resolved
    if _seconds_since(head.created_at) < interval:
        return
    _apply(store, active.id, RECOVERY_STEPS[steps_applied])
    if steps_applied + 1 >= len(RECOVERY_STEPS):
        ToolProxy(store, executors={"slack.post": _slack_executor}).flush(active.id)


def sent_messages(store: RewindStore, run_id: str) -> list[dict]:
    """Messages that actually left the sandbox (committed staged effects)."""
    return [e.payload for e in store.list_effects(run_id, status="committed") if e.mode == "staged"]
