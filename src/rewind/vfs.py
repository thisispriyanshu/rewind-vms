"""Virtual file system: versioned files the agent can safely mutate.

The agent's "disk" is rows in the database, not the real filesystem, which is
what makes file writes rewindable. Content lives inline for local dev; in
production large blobs go to S3 and ``blob_ref`` holds the pointer while the
row keeps the versioned metadata (that split is why ``content`` is nullable).

Reads resolve exactly like state: the latest write for a path along the
checkpoint's lineage wins, and a tombstone row means "deleted here" while
older checkpoints still see the file.
"""

from __future__ import annotations

from dataclasses import dataclass

from rewind.store import RewindStore


@dataclass(frozen=True)
class VfsFile:
    path: str
    size: int
    checkpoint_id: str  # checkpoint whose commit last wrote this path
    content: bytes | None


class VFS:
    def __init__(self, store: RewindStore) -> None:
        self.store = store

    def _resolve(self, checkpoint_id: str) -> dict[str, VfsFile]:
        chain = self.store.lineage(checkpoint_id)
        order = {c.id: i for i, c in enumerate(chain)}
        placeholders = ", ".join("?" for _ in chain)
        rows = self.store.db.query(
            "SELECT checkpoint_id, path, content, size, tombstone FROM vfs_objects"
            f" WHERE checkpoint_id IN ({placeholders})",
            [c.id for c in chain],
        )
        rows.sort(key=lambda r: order[r[0]])
        files: dict[str, VfsFile] = {}
        for ckpt_id, path, content, size, tombstone in rows:
            if tombstone:
                files.pop(path, None)
            else:
                data = bytes(content) if content is not None else None
                files[path] = VfsFile(path=path, size=size, checkpoint_id=ckpt_id, content=data)
        return files

    def read(self, checkpoint_id: str, path: str) -> bytes:
        files = self._resolve(checkpoint_id)
        if path not in files:
            raise FileNotFoundError(f"{path!r} does not exist at checkpoint {checkpoint_id}")
        content = files[path].content
        if content is None:
            raise NotImplementedError("blob_ref (S3) content fetch is not wired up yet")
        return content

    def read_text(self, checkpoint_id: str, path: str, encoding: str = "utf-8") -> str:
        return self.read(checkpoint_id, path).decode(encoding)

    def exists(self, checkpoint_id: str, path: str) -> bool:
        return path in self._resolve(checkpoint_id)

    def list(self, checkpoint_id: str, prefix: str = "") -> list[VfsFile]:
        files = self._resolve(checkpoint_id)
        return sorted((f for p, f in files.items() if p.startswith(prefix)), key=lambda f: f.path)

    # Convenience writers — each is one checkpoint commit.

    def write(
        self, branch_id: str, path: str, content: bytes | str, label: str | None = None
    ) -> str:
        """Write one file as its own checkpoint; returns the checkpoint id."""
        data = content.encode() if isinstance(content, str) else content
        ckpt = self.store.create_checkpoint(
            branch_id, files={path: data}, label=label or f"write {path}"
        )
        return ckpt.id

    def delete(self, branch_id: str, path: str, label: str | None = None) -> str:
        ckpt = self.store.create_checkpoint(
            branch_id, files={path: None}, label=label or f"delete {path}"
        )
        return ckpt.id
