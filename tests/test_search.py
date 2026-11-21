from pathlib import Path

import pytest

from src.mm.embed import LocalHashEmbedder
from src.mm.index import Index
from src.mm.ingest import ROOT, ingest_file
from src.mm.models import Passage
from src.mm.search import CrossEncoderReranker, expand_queries, hybrid_search, rrf_fuse

A, B, C = "comp_cloudvane_01", "comp_pulsepeak_02", "comp_terraroast_03"


class FakeReranker:
    def __init__(self, scores_by_text: dict[str, float] | None = None) -> None:
        self.scores_by_text = scores_by_text or {}
        self.calls = 0

    def predict(self, pairs):
        self.calls += 1
        return [self.scores_by_text.get(text, 0.0) for _, text in pairs]


class FakeIndex:
    def __init__(self, passages: list[Passage], dense: list[str], lexical: list[str]) -> None:
        self._passages = passages
        self._dense = dense
        self._lexical = lexical
        self.dense_queries: list[str] = []
        self.lexical_queries: list[str] = []

    def search_dense(self, company_id, query, n=50):
        self.dense_queries.append(query)
        return [(pid, 1.0) for pid in self._dense[:n]]

    def search_lexical(self, company_id, query, n=50):
        self.lexical_queries.append(query)
        return [(pid, 1.0) for pid in self._lexical[:n]]

    def passages_for(self, company_id):
        return list(self._passages)


def _passage(pid: str, text: str, company_id: str = A) -> Passage:
    return Passage(
        passage_id=pid, company_id=company_id, text=text, source_ref="f.txt",
        source_sha12="0123456789ab", chunk_index=int(pid.rsplit(":", 1)[1]),
    )


def test_rrf_matches_hand_computed_scores():
    fused = rrf_fuse([["a", "b"], ["c", "a"]], k=60)
    scores = dict(fused)
    assert scores["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert scores["c"] == pytest.approx(1 / 61)
    assert scores["b"] == pytest.approx(1 / 62)
    assert [pid for pid, _ in fused] == ["a", "c", "b"]


def test_rrf_orders_by_score_not_by_identifier():
    fused = rrf_fuse([["z"], ["z", "a"]], k=60)
    assert [pid for pid, _ in fused] == ["z", "a"]


def test_expand_queries_produces_three_variants_and_can_be_disabled():
    variants = expand_queries("solar cooling")
    assert variants == [
        "solar cooling",
        "solar cooling pricing terms and offers",
        "solar cooling restrictions and guidelines",
    ]
    assert expand_queries("solar cooling", use_multi_query=False) == ["solar cooling"]


def test_multi_query_runs_three_variants_on_both_lists():
    passages = [_passage(f"{A}:0123456789ab:0", "solar cooling")]
    index = FakeIndex(passages, dense=[passages[0].passage_id], lexical=[passages[0].passage_id])
    hybrid_search(index, A, "solar cooling", use_multi_query=True, reranker=FakeReranker())
    assert len(index.dense_queries) == 3
    assert len(index.lexical_queries) == 3


def test_single_query_mode_runs_one_variant():
    passages = [_passage(f"{A}:0123456789ab:0", "solar cooling")]
    index = FakeIndex(passages, dense=[passages[0].passage_id], lexical=[])
    hybrid_search(index, A, "solar cooling", use_multi_query=False, reranker=FakeReranker())
    assert index.dense_queries == ["solar cooling"]


def test_final_order_follows_reranker_scores():
    p0 = _passage(f"{A}:0123456789ab:0", "alpha text")
    p1 = _passage(f"{A}:0123456789ab:1", "beta text")
    p2 = _passage(f"{A}:0123456789ab:2", "gamma text")
    index = FakeIndex([p0, p1, p2], dense=[p0.passage_id, p1.passage_id, p2.passage_id], lexical=[])
    reranker = FakeReranker({"alpha text": 0.1, "beta text": 0.9, "gamma text": 0.5})
    result = hybrid_search(index, A, "q", top_k=2, use_multi_query=False, reranker=reranker)
    assert [h.passage_id for h in result.hits] == [p1.passage_id, p2.passage_id]
    assert result.total_found == 2
    assert result.error is None


@pytest.mark.parametrize("bad_k", [0, 21])
def test_top_k_out_of_bounds_returns_structured_error(bad_k):
    index = FakeIndex([], dense=[], lexical=[])
    result = hybrid_search(index, A, "q", top_k=bad_k, reranker=FakeReranker())
    assert result.hits == []
    assert result.error is not None
    assert result.error.error_code == "INVALID_TOP_K"


def test_reranker_is_not_loaded_until_used():
    reranker = CrossEncoderReranker("cross-encoder/ms-marco-MiniLM-L-6-v2")
    assert reranker._model is None


@pytest.fixture()
def real_index(tmp_path: Path) -> Index:
    idx = Index(LocalHashEmbedder(), persist_dir=tmp_path / "chroma")
    for source in (ROOT / "data_input").rglob("*"):
        if source.is_file() and source.suffix.lower() in {".txt", ".md"}:
            idx.index_passages(ingest_file(source, source.parent.name).passages)
    return idx


def test_unknown_company_returns_zero_total_found(real_index):
    result = hybrid_search(real_index, "comp_missing_99", "solar", reranker=FakeReranker())
    assert result.total_found == 0
    assert result.hits == []
    assert result.error is None


def test_search_never_returns_another_tenants_passages(real_index):
    for company_id in (A, B, C):
        for query in ("sauna recovery", "Yirgacheffe coffee", "solar hybrid AC"):
            result = hybrid_search(real_index, company_id, query, reranker=FakeReranker())
            assert all(hit.passage_id.startswith(f"{company_id}:") for hit in result.hits)


def test_tenant_term_does_not_surface_in_other_tenant(real_index):
    result = hybrid_search(real_index, A, "sauna", reranker=FakeReranker())
    assert all(not hit.passage_id.startswith(f"{B}:") for hit in result.hits)


def test_real_pipeline_returns_cited_passage_ids(real_index):
    result = hybrid_search(real_index, C, "Yirgacheffe", top_k=5, reranker=FakeReranker())
    assert result.error is None
    assert result.hits
    for hit in result.hits:
        assert hit.passage_id == hit.passage.passage_id
        assert hit.passage.company_id == C
