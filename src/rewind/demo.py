"""Scripted incident-response demo agent for the Time-Travel Dashboard.

Runs the PRD hero storyboard as a background thread so the dashboard's tree
graph grows live:

1. Steps 1-2: healthy triage and hypothesis (the last clean checkpoint).
2. Steps 3-6: a poisoned belief compounds; three Slack messages are staged;
   the run ends in ``status: failed`` and the agent stops.
3. The thread then waits for the operator to rewind (the poisoned branch
   becomes abandoned). When a fresh branch appears, the agent resumes on it
   with the corrected diagnosis and resolves the incident.

Every step is a real checkpoint commit through the same public APIs the SDK
exposes — the demo exercises the actual system, not a mock of it.
"""

from __future__ import annotations

import threading
import time

from rewind.memory import VectorMemory
from rewind.proxy import ToolProxy
from rewind.store import RewindStore

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


class IncidentDemo:
    """Drives the scripted incident in a daemon thread."""

    def __init__(self, store: RewindStore, interval: float = 2.0) -> None:
        self.store = store
        self.interval = interval
        self.memory = VectorMemory(store)
        self.sent: list[dict] = []
        self.proxy = ToolProxy(
            store, executors={"slack.post": lambda p: self.sent.append(p) or {"ok": True}}
        )
        self.run_id: str | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        run = self.store.create_run(f"incident-{time.strftime('%H%M%S')}")
        self.run_id = run.id
        self._thread = threading.Thread(target=self._drive, daemon=True)
        self._thread.start()
        return run.id

    def _apply(self, branch_id: str, step: dict) -> None:
        self.store.create_checkpoint(
            branch_id,
            updates=step.get("updates"),
            files=step.get("files"),
            label=step["label"],
        )
        if step.get("memory"):
            self.memory.remember(branch_id, step["memory"])
        if step.get("slack"):
            self.proxy.call(branch_id, "slack.post", {"text": step["slack"]}, mode="staged")

    def _drive(self) -> None:
        assert self.run_id is not None
        poisoned = self.store.active_branch(self.run_id)
        for step in POISONED_STEPS:
            time.sleep(self.interval)
            self._apply(poisoned.id, step)

        # Failed. Wait for the operator to rewind (a new active branch appears).
        while True:
            time.sleep(1.0)
            active = self.store.active_branch(self.run_id)
            if active.id != poisoned.id:
                break

        for step in RECOVERY_STEPS:
            time.sleep(self.interval)
            self._apply(active.id, step)
        self.proxy.flush(active.id)
