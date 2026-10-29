"""Exercise real models and a disposable PostgreSQL database; no model APIs."""

import json
import os
import time
from datetime import datetime, timezone
from uuid import uuid4

import psycopg

from tracemind.config import LOCAL
from tracemind.config import dsn as configured_dsn
from tracemind.retrieval import BGEModels, Retriever
from tracemind.retrieval.models import EMBEDDING_REVISION, RERANKER_REVISION
from tracemind.storage import EvidenceRecord, EvidenceRepository, migrate


def main():
    dsn = os.environ.get("TRACEMIND_TEST_DSN") or configured_dsn()
    if not dsn:
        raise SystemExit(
            "Set TRACEMIND_TEST_DSN to a disposable PostgreSQL 17 database with pgvector available."
        )
    case, row_id = str(uuid4()), str(uuid4())
    now = datetime.now(timezone.utc)
    models = BGEModels()
    started = time.monotonic()
    migrate(dsn)
    try:
        vectors = models.embed(["The blue fox lives in the forest."])
        assert len(vectors) == 1 and len(vectors[0]) == 1024
        repository = EvidenceRepository(dsn)
        repository.insert(
            EvidenceRecord(
                row_id, case, 1, 1, "user", "The blue fox lives in the forest.", now, now
            ),
            vectors[0],
        )
        result = Retriever(repository, models).search(
            "Where does the blue fox live?", case, (1, 1), now
        )
        assert result and result[0].evidence.id == row_id
        assert len(models.encode_tokens(result[0].evidence.text)) <= 4096
        with psycopg.connect(dsn) as connection:
            postgres_version = connection.execute("SHOW server_version").fetchone()[0]
            vector_version = connection.execute(
                "SELECT extversion FROM pg_extension WHERE extname='vector'"
            ).fetchone()[0]
        proof = {
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "postgresql": postgres_version,
            "pgvector": vector_version,
            "embedding_model": "BAAI/bge-m3",
            "embedding_revision": EMBEDDING_REVISION,
            "reranker_model": "BAAI/bge-reranker-v2-m3",
            "reranker_revision": RERANKER_REVISION,
            "vector_dimension": len(vectors[0]),
            "retrieved_expected_record": True,
            "reranker_score": result[0].score,
            "seconds": round(time.monotonic() - started, 3),
        }
        path = LOCAL / "verification" / "stack.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(proof, indent=2), encoding="utf-8")
        print("PostgreSQL, pgvector, BGE-M3 embedding and reranking verified.")
    finally:
        with psycopg.connect(dsn) as connection:
            connection.execute("DELETE FROM evidence WHERE id = %s", (row_id,))


if __name__ == "__main__":
    main()
