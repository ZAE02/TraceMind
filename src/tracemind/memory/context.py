"""Bounded, structured projection and append-only memory patches."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from tracemind.experiments.context import serialized

from .models import MemoryRevision, new_revision
from .validation import validate_revision


def project_memory(
    revision: MemoryRevision | None, tokenizer, budget: int = 4096
) -> dict[str, Any]:
    result = {"statements": [], "evidence": []}
    if revision is None:
        return result
    superseded = {old for statement in revision.statements for old in statement.supersedes}
    eligible = [
        s
        for s in revision.statements
        if s.id not in superseded and s.status.value not in ("superseded", "inapplicable")
    ]

    def priority(s):
        now = datetime.fromisoformat(revision.created_at.replace("Z", "+00:00"))

        def parse(value):
            return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None

        start, end = parse(s.valid_from), parse(s.valid_to)
        applicable = (start is None or start <= now) and (end is None or end >= now)
        return (
            0 if applicable else 1,
            0
            if s.kind.value == "unresolved_question"
            else 1
            if s.status.value == "challenged" or s.refute_ids
            else 2
            if s.kind.value == "hypothesis"
            else 3,
            s.id,
        )

    evidence = {item.id: item for item in revision.evidence}
    for statement in sorted(eligible, key=priority):
        ids = list(dict.fromkeys((*statement.refute_ids, *statement.support_ids)))
        existing = {item["id"] for item in result["evidence"]}
        snippets = []
        for key in ids:
            if key in evidence and key not in existing:
                record = asdict(evidence[key])
                record["text"] = record["text"][:256]
                snippets.append(record)
        candidate = {
            "statements": result["statements"] + [asdict(statement)],
            "evidence": result["evidence"] + snippets,
        }
        if (
            len(tokenizer.encode_tokens(serialized({"memory": candidate, "evidence": []})))
            <= budget
        ):
            result = candidate
    return result


def apply_patch(
    subject_id: str, previous: MemoryRevision | None, patch: dict, allowed_evidence_ids: set[str]
) -> MemoryRevision:
    """Add records while retaining every earlier record unchanged in storage."""
    if (
        not isinstance(patch, dict)
        or not isinstance(patch.get("evidence"), list)
        or not isinstance(patch.get("statements"), list)
    ):
        raise ValueError("patch requires evidence and statements arrays")
    old_evidence = [asdict(e) for e in previous.evidence] if previous else []
    old_statements = [asdict(s) for s in previous.statements] if previous else []
    if {e["id"] for e in old_evidence} & {e["id"] for e in patch["evidence"]} or {
        s["id"] for s in old_statements
    } & {s["id"] for s in patch["statements"]}:
        raise ValueError("patch cannot replace existing records")
    revision = new_revision(
        subject_id,
        previous,
        evidence=old_evidence + patch["evidence"],
        statements=old_statements + patch["statements"],
    )
    validate_revision(
        revision,
        previous,
        allowed_evidence_ids=allowed_evidence_ids | {e["id"] for e in old_evidence},
    )
    return revision


def canonicalize_evidence_patch(patch, records, allowed_ids, *, case_id):
    """Resolve IDs to authoritative records, rejecting model-supplied evidence content."""
    ids = patch.get("evidence")
    if not isinstance(ids, list) or any(not isinstance(item, str) or not item for item in ids):
        raise ValueError("evidence must contain only authoritative evidence IDs")
    if len(set(ids)) != len(ids) or not set(ids).issubset(allowed_ids):
        raise ValueError("duplicate or unavailable evidence IDs")
    if any(key not in records or records[key].case_id != case_id for key in ids):
        raise ValueError("evidence ID does not resolve to the permitted case")
    evidence = []
    for key in ids:
        record = records[key]
        evidence.append(
            {
                "id": record.id,
                "text": record.text,
                "source": record.role,
                "observed_at": record.occurred_at.isoformat(),
                "recorded_at": record.observed_at.isoformat(),
            }
        )
    return {**patch, "evidence": evidence}
