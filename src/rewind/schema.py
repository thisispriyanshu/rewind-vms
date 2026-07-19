"""Append-only schema shared by all backends.

Every table is versioned by ``(branch_id, checkpoint_id)``. Nothing is ever
updated destructively except branch head pointers and effect statuses — history
rows are immutable, which is what makes rewind, branching, and the audit trail
possible. Timestamps are stored as ISO-8601 UTC text for portability; on
CockroachDB, MVCC (`AS OF SYSTEM TIME`) supplies wall-clock time travel
independent of column types.
"""

from __future__ import annotations

from rewind.db import Database

DDL = [
    """
    CREATE TABLE IF NOT EXISTS runs (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        created_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS branches (
        id                        TEXT PRIMARY KEY,
        run_id                    TEXT NOT NULL REFERENCES runs(id),
        name                      TEXT NOT NULL,
        status                    TEXT NOT NULL DEFAULT 'active',
        head_checkpoint_id        TEXT,
        forked_from_checkpoint_id TEXT,
        created_at                TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS checkpoints (
        id                   TEXT PRIMARY KEY,
        run_id               TEXT NOT NULL REFERENCES runs(id),
        branch_id            TEXT NOT NULL REFERENCES branches(id),
        parent_checkpoint_id TEXT,
        step_index           INTEGER NOT NULL,
        label                TEXT,
        created_at           TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS state_kv (
        id            TEXT PRIMARY KEY,
        run_id        TEXT NOT NULL,
        branch_id     TEXT NOT NULL,
        checkpoint_id TEXT NOT NULL REFERENCES checkpoints(id),
        key           TEXT NOT NULL,
        value         TEXT,
        tombstone     INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS vfs_objects (
        id            TEXT PRIMARY KEY,
        run_id        TEXT NOT NULL,
        branch_id     TEXT NOT NULL,
        checkpoint_id TEXT NOT NULL REFERENCES checkpoints(id),
        path          TEXT NOT NULL,
        content       BLOB,
        blob_ref      TEXT,
        size          INTEGER NOT NULL DEFAULT 0,
        tombstone     INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_memory (
        id            TEXT PRIMARY KEY,
        run_id        TEXT NOT NULL,
        branch_id     TEXT NOT NULL,
        checkpoint_id TEXT NOT NULL REFERENCES checkpoints(id),
        content       TEXT NOT NULL,
        embedding     TEXT,
        tombstone     INTEGER NOT NULL DEFAULT 0,
        created_at    TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS staged_effects (
        id              TEXT PRIMARY KEY,
        run_id          TEXT NOT NULL,
        branch_id       TEXT NOT NULL,
        checkpoint_id   TEXT NOT NULL REFERENCES checkpoints(id),
        tool_name       TEXT NOT NULL,
        idempotency_key TEXT,
        payload         TEXT NOT NULL,
        result          TEXT,
        mode            TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'staged',
        created_at      TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_checkpoints_run ON checkpoints(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_state_kv_ckpt ON state_kv(checkpoint_id)",
    "CREATE INDEX IF NOT EXISTS idx_vfs_ckpt ON vfs_objects(checkpoint_id)",
    "CREATE INDEX IF NOT EXISTS idx_memory_ckpt ON agent_memory(checkpoint_id)",
    "CREATE INDEX IF NOT EXISTS idx_effects_ckpt ON staged_effects(checkpoint_id)",
    "CREATE INDEX IF NOT EXISTS idx_effects_idem ON staged_effects(idempotency_key)",
]


def ddl_for(dialect: str) -> list[str]:
    """The DDL is written in the SQLite/portable subset; BLOB is the only
    type name that differs on PostgreSQL/CockroachDB (BYTEA)."""
    if dialect == "postgres":
        return [s.replace("BLOB", "BYTEA") for s in DDL]
    return list(DDL)


def create_schema(db: Database) -> None:
    with db.transaction():
        for statement in ddl_for(db.dialect):
            db.execute(statement)
