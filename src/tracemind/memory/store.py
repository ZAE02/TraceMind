"""Append-only memory storage interface and an in-memory implementation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from threading import RLock
from typing import Protocol

from .models import MemoryRevision
from .validation import validate_revision


class MemoryStore(Protocol):
    def history(self, subject_id: str) -> list[MemoryRevision]: ...
    def append(self, revision: MemoryRevision) -> None: ...


class InMemoryMemoryStore:
    def __init__(self) -> None:
        self._history: dict[str, list[MemoryRevision]] = {}
        self._lock = RLock()

    def history(self, subject_id: str) -> list[MemoryRevision]:
        with self._lock:
            return list(self._history.get(subject_id, ()))

    def append(self, revision: MemoryRevision) -> None:
        with self._lock:
            history = self._history.get(revision.subject_id, [])
            if any(item.id == revision.id for item in history):
                if history[-1] == revision:
                    return  # safe replay after an interrupted persist
                raise ValueError("revision ID already exists")
            validate_revision(revision, history[-1] if history else None)
            self._history.setdefault(revision.subject_id, []).append(revision)


class PostgresMemoryStore:
    """Postgres-backed immutable snapshots with per-subject transactional locking."""

    def __init__(self, connection_string: str) -> None:
        self.connection_string = connection_string

    def setup(self) -> None:
        import psycopg

        with psycopg.connect(self.connection_string) as connection:
            migration = Path(__file__).resolve().parent.parent / "migrations" / "002_memory.sql"
            connection.execute(migration.read_text(encoding="utf-8"))

    def history(self, subject_id: str) -> list[MemoryRevision]:
        import psycopg

        with psycopg.connect(self.connection_string) as connection:
            rows = connection.execute(
                "SELECT snapshot, previous_hash, snapshot_hash FROM tracemind_memory_revisions WHERE subject_id = %s ORDER BY revision",
                (subject_id,),
            ).fetchall()
            previous_hash = None
            revisions = []
            for snapshot, linked_hash, snapshot_hash in rows:
                if linked_hash != previous_hash or _snapshot_hash(snapshot) != snapshot_hash:
                    raise ValueError("memory revision hash chain is invalid")
                revisions.append(MemoryRevision.from_dict(snapshot))
                previous_hash = snapshot_hash
            return revisions

    def append(self, revision: MemoryRevision) -> None:
        import psycopg
        from psycopg.types.json import Jsonb

        with psycopg.connect(self.connection_string) as connection:
            # Serializes even concurrent first writes for the same subject.
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (revision.subject_id,)
            )
            row = connection.execute(
                "SELECT snapshot, snapshot_hash FROM tracemind_memory_revisions WHERE subject_id = %s ORDER BY revision DESC LIMIT 1",
                (revision.subject_id,),
            ).fetchone()
            if row and _snapshot_hash(row[0]) != row[1]:
                raise ValueError("latest memory revision hash is invalid")
            previous = MemoryRevision.from_dict(row[0]) if row else None
            if previous and previous.id == revision.id:
                if previous == revision:
                    return
                raise ValueError("revision ID already exists")
            validate_revision(revision, previous)
            snapshot = revision.to_dict()
            connection.execute(
                "INSERT INTO tracemind_memory_revisions (subject_id, revision, id, snapshot, previous_hash, snapshot_hash) VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    revision.subject_id,
                    revision.revision,
                    revision.id,
                    Jsonb(snapshot),
                    row[1] if row else None,
                    _snapshot_hash(snapshot),
                ),
            )


def _snapshot_hash(snapshot: dict) -> str:
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
