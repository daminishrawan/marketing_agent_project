import json
import tempfile
from pathlib import Path

import pytest

from src.mm.bench import build_index
from src.mm.cascade import NullTraceSink, purge_company_data, purge_lead
from src.mm.campaign_checks import seed_all
from src.mm.embed import LocalHashEmbedder
from src.mm.index import Index
from src.mm.ingest import ROOT
from src.mm.records import connect, create_lead
from src.mm.search import hybrid_search

A, B, C = "comp_cloudvane_01", "comp_pulsepeak_02", "comp_terraroast_03"
KEY = "receipt-signing-key-test-only"


class FakeTraces:
    def __init__(self) -> None:
        self.tagged = {B: 3}

    def delete_tagged(self, company_id: str) -> int:
        return self.tagged.pop(company_id, 0)

    def remaining_tagged(self, company_id: str) -> int:
        return self.tagged.get(company_id, 0)


@pytest.fixture()
def world(tmp_path):
    conn = connect(tmp_path / "records.db")
    seed_all(conn)
    index = build_index(tmp_path / "chroma", LocalHashEmbedder())
    lead_ids = {}
    for company, lead in [(B, "Looking for a sauna membership."), (B, "Second lead about pricing."),
                          (C, "Wholesale price list for cafes.")]:
        result = create_lead(conn, company, "web_form", lead, "j***@example.com", True)
        lead_ids.setdefault(company, []).append(result.lead.lead_id)
    yield conn, index, lead_ids
    conn.close()


def _relational_count(conn, company):
    return sum(conn.execute(f"SELECT COUNT(*) FROM {t} WHERE company_id = ?", (company,)).fetchone()[0]
               for t in ("customer_leads", "products", "product_offers", "campaign_dispatches"))


def test_purge_removes_the_company_from_every_built_layer(world):
    conn, index, _ = world
    assert _relational_count(conn, B) > 0
    assert index.count_company(B) > 0
    receipt = purge_company_data(conn, index, B, trace_sink=FakeTraces(), signing_key=KEY)
    assert receipt.verified is True
    assert _relational_count(conn, B) == 0
    assert index.count_company(B) == 0
    assert index.passages_for(B) == []
    assert receipt.layers["relational"].remaining == 0
    assert receipt.layers["vector_and_bm25"].remaining == 0
    assert receipt.layers["traces"].deleted == 3


def test_purge_leaves_other_tenants_untouched(world):
    conn, index, _ = world
    before_c = (_relational_count(conn, C), index.count_company(C))
    purge_company_data(conn, index, B, trace_sink=FakeTraces())
    assert (_relational_count(conn, C), index.count_company(C)) == before_c
    assert hybrid_search(index, C, "Yirgacheffe", reranker=_NoRerank()).hits


def test_purged_company_returns_nothing_from_search(world):
    conn, index, _ = world
    purge_company_data(conn, index, B, trace_sink=FakeTraces())
    result = hybrid_search(index, B, "sauna recovery", reranker=_NoRerank())
    assert result.total_found == 0


def test_purge_is_idempotent(world):
    conn, index, _ = world
    traces = FakeTraces()
    purge_company_data(conn, index, B, trace_sink=traces)
    second = purge_company_data(conn, index, B, trace_sink=traces)
    assert second.verified is True
    assert second.layers["relational"].deleted == 0
    assert second.layers["vector_and_bm25"].deleted == 0
    assert second.layers["traces"].deleted == 0


def test_purge_survives_a_reopened_index(tmp_path):
    persist = tmp_path / "chroma"
    conn = connect(tmp_path / "records.db")
    seed_all(conn)
    index = build_index(persist, LocalHashEmbedder())
    purge_company_data(conn, index, B, trace_sink=FakeTraces())
    conn.close()
    reopened = Index(LocalHashEmbedder(), persist_dir=persist)
    assert reopened.count_company(B) == 0
    assert reopened.count_company(C) > 0


def test_layers_not_yet_built_are_reported_not_passed(world):
    conn, index, _ = world
    receipt = purge_company_data(conn, index, B, trace_sink=FakeTraces())
    assert receipt.layers["langgraph_store"].status == "not_built"
    assert receipt.layers["checkpoints"].status == "not_built"


def test_missing_trace_backend_is_reported_not_configured(world):
    conn, index, _ = world
    receipt = purge_company_data(conn, index, B, trace_sink=None)
    assert receipt.layers["traces"].status == "not_configured"
    assert isinstance(NullTraceSink(), NullTraceSink)


def test_receipt_signature_is_bound_to_the_receipt_contents(world):
    from src.mm.cascade import LayerResult, _digest, _sign
    conn, index, _ = world
    receipt = purge_company_data(conn, index, B, trace_sink=FakeTraces(), signing_key=KEY)
    assert receipt.signature == _sign(receipt.digest, KEY)
    assert receipt.signature is not None
    tampered_layers = dict(receipt.layers)
    tampered_layers["relational"] = LayerResult(status="purged", deleted=99, remaining=0)
    assert _sign(_digest(receipt.company_id, tampered_layers, receipt.timestamp), KEY) != receipt.signature
    assert _sign(receipt.digest, "wrong-key") != receipt.signature


def test_purge_lead_removes_only_that_lead(world):
    conn, _, lead_ids = world
    target, keep = lead_ids[B][0], lead_ids[B][1]
    receipt = purge_lead(conn, target)
    assert receipt.verified is True
    assert receipt.layers["customer_leads"].deleted == 1
    remaining = [r[0] for r in conn.execute("SELECT lead_id FROM customer_leads WHERE company_id = ?", (B,))]
    assert remaining == [keep]


def test_purge_lead_is_idempotent(world):
    conn, _, lead_ids = world
    purge_lead(conn, lead_ids[B][0])
    again = purge_lead(conn, lead_ids[B][0])
    assert again.verified is True
    assert again.layers["customer_leads"].deleted == 0


def test_purge_lead_reports_store_and_checkpoint_layers_as_not_built(world):
    conn, _, lead_ids = world
    receipt = purge_lead(conn, lead_ids[C][0])
    assert receipt.layers["langgraph_store"].status == "not_built"
    assert receipt.layers["checkpoints"].status == "not_built"


def test_empty_identifiers_are_refused(world):
    conn, index, _ = world
    with pytest.raises(ValueError):
        purge_company_data(conn, index, " ")
    with pytest.raises(ValueError):
        purge_lead(conn, "")


class _NoRerank:
    def predict(self, pairs):
        return [float(len(text)) for _, text in pairs]
