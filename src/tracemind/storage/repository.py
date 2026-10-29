"""PostgreSQL evidence storage with case-scoped historical visibility."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    id: str
    case_id: str
    session: int
    turn: int
    role: str
    text: str
    occurred_at: datetime
    observed_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


def migrate(dsn: str) -> None:
    """Apply the idempotent schema, committing on success."""
    import psycopg

    sql = (Path(__file__).parent.parent / "migrations" / "001_evidence.sql").read_text(
        encoding="utf-8-sig"
    )
    with psycopg.connect(dsn) as connection:
        connection.execute(sql)


def _check_cutoff(cutoff: tuple[int, int]) -> None:
    if len(cutoff) != 2 or any(not isinstance(n, int) or n < 0 for n in cutoff):
        raise ValueError("cutoff must be a pair of nonnegative integers")


def _check_as_of(as_of: datetime) -> None:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")


class EvidenceRepository:
    """Open a short-lived connection per operation; inserts commit atomically."""

    def __init__(self, dsn: str):
        self.dsn = dsn

    def insert(self, record: EvidenceRecord, embedding: Sequence[float]) -> None:
        import psycopg
        from psycopg.types.json import Jsonb

        if not record.id or not record.case_id or not record.text.strip():
            raise ValueError("id, case_id and text must not be empty")
        _check_cutoff((record.session, record.turn))
        if any(
            t.tzinfo is None or t.utcoffset() is None
            for t in (record.occurred_at, record.observed_at)
        ):
            raise ValueError("timestamps must be timezone-aware")
        if len(embedding) != 1024:
            raise ValueError("BGE-M3 embeddings must have 1024 dimensions")
        # Explicit text representation avoids driver adapter registration requirements.
        vector = "[" + ",".join(str(float(v)) for v in embedding) + "]"
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                """INSERT INTO evidence
                (id, case_id, session, turn, role, text, occurred_at, observed_at, metadata, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)""",
                (
                    record.id,
                    record.case_id,
                    record.session,
                    record.turn,
                    record.role,
                    record.text,
                    record.occurred_at,
                    record.observed_at,
                    Jsonb(record.metadata),
                    vector,
                ),
            )

    def existing(self, ids: Sequence[str]) -> dict[str, EvidenceRecord]:
        import psycopg

        if not ids:
            return {}
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                "SELECT id,case_id,session,turn,role,text,occurred_at,observed_at,metadata FROM evidence WHERE id = ANY(%s)",
                (list(ids),),
            ).fetchall()
        return {row[0]: EvidenceRecord(*row) for row in rows}

    def insert_if_absent(self, record: EvidenceRecord, embedding: Sequence[float]) -> bool:
        import math

        import psycopg
        from psycopg.types.json import Jsonb

        if len(embedding) != 1024 or any(not math.isfinite(float(x)) for x in embedding):
            raise ValueError("embedding must contain 1024 finite values")
        vector = "[" + ",".join(str(float(x)) for x in embedding) + "]"
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                """INSERT INTO evidence (id,case_id,session,turn,role,text,occurred_at,observed_at,metadata,embedding)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector) ON CONFLICT (id) DO NOTHING RETURNING id""",
                (
                    record.id,
                    record.case_id,
                    record.session,
                    record.turn,
                    record.role,
                    record.text,
                    record.occurred_at,
                    record.observed_at,
                    Jsonb(record.metadata),
                    vector,
                ),
            ).fetchone()
            if row:
                return True
            existing = conn.execute(
                "SELECT id,case_id,session,turn,role,text,occurred_at,observed_at,metadata FROM evidence WHERE id=%s",
                (record.id,),
            ).fetchone()
            if not existing or EvidenceRecord(*existing) != record:
                raise ValueError(f"existing evidence differs: {record.id}")
            return False

    def visible(
        self, case_id: str, cutoff: tuple[int, int], as_of: datetime
    ) -> list[EvidenceRecord]:
        import psycopg

        _check_cutoff(cutoff)
        _check_as_of(as_of)
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                """SELECT id, case_id, session, turn, role, text, occurred_at, observed_at, metadata
                FROM evidence WHERE case_id = %s AND (session, turn) <= (%s, %s) AND observed_at <= %s AND occurred_at <= %s
                ORDER BY session, turn, id""",
                (case_id, *cutoff, as_of, as_of),
            ).fetchall()
        return [EvidenceRecord(*row) for row in rows]

    def dense_search(
        self,
        embedding: Sequence[float],
        case_id: str,
        cutoff: tuple[int, int],
        as_of: datetime,
        limit: int,
    ) -> list[EvidenceRecord]:
        import psycopg

        _check_cutoff(cutoff)
        _check_as_of(as_of)
        if len(embedding) != 1024 or limit <= 0:
            raise ValueError("expected a 1024-dimensional embedding and positive limit")
        vector = "[" + ",".join(str(float(v)) for v in embedding) + "]"
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                """SELECT id, case_id, session, turn, role, text, occurred_at, observed_at, metadata
                FROM evidence WHERE case_id = %s AND (session, turn) <= (%s, %s) AND observed_at <= %s AND occurred_at <= %s
                ORDER BY embedding <=> %s::vector, id LIMIT %s""",
                (case_id, *cutoff, as_of, as_of, vector, limit),
            ).fetchall()
        return [EvidenceRecord(*row) for row in rows]
