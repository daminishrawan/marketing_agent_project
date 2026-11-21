"""Hybrid retrieval: query expansion, dense + BM25 candidates, RRF fusion, cross-encoder rerank."""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import yaml

from src.mm.index import Index
from src.mm.models import ErrorPayload, SearchHit, SearchResult

ROOT = Path(__file__).resolve().parents[2]
MAX_TOP_K = 20
EXPANSION_TEMPLATES = (
    "{query}",
    "{query} pricing terms and offers",
    "{query} restrictions and guidelines",
)


class Reranker(Protocol):
    def predict(self, pairs: list[tuple[str, str]]) -> list[float]: ...


@lru_cache(maxsize=1)
def _retrieval_config() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["retrieval"]


class CrossEncoderReranker:
    """Loads the cross-encoder on first use so importing this module stays cheap."""

    def __init__(self, model_name: str | None = None) -> None:
        self.name = model_name or _retrieval_config()["reranker_model"]
        self._model = None

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.name, device="cpu")
        return [float(score) for score in self._model.predict(pairs)]


_default_reranker: Reranker | None = None


def _get_default_reranker() -> Reranker:
    global _default_reranker
    if _default_reranker is None:
        _default_reranker = CrossEncoderReranker()
    return _default_reranker


def expand_queries(query: str, use_multi_query: bool = True) -> list[str]:
    if not use_multi_query:
        return [query]
    count = _retrieval_config()["multi_query_expansion_count"]
    return [template.format(query=query) for template in EXPANSION_TEMPLATES[:count]]


def rrf_fuse(rankings: list[list[str]], k: int) -> list[tuple[str, float]]:
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, passage_id in enumerate(ranking, start=1):
            scores[passage_id] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def hybrid_search(
    index: Index,
    company_id: str,
    query: str,
    top_k: int = 5,
    use_multi_query: bool = True,
    reranker: Reranker | None = None,
) -> SearchResult:
    if not 1 <= top_k <= MAX_TOP_K:
        return SearchResult(error=ErrorPayload(
            error_code="INVALID_TOP_K",
            message=f"top_k={top_k} is outside 1..{MAX_TOP_K}",
            remediation_hint=f"Use a top_k between 1 and {MAX_TOP_K}",
        ))

    cfg = _retrieval_config()
    pool = cfg["candidate_pool_size"]
    rankings: list[list[str]] = []
    for variant in expand_queries(query, use_multi_query):
        rankings.append([pid for pid, _ in index.search_dense(company_id, variant, pool)])
        rankings.append([pid for pid, _ in index.search_lexical(company_id, variant, pool)])

    fused = rrf_fuse([ranking for ranking in rankings if ranking], cfg["rrf_k"])
    lookup = {p.passage_id: p for p in index.passages_for(company_id)}
    candidates = [(pid, score) for pid, score in fused if pid in lookup][: cfg["rerank_candidate_count"]]
    if not candidates:
        return SearchResult()

    rerank_scores = (reranker or _get_default_reranker()).predict(
        [(query, lookup[pid].text) for pid, _ in candidates]
    )
    ranked = sorted(
        zip(candidates, rerank_scores),
        key=lambda item: (-item[1], -item[0][1], item[0][0]),
    )[:top_k]
    hits = [
        SearchHit(passage_id=pid, score=float(score), passage=lookup[pid])
        for (pid, _), score in ranked
    ]
    return SearchResult(hits=hits, total_found=len(hits))
