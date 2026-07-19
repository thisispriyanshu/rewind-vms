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
    """Drives the scripted incident via lazy ticks.

    No background threads: callers (the dashboard's poll loop via the API)
    call ``tick()``, and a step is applied when its interval has elapsed.
    This works identically on a laptop and on freeze/thaw runtimes like AWS
    Lambda, where a daemon thread would stall between requests.
    """

    def __init__(self, store: RewindStore, interval: float = 2.0) -> None:
        self.store = store
        self.interval = interval
        self.memory = VectorMemory(store)
        self.sent: list[dict] = []
        self.proxy = ToolProxy(
            store, executors={"slack.post": lambda p: self.sent.append(p) or {"ok": True}}
        )
        self.run_id: str | None = None
        self._poisoned_branch_id: str | None = None
        self._next_step = 0
        self._phase = "poisoned"  # -> 'awaiting_rewind' -> 'recovery' -> 'done'
        self._last_step_at = 0.0
        self._lock = threading.Lock()

    def start(self) -> str:
        run = self.store.create_run(f"incident-{time.strftime('%H%M%S')}")
        self.run_id = run.id
        self._poisoned_branch_id = self.store.active_branch(run.id).id
        self._last_step_at = time.monotonic()
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

    def tick(self) -> None:
        """Advance the storyboard if the current step's time has come."""
        with self._lock:
            if self.run_id is None or self._phase == "done":
                return

            active = self.store.active_branch(self.run_id)
            if self._phase == "awaiting_rewind":
                if active.id != self._poisoned_branch_id:
                    self._phase = "recovery"
                    self._next_step = 0
                    self._last_step_at = time.monotonic()
                return

            if time.monotonic() - self._last_step_at < self.interval:
                return
            self._last_step_at = time.monotonic()

            if self._phase == "poisoned":
                self._apply(active.id, POISONED_STEPS[self._next_step])
                self._next_step += 1
                if self._next_step >= len(POISONED_STEPS):
                    self._phase = "awaiting_rewind"
            elif self._phase == "recovery":
                self._apply(active.id, RECOVERY_STEPS[self._next_step])
                self._next_step += 1
                if self._next_step >= len(RECOVERY_STEPS):
                    self.proxy.flush(active.id)
                    self._phase = "done"
