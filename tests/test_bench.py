import tempfile
from pathlib import Path

import pytest

from src.mm.bench import QueryOutcome, build_index, format_report, load_queries, recall_at_k, resolve_gold
from src.mm.embed import LocalHashEmbedder


@pytest.fixture(scope="module")
def built_index():
    with tempfile.TemporaryDirectory() as scratch:
        yield build_index(Path(scratch) / "chroma", LocalHashEmbedder())


def test_benchmark_has_at_least_30_labelled_queries():
    queries = load_queries()
    assert len(queries) >= 30
    assert len({q["query_id"] for q in queries}) == len(queries)
    for q in queries:
        assert {"query_id", "company_id", "query", "gold_phrase", "label_status"} <= q.keys()
        assert q["label_status"] == "self-drafted"


def test_every_gold_label_resolves_in_its_company(built_index):
    for q in load_queries():
        gold = resolve_gold(built_index, q["company_id"], q["gold_phrase"])
        assert gold, f"{q['query_id']} gold phrase not found in {q['company_id']}"


def test_gold_labels_never_resolve_in_another_company(built_index):
    for q in load_queries():
        other = next(c for c in ("comp_cloudvane_01", "comp_pulsepeak_02", "comp_terraroast_03") if c != q["company_id"])
        assert not resolve_gold(built_index, other, q["gold_phrase"]), q["query_id"]


def _outcome(qid: str, resolved: bool, baseline: bool, multi: bool) -> QueryOutcome:
    return QueryOutcome(
        query_id=qid, company_id="comp_x", query="q", gold_ids={"comp_x:0123456789ab:0"} if resolved else set(),
        resolved=resolved, baseline_hit=baseline, multi_hit=multi,
    )


def test_recall_ignores_unresolved_labels():
    outcomes = [
        _outcome("1", True, True, True),
        _outcome("2", True, False, True),
        _outcome("3", False, False, False),
    ]
    assert recall_at_k(outcomes, "baseline_hit") == pytest.approx(0.5)
    assert recall_at_k(outcomes, "multi_hit") == pytest.approx(1.0)


def test_recall_is_zero_when_nothing_resolves():
    assert recall_at_k([_outcome("1", False, False, False)], "baseline_hit") == 0.0


def test_report_states_label_status_and_target():
    outcomes = [_outcome("1", True, True, True), _outcome("2", True, False, True)]
    report = format_report(outcomes, (10, 10), "local-hash-256", "cross-encoder/ms-marco-MiniLM-L-6-v2")
    assert "Recall@5" in report
    assert "self-drafted" in report
    assert "0.85" in report
    assert "Missed by both" not in report
