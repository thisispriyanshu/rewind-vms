"""The versioned core: checkpoint / branch / rewind over an append-only model.

Design rules:

- History is immutable. A rewind never deletes rows; it abandons the poisoned
  branch and forks a fresh one from a clean checkpoint, so the full tree stays
  available for the dashboard and the audit trail.
- Checkpoints form a single global parent chain across branches: the first
  checkpoint on a forked branch has the fork point as its parent. "State at
  checkpoint X" is resolved by replaying key writes along X's ancestor lineage.
- Each checkpoint commit is one transaction: state deltas, memory, and staged
  effect metadata move together or not at all (serializable on CockroachDB).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from rewind.db import Database, connect
from rewind.models import Branch, Checkpoint, RewindResult, Run, StagedEffect
from rewind.schema import create_schema

GENESIS_LABEL = "genesis"


class StaleHeadError(RuntimeError):
    """The branch head moved between reading it and committing a checkpoint."""


def _uid() -> str:
    return uuid.uuid4().hex


def _now() -> str:
    return datetime.now(UTC).isoformat()


class RewindStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        create_schema(db)

    @classmethod
    def open(cls, url: str | None = None) -> RewindStore:
        return cls(connect(url))

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------------ runs

    def create_run(self, name: str) -> Run:
        """Create a run with a 'main' branch and a genesis checkpoint."""
        run_id, branch_id, ckpt_id, ts = _uid(), _uid(), _uid(), _now()
        with self.db.transaction():
            self.db.execute(
                "INSERT INTO runs (id, name, created_at) VALUES (?, ?, ?)",
                (run_id, name, ts),
            )
            self.db.execute(
                "INSERT INTO branches (id, run_id, name, status, head_checkpoint_id,"
                " forked_from_checkpoint_id, created_at)"
                " VALUES (?, ?, 'main', 'active', NULL, NULL, ?)",
                (branch_id, run_id, ts),
            )
            self.db.execute(
                "INSERT INTO checkpoints (id, run_id, branch_id, parent_checkpoint_id,"
                " step_index, label, created_at) VALUES (?, ?, ?, NULL, 0, ?, ?)",
                (ckpt_id, run_id, branch_id, GENESIS_LABEL, ts),
            )
            self.db.execute(
                "UPDATE branches SET head_checkpoint_id = ? WHERE id = ?",
                (ckpt_id, branch_id),
            )
        return Run(run_id, name, ts)

    def get_run(self, run_id: str) -> Run:
        rows = self.db.query("SELECT id, name, created_at FROM runs WHERE id = ?", (run_id,))
        if not rows:
            raise KeyError(f"run {run_id!r} not found")
        return Run(*rows[0])

    def list_runs(self) -> list[Run]:
        rows = self.db.query("SELECT id, name, created_at FROM runs ORDER BY created_at")
        return [Run(*r) for r in rows]

    # -------------------------------------------------------------- branches

    _BRANCH_COLS = (
        "id, run_id, name, status, head_checkpoint_id, forked_from_checkpoint_id, created_at"
    )

    def get_branch(self, branch_id: str) -> Branch:
        rows = self.db.query(f"SELECT {self._BRANCH_COLS} FROM branches WHERE id = ?", (branch_id,))
        if not rows:
            raise KeyError(f"branch {branch_id!r} not found")
        return Branch(*rows[0])

    def list_branches(self, run_id: str) -> list[Branch]:
        return [
            Branch(*r)
            for r in self.db.query(
                f"SELECT {self._BRANCH_COLS} FROM branches WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            )
        ]

    def active_branch(self, run_id: str) -> Branch:
        """The single active branch of a run (rewind abandons the old one)."""
        active = [b for b in self.list_branches(run_id) if b.status == "active"]
        if not active:
            raise LookupError(f"run {run_id!r} has no active branch")
        return active[-1]

    # ----------------------------------------------------------- checkpoints

    _CKPT_COLS = "id, run_id, branch_id, parent_checkpoint_id, step_index, label, created_at"

    def get_checkpoint(self, checkpoint_id: str) -> Checkpoint:
        rows = self.db.query(
            f"SELECT {self._CKPT_COLS} FROM checkpoints WHERE id = ?", (checkpoint_id,)
        )
        if not rows:
            raise KeyError(f"checkpoint {checkpoint_id!r} not found")
        return Checkpoint(*rows[0])

    def list_checkpoints(self, run_id: str) -> list[Checkpoint]:
        return [
            Checkpoint(*r)
            for r in self.db.query(
                f"SELECT {self._CKPT_COLS} FROM checkpoints WHERE run_id = ?"
                " ORDER BY step_index, created_at",
                (run_id,),
            )
        ]

    def create_checkpoint(
        self,
        branch_id: str,
        updates: Mapping[str, Any] | None = None,
        deletes: Iterable[str] = (),
        label: str | None = None,
        files: Mapping[str, bytes | None] | None = None,
        expected_head: str | None = None,
    ) -> Checkpoint:
        """Commit one step: a new checkpoint plus its state deltas, atomically.

        ``files`` maps VFS paths to content; a ``None`` content deletes the
        path (as a tombstone row — history stays readable at old checkpoints).
        ``expected_head`` enables optimistic concurrency: if another writer
        advanced the branch since the caller read it, StaleHeadError is raised
        instead of appending a duplicate step onto the newer head.
        """
        with self.db.transaction():
            branch = self.get_branch(branch_id)
            if branch.status != "active":
                raise ValueError(f"branch {branch.name!r} is {branch.status}; cannot checkpoint")
            if expected_head is not None and branch.head_checkpoint_id != expected_head:
                raise StaleHeadError(f"branch {branch.name!r} head moved past {expected_head[:8]}")
            parent = self.get_checkpoint(branch.head_checkpoint_id)
            ckpt = Checkpoint(
                id=_uid(),
                run_id=branch.run_id,
                branch_id=branch_id,
                parent_checkpoint_id=parent.id,
                step_index=parent.step_index + 1,
                label=label,
                created_at=_now(),
            )
            self.db.execute(
                f"INSERT INTO checkpoints ({self._CKPT_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ckpt.id,
                    ckpt.run_id,
                    ckpt.branch_id,
                    ckpt.parent_checkpoint_id,
                    ckpt.step_index,
                    ckpt.label,
                    ckpt.created_at,
                ),
            )
            for key, value in (updates or {}).items():
                self.db.execute(
                    "INSERT INTO state_kv (id, run_id, branch_id, checkpoint_id, key, value,"
                    " tombstone) VALUES (?, ?, ?, ?, ?, ?, 0)",
                    (_uid(), ckpt.run_id, branch_id, ckpt.id, key, json.dumps(value)),
                )
            for key in deletes:
                self.db.execute(
                    "INSERT INTO state_kv (id, run_id, branch_id, checkpoint_id, key, value,"
                    " tombstone) VALUES (?, ?, ?, ?, ?, NULL, 1)",
                    (_uid(), ckpt.run_id, branch_id, ckpt.id, key),
                )
            for path, content in (files or {}).items():
                self.db.execute(
                    "INSERT INTO vfs_objects (id, run_id, branch_id, checkpoint_id, path,"
                    " content, blob_ref, size, tombstone) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                    (
                        _uid(),
                        ckpt.run_id,
                        branch_id,
                        ckpt.id,
                        path,
                        content,
                        len(content) if content is not None else 0,
                        0 if content is not None else 1,
                    ),
                )
            self.db.execute(
                "UPDATE branches SET head_checkpoint_id = ? WHERE id = ?", (ckpt.id, branch_id)
            )
        return ckpt

    def lineage(self, checkpoint_id: str) -> list[Checkpoint]:
        """Ancestor chain from genesis to the given checkpoint (inclusive)."""
        tip = self.get_checkpoint(checkpoint_id)
        by_id = {c.id: c for c in self.list_checkpoints(tip.run_id)}
        chain: list[Checkpoint] = []
        cur: Checkpoint | None = by_id[tip.id]
        while cur is not None:
            chain.append(cur)
            cur = by_id.get(cur.parent_checkpoint_id) if cur.parent_checkpoint_id else None
        chain.reverse()
        return chain

    # ------------------------------------------------------------- state kv

    def get_state(self, checkpoint_id: str) -> dict[str, Any]:
        """Full key/value state as of a checkpoint, replayed along its lineage."""
        chain = self.lineage(checkpoint_id)
        order = {c.id: i for i, c in enumerate(chain)}
        placeholders = ", ".join("?" for _ in chain)
        rows = self.db.query(
            "SELECT checkpoint_id, key, value, tombstone FROM state_kv"
            f" WHERE checkpoint_id IN ({placeholders})",
            [c.id for c in chain],
        )
        rows.sort(key=lambda r: order[r[0]])
        state: dict[str, Any] = {}
        for _ckpt, key, value, tombstone in rows:
            if tombstone:
                state.pop(key, None)
            else:
                state[key] = json.loads(value)
        return state

    def get_value(self, checkpoint_id: str, key: str, default: Any = None) -> Any:
        return self.get_state(checkpoint_id).get(key, default)

    def diff(self, checkpoint_a: str, checkpoint_b: str) -> dict[str, tuple[Any, Any]]:
        """Keys that differ between two checkpoints, as ``key -> (a, b)``."""
        a, b = self.get_state(checkpoint_a), self.get_state(checkpoint_b)
        missing = object()
        out: dict[str, tuple[Any, Any]] = {}
        for key in sorted(a.keys() | b.keys()):
            va, vb = a.get(key, missing), b.get(key, missing)
            if va != vb:
                out[key] = (None if va is missing else va, None if vb is missing else vb)
        return out

    # --------------------------------------------------------------- rewind

    def rewind(
        self,
        branch_id: str,
        to_checkpoint_id: str,
        new_branch_name: str | None = None,
    ) -> RewindResult:
        """Abandon a poisoned branch and fork a fresh one at a clean checkpoint.

        Staged (unflushed) effects recorded on the abandoned branch beyond the
        rewind point are discarded — they never happened in the real world.
        """
        with self.db.transaction():
            old = self.get_branch(branch_id)
            target = self.get_checkpoint(to_checkpoint_id)
            if target.run_id != old.run_id:
                raise ValueError("rewind target belongs to a different run")
            keep = {c.id for c in self.lineage(to_checkpoint_id)}

            self.db.execute("UPDATE branches SET status = 'abandoned' WHERE id = ?", (old.id,))

            discarded: list[StagedEffect] = []
            for effect in self.list_effects(old.run_id, status="staged"):
                if effect.branch_id == old.id and effect.checkpoint_id not in keep:
                    self.db.execute(
                        "UPDATE staged_effects SET status = 'discarded' WHERE id = ?",
                        (effect.id,),
                    )
                    discarded.append(effect)

            name = new_branch_name or f"rewind-{target.step_index}-{_uid()[:6]}"
            new = Branch(
                id=_uid(),
                run_id=old.run_id,
                name=name,
                status="active",
                head_checkpoint_id=target.id,
                forked_from_checkpoint_id=target.id,
                created_at=_now(),
            )
            self.db.execute(
                f"INSERT INTO branches ({self._BRANCH_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    new.id,
                    new.run_id,
                    new.name,
                    new.status,
                    new.head_checkpoint_id,
                    new.forked_from_checkpoint_id,
                    new.created_at,
                ),
            )
        abandoned = self.get_branch(old.id)
        return RewindResult(new_branch=new, abandoned_branch=abandoned, discarded_effects=discarded)

    # ------------------------------------------------------- staged effects

    _EFFECT_COLS = (
        "id, run_id, branch_id, checkpoint_id, tool_name, idempotency_key,"
        " payload, result, mode, status, created_at"
    )

    def _effect_from_row(self, row: tuple[Any, ...]) -> StagedEffect:
        (eid, run_id, branch_id, ckpt_id, tool, idem, payload, result, mode, status, ts) = row
        return StagedEffect(
            id=eid,
            run_id=run_id,
            branch_id=branch_id,
            checkpoint_id=ckpt_id,
            tool_name=tool,
            idempotency_key=idem,
            payload=json.loads(payload) if payload else None,
            result=json.loads(result) if result else None,
            mode=mode,
            status=status,
            created_at=ts,
        )

    def record_effect(
        self,
        branch_id: str,
        tool_name: str,
        payload: Any,
        mode: str,
        idempotency_key: str | None = None,
        result: Any = None,
        status: str = "staged",
    ) -> StagedEffect:
        branch = self.get_branch(branch_id)
        effect = StagedEffect(
            id=_uid(),
            run_id=branch.run_id,
            branch_id=branch_id,
            checkpoint_id=branch.head_checkpoint_id,
            tool_name=tool_name,
            idempotency_key=idempotency_key,
            payload=payload,
            result=result,
            mode=mode,
            status=status,
            created_at=_now(),
        )
        self.db.execute(
            f"INSERT INTO staged_effects ({self._EFFECT_COLS})"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                effect.id,
                effect.run_id,
                effect.branch_id,
                effect.checkpoint_id,
                effect.tool_name,
                effect.idempotency_key,
                json.dumps(payload),
                json.dumps(result) if result is not None else None,
                effect.mode,
                effect.status,
                effect.created_at,
            ),
        )
        return effect

    def list_effects(self, run_id: str, status: str | None = None) -> list[StagedEffect]:
        sql = f"SELECT {self._EFFECT_COLS} FROM staged_effects WHERE run_id = ?"
        params: list[Any] = [run_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at"
        return [self._effect_from_row(r) for r in self.db.query(sql, params)]

    def find_effect_by_key(self, run_id: str, idempotency_key: str) -> StagedEffect | None:
        rows = self.db.query(
            f"SELECT {self._EFFECT_COLS} FROM staged_effects"
            " WHERE run_id = ? AND idempotency_key = ? AND status != 'discarded'"
            " ORDER BY created_at",
            (run_id, idempotency_key),
        )
        return self._effect_from_row(rows[0]) if rows else None

    def set_effect_status(self, effect_id: str, status: str, result: Any = None) -> None:
        if result is not None:
            self.db.execute(
                "UPDATE staged_effects SET status = ?, result = ? WHERE id = ?",
                (status, json.dumps(result), effect_id),
            )
        else:
            self.db.execute(
                "UPDATE staged_effects SET status = ? WHERE id = ?", (status, effect_id)
            )

    # ----------------------------------------------------------------- tree

    def get_tree(self, run_id: str) -> dict[str, Any]:
        """Everything the dashboard needs to render the commit graph."""
        return {
            "run": self.get_run(run_id).__dict__,
            "branches": [b.__dict__ for b in self.list_branches(run_id)],
            "checkpoints": [c.__dict__ for c in self.list_checkpoints(run_id)],
            "effects": [{**e.__dict__} for e in self.list_effects(run_id)],
        }
