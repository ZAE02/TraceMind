"""Memory validation enforces provenance and explicit corrections."""

from __future__ import annotations

from datetime import datetime

from .models import Kind, MemoryRevision, Status


class MemoryValidationError(ValueError):
    pass


def _time(value: str | None, field: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise MemoryValidationError(f"invalid {field}: {value!r}") from exc
    if parsed.utcoffset() is None:
        raise MemoryValidationError(f"{field} must include a timezone")
    return parsed


def validate_revision(
    revision: MemoryRevision,
    previous: MemoryRevision | None = None,
    *,
    allowed_evidence_ids: set[str] | None = None,
) -> None:
    if not revision.subject_id or not revision.id:
        raise MemoryValidationError("revision and subject IDs are required")
    _time(revision.created_at, "created_at")
    if previous is None:
        if revision.revision != 1 or revision.previous_revision_id is not None:
            raise MemoryValidationError("first revision must start at 1")
    elif (
        revision.subject_id != previous.subject_id
        or revision.revision != previous.revision + 1
        or revision.previous_revision_id != previous.id
    ):
        raise MemoryValidationError("revision must extend the current subject revision")
    evidence = {e.id: e for e in revision.evidence}
    statements = {s.id: s for s in revision.statements}
    if len(evidence) != len(revision.evidence) or len(statements) != len(revision.statements):
        raise MemoryValidationError("duplicate IDs")
    if previous:
        for old in previous.evidence:
            if evidence.get(old.id) != old:
                raise MemoryValidationError("previous evidence must be retained unchanged")
        for old in previous.statements:
            if statements.get(old.id) != old:
                raise MemoryValidationError(
                    "previous statements must be retained unchanged; append a correction"
                )
    if allowed_evidence_ids is not None and not set(evidence).issubset(allowed_evidence_ids):
        raise MemoryValidationError("evidence contains IDs outside the allowed set")
    for item in revision.evidence:
        if not item.id or not item.text or not item.source:
            raise MemoryValidationError("evidence requires ID, text and source")
        _time(item.observed_at, "observed_at")
        _time(item.recorded_at, "recorded_at")
        start, end = _time(item.valid_from, "valid_from"), _time(item.valid_to, "valid_to")
        if start and end and end < start:
            raise MemoryValidationError("valid_to precedes valid_from")
    for item in revision.statements:
        if not item.id or not item.text:
            raise MemoryValidationError("statement requires ID and text")
        support, refute = set(item.support_ids), set(item.refute_ids)
        if not (support | refute).issubset(evidence):
            raise MemoryValidationError("statement references unknown evidence")
        if support & refute:
            raise MemoryValidationError("same evidence cannot support and refute a statement")
        if item.status == Status.SUPPORTED and not support:
            raise MemoryValidationError("supported statement needs supporting evidence")
        if (
            item.status == Status.SUPPORTED
            and item.kind in (Kind.PSYCHOLOGICAL_STATE, Kind.HYPOTHESIS)
            and not any(evidence[key].source.lower() in ("client", "user") for key in support)
        ):
            raise MemoryValidationError(
                "supported psychological claims require client or user evidence"
            )
        if item.status == Status.CHALLENGED and not refute:
            raise MemoryValidationError("challenged statement needs refuting evidence")
        if item.status == Status.SUPERSEDED and not item.supersedes:
            raise MemoryValidationError("superseded record must identify what it supersedes")
        if item.id in item.supersedes or not set(item.supersedes).issubset(statements):
            raise MemoryValidationError("correction references unknown or identical statement")
        start, end = _time(item.valid_from, "valid_from"), _time(item.valid_to, "valid_to")
        if start and end and end < start:
            raise MemoryValidationError("valid_to precedes valid_from")
    # Existing records cannot be rewritten: a correction is a new record referencing the old one.
    old_ids = {s.id for s in previous.statements} if previous else set()
    for item in revision.statements:
        if (
            item.id not in old_ids
            and item.supersedes
            and not set(item.supersedes).issubset(old_ids)
        ):
            raise MemoryValidationError("corrections must reference earlier statements")
