"""Idempotent Tool Proxy: the only gate between the agent and the real world.

A database can rewind rows; it cannot un-send a Slack message. So external
side effects never fire directly — they pass through this proxy in one of
three modes:

- ``staged``     — recorded as a pending row, executed only on ``flush()``
                   (checkpoint commit). A rewind discards unflushed effects.
- ``idempotent`` — fires immediately but under a stable idempotency key; a
                   replay after rewind returns the recorded result instead of
                   executing twice (no double-charge, no duplicate ticket).
- ``dry_run``    — never touches a real executor; returns a mock result.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rewind.models import StagedEffect
from rewind.store import RewindStore

Executor = Callable[[Any], Any]


@dataclass(frozen=True)
class EffectOutcome:
    effect: StagedEffect
    executed: bool  # did a real executor run for THIS call
    result: Any


class ToolProxy:
    def __init__(self, store: RewindStore, executors: dict[str, Executor] | None = None) -> None:
        self.store = store
        self.executors = dict(executors or {})

    def register(self, tool_name: str, executor: Executor) -> None:
        self.executors[tool_name] = executor

    def _execute(self, tool_name: str, payload: Any) -> Any:
        if tool_name not in self.executors:
            raise KeyError(f"no executor registered for tool {tool_name!r}")
        return self.executors[tool_name](payload)

    def call(
        self,
        branch_id: str,
        tool_name: str,
        payload: Any,
        mode: str = "staged",
        idempotency_key: str | None = None,
    ) -> EffectOutcome:
        if mode == "staged":
            effect = self.store.record_effect(branch_id, tool_name, payload, mode="staged")
            return EffectOutcome(effect=effect, executed=False, result=None)

        if mode == "idempotent":
            if not idempotency_key:
                raise ValueError("idempotent mode requires an idempotency_key")
            branch = self.store.get_branch(branch_id)
            prior = self.store.find_effect_by_key(branch.run_id, idempotency_key)
            if prior is not None and prior.status == "committed":
                return EffectOutcome(effect=prior, executed=False, result=prior.result)
            result = self._execute(tool_name, payload)
            effect = self.store.record_effect(
                branch_id,
                tool_name,
                payload,
                mode="idempotent",
                idempotency_key=idempotency_key,
                result=result,
                status="committed",
            )
            return EffectOutcome(effect=effect, executed=True, result=result)

        if mode == "dry_run":
            result = {"dry_run": True, "tool": tool_name}
            effect = self.store.record_effect(
                branch_id, tool_name, payload, mode="dry_run", result=result, status="committed"
            )
            return EffectOutcome(effect=effect, executed=False, result=result)

        raise ValueError(f"unknown proxy mode {mode!r}")

    def pending(self, branch_id: str) -> list[StagedEffect]:
        """Staged effects that would flush if this branch committed now.

        Only effects recorded at checkpoints on the branch head's lineage
        count — effects stranded on abandoned branches are not pending here.
        """
        branch = self.store.get_branch(branch_id)
        keep = {c.id for c in self.store.lineage(branch.head_checkpoint_id)}
        return [
            e
            for e in self.store.list_effects(branch.run_id, status="staged")
            if e.checkpoint_id in keep
        ]

    def flush(self, branch_id: str) -> list[EffectOutcome]:
        """Execute and commit every pending staged effect on this branch.

        This is the moment staged work becomes real; anything a rewind
        discarded before this point simply never happens.
        """
        outcomes: list[EffectOutcome] = []
        for effect in self.pending(branch_id):
            result = self._execute(effect.tool_name, effect.payload)
            self.store.set_effect_status(effect.id, "committed", result=result)
            committed = StagedEffect(**{**effect.__dict__, "status": "committed", "result": result})
            outcomes.append(EffectOutcome(effect=committed, executed=True, result=result))
        return outcomes
