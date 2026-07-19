"""Database backends.

Rewind speaks a deliberately small, portable SQL subset so the same store code
runs against SQLite (zero-setup local dev) and anything PostgreSQL
wire-compatible — most importantly CockroachDB. Queries are written with ``?``
placeholders; the Postgres backend translates them to ``%s``. Because of that,
a literal ``?`` must never appear inside a SQL string.
"""

from __future__ import annotations

import sqlite3
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any


class Database(ABC):
    """Minimal driver wrapper: execute, query, and a transaction scope."""

    dialect: str

    @abstractmethod
    @contextmanager
    def transaction(self) -> Iterator[None]:
        """All writes inside the block commit atomically or not at all."""

    @abstractmethod
    def execute(self, sql: str, params: Sequence[Any] = ()) -> None: ...

    @abstractmethod
    def query(self, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]: ...

    @abstractmethod
    def close(self) -> None: ...


class SQLiteDatabase(Database):
    dialect = "sqlite"

    def __init__(self, path: str = ":memory:") -> None:
        # check_same_thread=False + a lock so the FastAPI server can share one
        # in-memory connection across request threads.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.isolation_level = None  # autocommit; we manage BEGIN explicitly
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.RLock()
        self._depth = 0

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            if self._depth == 0:
                self._conn.execute("BEGIN")
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                if self._depth == 0:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if self._depth == 0:
                    self._conn.execute("COMMIT")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        with self._lock:
            self._conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
        with self._lock:
            return list(self._conn.execute(sql, tuple(params)).fetchall())

    def close(self) -> None:
        self._conn.close()


class PostgresDatabase(Database):
    """PostgreSQL / CockroachDB backend via psycopg (optional extra)."""

    dialect = "postgres"

    def __init__(self, url: str) -> None:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "PostgreSQL/CockroachDB support requires psycopg: "
                'pip install "rewind-agents[postgres]"'
            ) from exc
        self._conn = psycopg.connect(url, autocommit=True)
        self._lock = threading.RLock()
        self._depth = 0

    @staticmethod
    def _translate(sql: str) -> str:
        return sql.replace("?", "%s")

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            if self._depth == 0:
                self._conn.autocommit = False
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                if self._depth == 0:
                    self._conn.rollback()
                    self._conn.autocommit = True
                raise
            else:
                self._depth -= 1
                if self._depth == 0:
                    self._conn.commit()
                    self._conn.autocommit = True

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        with self._lock:
            self._conn.execute(self._translate(sql), tuple(params))

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
        with self._lock:
            cur = self._conn.execute(self._translate(sql), tuple(params))
            return [tuple(row) for row in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()


def connect(url: str | None = None) -> Database:
    """Open a backend from a URL.

    - ``None`` or ``"sqlite:///:memory:"`` — in-memory SQLite
    - ``"sqlite:///path/to.db"`` — file-backed SQLite
    - ``"postgresql://..."`` / ``"cockroachdb://..."`` — psycopg
    """
    if url is None or url == "sqlite:///:memory:":
        return SQLiteDatabase(":memory:")
    if url.startswith("sqlite:///"):
        return SQLiteDatabase(url[len("sqlite:///") :])
    if url.startswith(("postgres://", "postgresql://", "cockroachdb://")):
        return PostgresDatabase(url.replace("cockroachdb://", "postgresql://", 1))
    raise ValueError(f"Unsupported database URL: {url!r}")
