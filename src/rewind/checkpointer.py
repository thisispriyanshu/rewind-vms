"""LangGraph integration: a BaseCheckpointSaver backed by the Rewind store.

Drop-in persistence for any LangGraph agent:

    from rewind import RewindStore
    from rewind.checkpointer import RewindSaver

    saver = RewindSaver(RewindStore.open(...))
    graph = builder.compile(checkpointer=saver)

Every LangGraph checkpoint becomes a Rewind checkpoint (labelled ``lg:<id>``)
on the thread's run, so the whole toolbox applies: the tree graph, diffs,
staged effects, vector memory, and the dashboard's rewind button.

Time-travel works in both directions. If LangGraph is invoked from an older
checkpoint (its own time-travel API), the saver notices the parent isn't the
branch head and forks a Rewind branch there automatically. If the operator
rewinds from the dashboard, the next ``get_tuple`` simply returns the new
branch head and the agent resumes from the clean state.

v0 limitation: pending task writes (``put_writes``) are kept in-process, not
persisted — resuming an *interrupted* superstep requires the same saver
instance. Completed checkpoints are always durable.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)

from rewind.models import Checkpoint as RewindCheckpoint
from rewind.store import RewindStore

STATE_KEY = "__langgraph__"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"))


class RewindSaver(BaseCheckpointSaver[str]):
    def __init__(self, store: RewindStore) -> None:
        super().__init__()
        self.store = store
        self._writes: dict[tuple[str, str, str], list[tuple[str, str, Any]]] = {}

    # ------------------------------------------------------------- plumbing

    def _run_id(self, thread_id: str, create: bool = False) -> str | None:
        name = f"langgraph:{thread_id}"
        for run in self.store.list_runs():
            if run.name == name:
                return run.id
        if create:
            return self.store.create_run(name).id
        return None

    def _find_by_lg_id(self, run_id: str, lg_checkpoint_id: str) -> RewindCheckpoint | None:
        for ckpt in self.store.list_checkpoints(run_id):
            if ckpt.label == f"lg:{lg_checkpoint_id}":
                return ckpt
        return None

    def _payload_at(self, rewind_checkpoint_id: str) -> dict[str, Any] | None:
        return self.store.get_value(rewind_checkpoint_id, STATE_KEY)

    def _tuple_from_payload(
        self, thread_id: str, checkpoint_ns: str, payload: dict[str, Any]
    ) -> CheckpointTuple:
        checkpoint = self.serde.loads_typed((payload["ckpt_type"], _unb64(payload["ckpt"])))
        metadata = self.serde.loads_typed((payload["meta_type"], _unb64(payload["meta"])))
        parent_config = None
        if payload.get("parent_id"):
            parent_config = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": payload["parent_id"],
                }
            }
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": payload["id"],
                }
            },
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=self._writes.get((thread_id, checkpoint_ns, payload["id"]), []),
        )

    # ------------------------------------------------------ saver interface

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        conf = config["configurable"]
        thread_id = conf["thread_id"]
        checkpoint_ns = conf.get("checkpoint_ns", "")
        parent_lg_id = conf.get("checkpoint_id")

        run_id = self._run_id(thread_id, create=True)
        branch = self.store.active_branch(run_id)

        # LangGraph time-travel: writing under a parent that is not the branch
        # head means the caller rewound — mirror it as a Rewind branch fork.
        if parent_lg_id is not None:
            parent_ckpt = self._find_by_lg_id(run_id, parent_lg_id)
            if parent_ckpt is not None and parent_ckpt.id != branch.head_checkpoint_id:
                head_lineage = {c.id for c in self.store.lineage(branch.head_checkpoint_id)}
                if parent_ckpt.id in head_lineage:
                    branch = self.store.rewind(branch.id, parent_ckpt.id).new_branch

        ckpt_type, ckpt_bytes = self.serde.dumps_typed(checkpoint)
        meta_type, meta_bytes = self.serde.dumps_typed(metadata)
        payload = {
            "id": checkpoint["id"],
            "parent_id": parent_lg_id,
            "ns": checkpoint_ns,
            "ckpt_type": ckpt_type,
            "ckpt": _b64(ckpt_bytes),
            "meta_type": meta_type,
            "meta": _b64(meta_bytes),
        }
        self.store.create_checkpoint(
            branch.id, updates={STATE_KEY: payload}, label=f"lg:{checkpoint['id']}"
        )
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        conf = config["configurable"]
        key = (conf["thread_id"], conf.get("checkpoint_ns", ""), conf["checkpoint_id"])
        self._writes.setdefault(key, []).extend(
            (task_id, channel, value) for channel, value in writes
        )

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        conf = config["configurable"]
        thread_id = conf["thread_id"]
        checkpoint_ns = conf.get("checkpoint_ns", "")
        run_id = self._run_id(thread_id)
        if run_id is None:
            return None

        branch = self.store.active_branch(run_id)
        lg_id = conf.get("checkpoint_id")
        if lg_id is not None:
            ckpt = self._find_by_lg_id(run_id, lg_id)
            if ckpt is None:
                return None
            resolved_id = ckpt.id
        else:
            resolved_id = branch.head_checkpoint_id
        payload = self._payload_at(resolved_id)
        if payload is None:
            return None

        # A fresh fork parked exactly at this checkpoint means the operator
        # rewound here (e.g. from the dashboard). Any in-process pending
        # writes for it belong to the abandoned future — drop them so the
        # agent resumes from the clean checkpoint, not a stale superstep.
        if (
            branch.head_checkpoint_id == resolved_id
            and branch.forked_from_checkpoint_id == resolved_id
        ):
            self._writes.pop((thread_id, checkpoint_ns, payload["id"]), None)

        return self._tuple_from_payload(thread_id, checkpoint_ns, payload)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        if config is None:
            return
        conf = config["configurable"]
        thread_id = conf["thread_id"]
        checkpoint_ns = conf.get("checkpoint_ns", "")
        run_id = self._run_id(thread_id)
        if run_id is None:
            return

        head = self.store.active_branch(run_id).head_checkpoint_id
        if before is not None:
            before_ckpt = self._find_by_lg_id(run_id, before["configurable"]["checkpoint_id"])
            if before_ckpt is not None and before_ckpt.parent_checkpoint_id:
                head = before_ckpt.parent_checkpoint_id
            else:
                return

        count = 0
        for ckpt in reversed(self.store.lineage(head)):
            if limit is not None and count >= limit:
                return
            payload = self._payload_at(ckpt.id) if ckpt.label else None
            if not payload or not (ckpt.label or "").startswith("lg:"):
                continue
            # Payload resolution follows lineage; only yield the checkpoint's own.
            if f"lg:{payload['id']}" != ckpt.label:
                continue
            count += 1
            yield self._tuple_from_payload(thread_id, checkpoint_ns, payload)
