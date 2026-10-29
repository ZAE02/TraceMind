import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from tracemind.retrieval import Retriever, reciprocal_rank_fusion
from tracemind.storage import EvidenceRecord, EvidenceRepository, migrate

NOW = datetime.now(timezone.utc)


def record(id, text, case="a", session=1, turn=1, observed=NOW):
    return EvidenceRecord(id, case, session, turn, "user", text, NOW, observed)


class FakeModels:
    def embed(self, texts):
        return [[1.0] + [0.0] * 1023 for _ in texts]

    def rerank(self, query, texts):
        return [float(len(t)) for t in texts]

    def encode_tokens(self, text):
        return list(map(ord, text))

    def decode_tokens(self, tokens):
        return "".join(map(chr, tokens))


class FakeStore:
    def __init__(self):
        self.rows = [record("1", "alpha"), record("2", "alpha longer")]

    def visible(self, case_id, cutoff, as_of):
        return self.rows

    def dense_search(self, embedding, case_id, cutoff, as_of, limit):
        return self.rows[::-1]


class FakeBM25:
    def __init__(self, corpus):
        self.corpus = corpus

    def get_scores(self, query):
        return [sum(t in row for t in query) for row in self.corpus]


class RetrievalTests(unittest.TestCase):
    def test_rrf_deduplicates_and_breaks_ties_deterministically(self):
        result = reciprocal_rank_fusion([["a", "a", "b"], ["b", "a"]])
        self.assertEqual(result[0][0], "a")
        self.assertAlmostEqual(result[0][1], 1 / 61 + 1 / 62)

    def test_retrieval_reranks_and_respects_token_budget(self):
        old = sys.modules.get("rank_bm25")
        sys.modules["rank_bm25"] = types.SimpleNamespace(BM25Okapi=FakeBM25)
        try:
            results = Retriever(FakeStore(), FakeModels(), token_budget=8).search(
                "alpha", "a", (1, 1), NOW
            )
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].evidence.id, "2")
            self.assertEqual(results[0].evidence.text, "alpha lo")
            self.assertTrue(results[0].truncated)
        finally:
            if old is None:
                del sys.modules["rank_bm25"]
            else:
                sys.modules["rank_bm25"] = old


@unittest.skipUnless(
    os.getenv("TRACEMIND_TEST_DSN"), "set TRACEMIND_TEST_DSN to a disposable pgvector database"
)
class PostgresIntegrationTests(unittest.TestCase):
    def test_case_and_temporal_visibility(self):
        import psycopg

        dsn = os.environ["TRACEMIND_TEST_DSN"]
        migrate(dsn)
        repo = EvidenceRepository(dsn)
        case = str(uuid4())
        ids = [str(uuid4()) for _ in range(4)]
        try:
            repo.insert(record(ids[0], "visible", case), [1.0] + [0.0] * 1023)
            repo.insert(record(ids[1], "later turn", case, turn=2), [1.0] + [0.0] * 1023)
            repo.insert(
                record(ids[2], "later observation", case, observed=NOW + timedelta(seconds=1)),
                [1.0] + [0.0] * 1023,
            )
            repo.insert(record(ids[3], "other case", case + "other"), [1.0] + [0.0] * 1023)
            self.assertEqual([r.id for r in repo.visible(case, (1, 1), NOW)], [ids[0]])
            self.assertEqual(
                [r.id for r in repo.dense_search([1.0] + [0.0] * 1023, case, (1, 1), NOW, 10)],
                [ids[0]],
            )
        finally:
            with psycopg.connect(dsn) as conn:
                conn.execute("DELETE FROM evidence WHERE id = ANY(%s)", (ids,))


if __name__ == "__main__":
    unittest.main()
