from pathlib import Path

import pytest

from src.mm.embed import LocalHashEmbedder, OpenRouterEmbedder, make_embedder
from src.mm.index import EmbedderMismatchError, Index, tokenize
from src.mm.ingest import ROOT, ingest_file
from src.mm.models import Passage

A, B, C = "comp_cloudvane_01", "comp_pulsepeak_02", "comp_terraroast_03"


def _all_passages() -> dict[str, list[Passage]]:
    passages: dict[str, list[Passage]] = {}
    for source in (ROOT / "data_input").rglob("*"):
        if source.is_file() and source.suffix.lower() in {".txt", ".md"}:
            result = ingest_file(source, source.parent.name)
            assert result.error is None
            passages.setdefault(source.parent.name, []).extend(result.passages)
    return passages


@pytest.fixture()
def index(tmp_path: Path) -> Index:
    idx = Index(LocalHashEmbedder(), persist_dir=tmp_path / "chroma")
    for company_id, passages in _all_passages().items():
        idx.index_passages(passages)
    return idx


def test_tokenizer_keeps_model_codes_and_splits_hyphens():
    tokens = tokenize("MOSETA Moto pi ODS, MW-1500W, solar-hybrid")
    assert {"moseta", "moto", "ods", "mw", "1500w", "solar", "hybrid"} <= set(tokens)


def test_local_embedder_is_deterministic_and_normalised():
    embedder = LocalHashEmbedder(dim=64)
    first = embedder.embed_query("solar hybrid cooling")
    second = embedder.embed_query("solar hybrid cooling")
    assert first == second
    assert len(first) == 64
    assert abs(sum(v * v for v in first) - 1.0) < 1e-9


def test_dense_results_stay_inside_the_queried_company(index: Index):
    hits = index.search_dense(A, "solar hybrid cooling for offices", n=50)
    assert hits
    assert all(pid.startswith(f"{A}:") for pid, _ in hits)


def test_lexical_results_stay_inside_the_queried_company(index: Index):
    hits = index.search_lexical(A, "solar hybrid cooling", n=50)
    assert hits
    assert all(pid.startswith(f"{A}:") for pid, _ in hits)


def test_term_unique_to_another_tenant_does_not_leak(index: Index):
    assert index.search_lexical(A, "sauna") == []
    assert index.search_lexical(B, "sauna")
    assert all(pid.startswith(f"{B}:") for pid, _ in index.search_lexical(B, "sauna"))


def test_in_brand_term_returns_results_in_both_indexes(index: Index):
    assert index.search_lexical(C, "Yirgacheffe")
    assert index.search_dense(C, "Yirgacheffe coffee beans")


def test_unknown_company_returns_empty(index: Index):
    assert index.search_dense("comp_missing_99", "anything") == []
    assert index.search_lexical("comp_missing_99", "anything") == []


def test_reindexing_same_passages_is_idempotent(index: Index):
    before = sorted(p.passage_id for p in index.passages_for(A))
    index.index_passages(_all_passages()[A])
    after = sorted(p.passage_id for p in index.passages_for(A))
    assert before == after


def test_edited_source_retires_old_passage_ids(tmp_path: Path):
    idx = Index(LocalHashEmbedder(), persist_dir=tmp_path / "chroma")
    original = ingest_file(ROOT / "data_input" / C / "bean_origins_and_catalog.md", C).passages
    idx.index_passages(original)
    edited_text = "Edited single-line source about Yirgacheffe only."
    from src.mm.ingest import build_passages
    edited = build_passages(edited_text, C, "bean_origins_and_catalog.md")
    idx.index_passages(edited)
    ids = {p.passage_id for p in idx.passages_for(C)}
    assert ids == {p.passage_id for p in edited}
    assert not ids & {p.passage_id for p in original}


def test_index_survives_reopen(tmp_path: Path):
    persist = tmp_path / "chroma"
    first = Index(LocalHashEmbedder(), persist_dir=persist)
    first.index_passages(_all_passages()[B])
    reopened = Index(LocalHashEmbedder(), persist_dir=persist)
    assert reopened.search_lexical(B, "sauna")
    assert reopened.search_dense(B, "sauna recovery")


def test_mismatched_embedder_is_refused(tmp_path: Path):
    persist = tmp_path / "chroma"
    Index(LocalHashEmbedder(dim=256), persist_dir=persist)
    with pytest.raises(EmbedderMismatchError):
        Index(LocalHashEmbedder(dim=128), persist_dir=persist)


def test_multi_company_batch_is_rejected(tmp_path: Path):
    idx = Index(LocalHashEmbedder(), persist_dir=tmp_path / "chroma")
    mixed = _all_passages()[A][:1] + _all_passages()[B][:1]
    with pytest.raises(ValueError):
        idx.index_passages(mixed)


def test_make_embedder_defaults_to_local_without_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert isinstance(make_embedder(), LocalHashEmbedder)


def test_openrouter_embedder_requires_key():
    with pytest.raises(ValueError):
        OpenRouterEmbedder("")
