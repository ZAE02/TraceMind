"""Append-only, evidence-grounded memory records."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


class Status(str, Enum):
    PROPOSED = "proposed"
    SUPPORTED = "supported"
    CHALLENGED = "challenged"
    SUPERSEDED = "superseded"
    INAPPLICABLE = "inapplicable"


class Kind(str, Enum):
    EVENT = "event"
    PSYCHOLOGICAL_STATE = "psychological_state"
    HYPOTHESIS = "hypothesis"
    UNRESOLVED_QUESTION = "unresolved_question"


@dataclass(frozen=True)
class Evidence:
    id: str
    text: str
    source: str
    observed_at: str
    recorded_at: str
    valid_from: str | None = None
    valid_to: str | None = None


@dataclass(frozen=True)
class Statement:
    id: str
    kind: Kind
    text: str
    status: Status = Status.PROPOSED
    support_ids: tuple[str, ...] = ()
    refute_ids: tuple[str, ...] = ()
    supersedes: tuple[str, ...] = ()
    valid_from: str | None = None
    valid_to: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", Kind(self.kind))
        object.__setattr__(self, "status", Status(self.status))
        for name in ("support_ids", "refute_ids", "supersedes"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


@dataclass(frozen=True)
class MemoryRevision:
    id: str
    subject_id: str
    revision: int
    created_at: str
    evidence: tuple[Evidence, ...] = ()
    statements: tuple[Statement, ...] = ()
    previous_revision_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryRevision":
        return cls(
            id=data["id"],
            subject_id=data["subject_id"],
            revision=data["revision"],
            created_at=data["created_at"],
            previous_revision_id=data.get("previous_revision_id"),
            evidence=tuple(Evidence(**item) for item in data.get("evidence", ())),
            statements=tuple(Statement(**item) for item in data.get("statements", ())),
        )


def new_revision(
    subject_id: str,
    previous: MemoryRevision | None,
    *,
    evidence: list[dict],
    statements: list[dict],
) -> MemoryRevision:
    return MemoryRevision(
        id=str(uuid4()),
        subject_id=subject_id,
        revision=1 if previous is None else previous.revision + 1,
        previous_revision_id=None if previous is None else previous.id,
        created_at=datetime.now(timezone.utc).isoformat(),
        evidence=tuple(Evidence(**item) for item in evidence),
        statements=tuple(Statement(**item) for item in statements),
    )
