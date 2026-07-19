"""Typed records returned by the store."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Run:
    id: str
    name: str
    created_at: str


@dataclass(frozen=True)
class Branch:
    id: str
    run_id: str
    name: str
    status: str  # 'active' | 'abandoned'
    head_checkpoint_id: str | None
    forked_from_checkpoint_id: str | None
    created_at: str


@dataclass(frozen=True)
class Checkpoint:
    id: str
    run_id: str
    branch_id: str
    parent_checkpoint_id: str | None
    step_index: int
    label: str | None
    created_at: str


@dataclass(frozen=True)
class StagedEffect:
    id: str
    run_id: str
    branch_id: str
    checkpoint_id: str
    tool_name: str
    idempotency_key: str | None
    payload: Any
    result: Any
    mode: str  # 'staged' | 'idempotent' | 'dry_run'
    status: str  # 'staged' | 'committed' | 'discarded'
    created_at: str


@dataclass(frozen=True)
class RewindResult:
    """Outcome of a rewind: the fresh branch plus what was safely thrown away."""

    new_branch: Branch
    abandoned_branch: Branch
    discarded_effects: list[StagedEffect]
