"""Idempotent indexing of completed benchmark dialogue, never synthetic embeddings."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from tracemind.benchmark.builder import read
from tracemind.jobs import digest
from tracemind.retrieval.models import EMBEDDING_REVISION
from tracemind.storage.repository import EvidenceRecord

BASE_TIME = datetime(2020, 1, 1, tzinfo=timezone.utc)


def collect_records(root: Path) -> list[EvidenceRecord]:
    result = []
    seen = set()
    for path in sorted((root / "dialogues").glob("*/S*.json")):
        session = read(path)
        if not session.get("complete"):
            continue
        # Gemini production indexing accepts only reviewed sources and no descendants
        # of an unresolved rejection. Legacy test fixtures/archives retain their old contract.
        manifest = root / "dataset.json"
        if manifest.exists() and read(manifest).get("namespace") == "gemini_v1":
            from tracemind.benchmark.review_resolution import effective_approval

            if not effective_approval(root, session):
                continue
            previous = [
                read(p) for p in path.parent.glob("S*.json") if int(p.stem[1:]) < session["session"]
            ]
            if any(not effective_approval(root, older) for older in previous):
                continue
        rows = session["utterances"]
        if len(rows) != 16 or not isinstance(session["session"], int) or session["session"] < 1:
            raise ValueError(f"invalid completed session: {path}")
        for turn, row in enumerate(rows, 1):
            if (
                row.get("role") != ("client" if turn % 2 else "counselor")
                or not row.get("text")
                or not row.get("job_id")
                or not row.get("result_sha256")
            ):
                raise ValueError(f"invalid utterance provenance or role: {path}")
            if row["result_sha256"] != digest({"text": row["text"]}):
                raise ValueError(f"utterance result hash differs: {row['id']}")
            if row["id"] in seen:
                raise ValueError(f"duplicate evidence ID: {row['id']}")
            seen.add(row["id"])
            timestamp = BASE_TIME + timedelta(days=session["session"] - 1, seconds=turn)
            metadata = {
                "text_sha256": digest(row["text"]),
                "job_id": row["job_id"],
                "result_sha256": row["result_sha256"],
                "embedding_model": "BAAI/bge-m3",
                "embedding_revision": EMBEDDING_REVISION,
                "embedding_dimension": 1024,
                "benchmark_time": "synthetic relative session time",
            }
            result.append(
                EvidenceRecord(
                    row["id"],
                    session["case_id"],
                    session["session"],
                    turn,
                    row["role"],
                    row["text"],
                    timestamp,
                    timestamp,
                    metadata,
                )
            )
    return result


def index_dialogues(root: Path, repository, models) -> dict:
    records = collect_records(root)
    existing = repository.existing([record.id for record in records])
    missing = []
    for record in records:
        previous = existing.get(record.id)
        if previous is not None and previous != record:
            raise ValueError(f"existing evidence differs: {record.id}")
        if previous is None:
            missing.append(record)
    if not missing:
        return {"existing": len(records), "inserted": 0}
    vectors = models.embed([record.text for record in missing])
    if len(vectors) != len(missing) or any(len(vector) != 1024 for vector in vectors):
        raise ValueError("embedding result count or dimensions are invalid")
    inserted = sum(
        repository.insert_if_absent(record, vector) for record, vector in zip(missing, vectors)
    )
    return {"existing": len(records) - len(missing) + len(missing) - inserted, "inserted": inserted}
