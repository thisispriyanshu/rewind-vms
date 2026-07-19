"""Vector memory: semantic long-term memory that rewinds with the agent.

Every memory row carries the branch and checkpoint it was formed at, so recall
resolves along the checkpoint lineage exactly like state and files. Rewinding
doesn't just reset the agent's data — it resets *what the agent remembers*,
and both move in the same transaction (no consistency gap between vector data
and operational rows).

Embedders are pluggable. Production uses a real model (e.g. Bedrock Titan) and
CockroachDB's distributed vector index; local dev falls back to a deterministic
hashing embedder and in-process cosine similarity so the whole system runs
offline.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass

from rewind.schema import EMBEDDING_DIM
from rewind.store import RewindStore, _now, _uid

Embedder = Callable[[str], list[float]]


class HashingEmbedder:
    """Deterministic bag-of-words embedding — no model, no network.

    Not semantically deep, but shared vocabulary yields higher cosine
    similarity, which is enough for offline tests and demos.
    """

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim

    def __call__(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            digest = hashlib.md5(token.encode()).digest()
            vec[int.from_bytes(digest[:4], "big") % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError("embedding dimensions differ")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    run_id: str
    branch_id: str
    checkpoint_id: str
    content: str
    embedding: list[float]
    created_at: str


@dataclass(frozen=True)
class RecallHit:
    record: MemoryRecord
    score: float


class VectorMemory:
    def __init__(self, store: RewindStore, embedder: Embedder | None = None) -> None:
        self.store = store
        self.embedder = embedder or HashingEmbedder()

    def remember(self, branch_id: str, content: str) -> MemoryRecord:
        """Store a memory at the branch's current head checkpoint."""
        branch = self.store.get_branch(branch_id)
        record = MemoryRecord(
            id=_uid(),
            run_id=branch.run_id,
            branch_id=branch_id,
            checkpoint_id=branch.head_checkpoint_id,
            content=content,
            embedding=self.embedder(content),
            created_at=_now(),
        )
        # json.dumps of a float list is also valid CockroachDB VECTOR literal
        # syntax; the explicit cast makes the intent unambiguous there.
        embedding_expr = "?::VECTOR" if self.store.db.dialect == "postgres" else "?"
        self.store.db.execute(
            "INSERT INTO agent_memory (id, run_id, branch_id, checkpoint_id, content,"
            f" embedding, tombstone, created_at) VALUES (?, ?, ?, ?, ?, {embedding_expr}, 0, ?)",
            (
                record.id,
                record.run_id,
                record.branch_id,
                record.checkpoint_id,
                record.content,
                json.dumps(record.embedding),
                record.created_at,
            ),
        )
        return record

    def memories_at(self, checkpoint_id: str) -> list[MemoryRecord]:
        """All memories the agent had formed as of a checkpoint's lineage."""
        chain = self.store.lineage(checkpoint_id)
        placeholders = ", ".join("?" for _ in chain)
        rows = self.store.db.query(
            "SELECT id, run_id, branch_id, checkpoint_id, content, embedding, created_at"
            f" FROM agent_memory WHERE tombstone = 0 AND checkpoint_id IN ({placeholders})"
            " ORDER BY created_at",
            [c.id for c in chain],
        )
        return [
            MemoryRecord(
                id=r[0],
                run_id=r[1],
                branch_id=r[2],
                checkpoint_id=r[3],
                content=r[4],
                embedding=json.loads(r[5]),
                created_at=r[6],
            )
            for r in rows
        ]

    def recall(self, checkpoint_id: str, query: str, k: int = 5) -> list[RecallHit]:
        """Semantic recall as of a checkpoint — the agent's memory *at that time*.

        On CockroachDB the search runs in the database against the distributed
        vector index (cosine distance, ``<=>``), then results are scoped to
        the checkpoint's lineage so a rewind also rewinds what is recallable.
        Local SQLite backend falls back to in-process cosine.
        """
        query_vec = self.embedder(query)
        if self.store.db.dialect == "postgres":
            try:
                return self._recall_indexed(checkpoint_id, query_vec, k)
            except Exception:  # noqa: BLE001 - e.g. vanilla Postgres without vectors
                pass
        hits = [
            RecallHit(record=m, score=cosine(query_vec, m.embedding))
            for m in self.memories_at(checkpoint_id)
        ]
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]

    def _recall_indexed(
        self, checkpoint_id: str, query_vec: list[float], k: int
    ) -> list[RecallHit]:
        """ANN search in CockroachDB, post-filtered to the exact lineage.

        The vector index is prefixed by run_id, so the ANN search stays inside
        this run; we over-fetch, then keep only memories on the checkpoint's
        ancestor chain (abandoned-branch memories must not leak back in).
        """
        chain = self.store.lineage(checkpoint_id)
        keep = {c.id for c in chain}
        run_id = chain[-1].run_id
        rows = self.store.db.query(
            "SELECT id, run_id, branch_id, checkpoint_id, content, embedding, created_at,"
            " embedding <=> ?::VECTOR AS distance"
            " FROM agent_memory WHERE run_id = ? AND tombstone = 0"
            " ORDER BY embedding <=> ?::VECTOR LIMIT ?",
            (json.dumps(query_vec), run_id, json.dumps(query_vec), max(k * 8, 32)),
        )
        hits = [
            RecallHit(
                record=MemoryRecord(
                    id=r[0],
                    run_id=r[1],
                    branch_id=r[2],
                    checkpoint_id=r[3],
                    content=r[4],
                    embedding=json.loads(r[5]),
                    created_at=r[6],
                ),
                score=1.0 - float(r[7]),
            )
            for r in rows
            if r[3] in keep
        ]
        return hits[:k]

    def recall_on_branch(self, branch_id: str, query: str, k: int = 5) -> list[RecallHit]:
        branch = self.store.get_branch(branch_id)
        return self.recall(branch.head_checkpoint_id, query, k=k)
