"""Case-scoped BM25, dense, reciprocal-rank fusion, and cross-encoder reranking."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol, Sequence

from ..storage import EvidenceRecord, EvidenceRepository


class Models(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...
    def rerank(self, query: str, texts: Sequence[str]) -> list[float]: ...
    def encode_tokens(self, text: str) -> list[int]: ...
    def decode_tokens(self, tokens: Sequence[int]) -> str: ...


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    evidence: EvidenceRecord
    score: float
    truncated: bool = False


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]], k: int = 60
) -> list[tuple[str, float]]:
    if k <= 0:
        raise ValueError("k must be positive")
    scores: dict[str, float] = {}
    for ranking in rankings:
        seen: set[str] = set()
        for rank, key in enumerate(ranking, 1):
            if key not in seen:
                scores[key] = scores.get(key, 0.0) + 1 / (k + rank)
                seen.add(key)
    return sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))


def _tokens(text: str) -> list[str]:
    # Individual CJK characters permit lexical matching without language-specific segmentation.
    return re.findall(r"[\u3400-\u9fff]|[^\W\u3400-\u9fff_]+", text.lower(), re.UNICODE)


def temporal_coverage(
    ranked_ids: list[str], visible: list[EvidenceRecord], limit: int = 30
) -> list[EvidenceRecord]:
    """Fixed pre-evaluation anchors: earliest and latest prior client evidence."""
    if limit < 2:
        raise ValueError("temporal coverage requires room for two anchors")
    by_id = {item.id: item for item in visible}
    clients = sorted(
        (item for item in visible if item.role in ("client", "user")),
        key=lambda x: (x.session, x.turn, x.id),
    )
    selected = []
    anchors = [clients[0].id, clients[-1].id] if clients else []
    for key in anchors + ranked_ids:
        if key in by_id and key not in selected:
            selected.append(key)
        if len(selected) >= limit:
            break
    return [by_id[key] for key in selected]


class Retriever:
    def __init__(
        self,
        repository: EvidenceRepository,
        models: Models,
        candidate_limit: int = 50,
        token_budget: int = 4096,
    ):
        if candidate_limit <= 0 or not 0 < token_budget <= 4096:
            raise ValueError("candidate_limit must be positive and token_budget must be 1..4096")
        self.repository, self.models = repository, models
        self.candidate_limit, self.token_budget = candidate_limit, token_budget

    def search(
        self, query: str, case_id: str, cutoff: tuple[int, int], as_of: datetime, limit: int = 10
    ) -> list[RetrievalResult]:
        from rank_bm25 import BM25Okapi

        if not query.strip() or not case_id or limit <= 0:
            raise ValueError("query, case_id, and positive limit are required")
        records = self.repository.visible(case_id, cutoff, as_of)
        if not records:
            return []
        corpus = [_tokens(record.text) for record in records]
        query_tokens = _tokens(query)
        lexical = []
        if query_tokens and any(corpus):
            scores = BM25Okapi(corpus).get_scores(query_tokens)
            # BM25's IDF can be negative for common terms, so match by token overlap.
            lexical = sorted(
                (i for i, tokens in enumerate(corpus) if set(tokens) & set(query_tokens)),
                key=lambda i: (-float(scores[i]), records[i].id),
            )[: self.candidate_limit]
        dense = self.repository.dense_search(
            self.models.embed([query])[0], case_id, cutoff, as_of, self.candidate_limit
        )
        by_id = {r.id: r for r in records}
        # Guard against stores returning records outside the visible candidate set.
        dense_ids = [r.id for r in dense if r.id in by_id]
        fused = reciprocal_rank_fusion([[records[i].id for i in lexical], dense_ids])[
            : self.candidate_limit
        ]
        candidates = [by_id[key] for key, _ in fused]
        if not candidates:
            return []
        scores = self.models.rerank(query, [r.text for r in candidates])
        if len(scores) != len(candidates):
            raise ValueError("reranker returned a mismatched number of scores")
        ranked = sorted(zip(candidates, scores), key=lambda x: (-x[1], x[0].id))
        results: list[RetrievalResult] = []
        remaining = self.token_budget
        for record, score in ranked[:limit]:
            tokens = self.models.encode_tokens(record.text)
            if not tokens or remaining == 0:
                continue
            truncated = len(tokens) > remaining
            content = self.models.decode_tokens(tokens[:remaining]) if truncated else record.text
            # The budget counts tokenizer tokens in returned evidence text.
            used = len(self.models.encode_tokens(content))
            while used > remaining and tokens:
                tokens = tokens[:-1]
                content = self.models.decode_tokens(tokens[:remaining])
                used = len(self.models.encode_tokens(content))
            if used:
                results.append(
                    RetrievalResult(replace(record, text=content), float(score), truncated)
                )
                remaining -= used
        return results
