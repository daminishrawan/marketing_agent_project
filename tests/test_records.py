import ast
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.mm.bench import build_index
from src.mm.embed import LocalHashEmbedder
from src.mm.ingest import ROOT
from src.mm.records import connect, create_lead, get_lead, score_lead

A, B = "comp_cloudvane_01", "comp_pulsepeak_02"


@pytest.fixture()
def conn(tmp_path: Path):
    db = connect(tmp_path / "records.db")
    yield db
    db.close()


def _lead(conn, **overrides):
    args = dict(
        company_id=A,
        lead_source="web_form",
        inquiry_text="Looking for a solar AC for a 20-seat office.",
        contact_masked="r***@example.com",
        consent_flag=True,
    )
    args.update(overrides)
    return create_lead(conn, **args)


def test_create_and_get_lead_round_trip(conn):
    created = _lead(conn)
    assert created.error is None
    assert created.lead.status == "new"
    assert created.lead.contact_masked == "r***@example.com"
    fetched = get_lead(conn, A, created.lead.lead_id)
    assert fetched.lead == created.lead


def test_lead_without_consent_is_rejected_and_not_stored(conn):
    result = _lead(conn, consent_flag=False)
    assert result.error.error_code == "CONSENT_REQUIRED"
    assert conn.execute("SELECT COUNT(*) FROM customer_leads").fetchone()[0] == 0


@pytest.mark.parametrize("contact", ["rahul.sharma@gmail.com", "+91 98765 43210", "9876543210"])
def test_raw_contact_is_rejected(conn, contact):
    result = _lead(conn, contact_masked=contact)
    assert result.error.error_code == "CONTACT_NOT_MASKED"


def test_raw_pii_inside_inquiry_is_rejected(conn):
    result = _lead(conn, inquiry_text="Call me on +91 98765 43210 please")
    assert result.error.error_code == "INQUIRY_CONTAINS_PII"


def test_missing_fields_are_rejected(conn):
    result = _lead(conn, inquiry_text="   ")
    assert result.error.error_code == "MISSING_FIELD"


def test_lead_is_scoped_to_its_company(conn):
    created = _lead(conn)
    result = get_lead(conn, B, created.lead.lead_id)
    assert result.lead is None
    assert result.error.error_code == "LEAD_NOT_FOUND"


def test_check_constraints_reject_invalid_rows(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO customer_leads (lead_id, company_id, lead_source, inquiry_text, contact_masked, consent_flag, qualification_score) "
            "VALUES ('x1', 'c', 's', 'i', 'r***@example.com', 1, 150)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO customer_leads (lead_id, company_id, lead_source, inquiry_text, contact_masked, consent_flag, status) "
            "VALUES ('x2', 'c', 's', 'i', 'r***@example.com', 1, 'bogus')"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO customer_leads (lead_id, company_id, lead_source, inquiry_text, contact_masked, consent_flag) "
            "VALUES ('x3', 'c', 's', 'i', 'r***@example.com', 2)"
        )


def test_records_module_is_not_imported_by_ingestion_or_indexing():
    for module in ("ingest.py", "index.py", "embed.py", "search.py", "bench.py"):
        tree = ast.parse((ROOT / "src" / "mm" / module).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module != "src.mm.records", module
            if isinstance(node, ast.Import):
                assert all(alias.name != "src.mm.records" for alias in node.names), module


def test_lead_text_never_appears_in_the_vector_index(conn):
    with tempfile.TemporaryDirectory() as scratch:
        index = build_index(Path(scratch) / "chroma", LocalHashEmbedder())
        leads = []
        for pack in (ROOT / "brand_packs").iterdir():
            if pack.is_dir() and (pack / "synthetic_leads.json").exists():
                leads += json.loads((pack / "synthetic_leads.json").read_text(encoding="utf-8"))
        assert leads
        for lead in leads:
            _lead(conn, company_id=lead["company_id"], inquiry_text=lead["inquiry_text"],
                  contact_masked=lead["contact_masked"], consent_flag=lead["consent_flag"], lead_source=lead["lead_source"])
            texts = " ".join(p.text for p in index.passages_for(lead["company_id"]))
            assert lead["inquiry_text"] not in texts


# ── score_lead tests ──────────────────────────────────────────────────────────

def test_score_lead_basic_scoring(conn):
    result = _lead(conn)
    scored = score_lead(conn, A, result.lead.lead_id)
    assert scored.error is None
    assert scored.lead.qualification_score is not None
    assert 0 <= scored.lead.qualification_score <= 100


def test_score_lead_high_score_for_detailed_referral(conn):
    result = _lead(conn, lead_source="referral",
                   inquiry_text="We need an urgent bulk order of 50 solar inverters for our office building. What discount do you offer on wholesale?")
    scored = score_lead(conn, A, result.lead.lead_id)
    assert scored.lead.qualification_score >= 70
    assert scored.lead.recommended_action == "route_to_sales"
    assert scored.lead.routing_destination == "sales_team"


def test_score_lead_nurture_for_medium_inquiry(conn):
    result = _lead(conn, lead_source="web_form",
                   inquiry_text="Looking for pricing on your AC models for a small office.")
    scored = score_lead(conn, A, result.lead.lead_id)
    assert 40 <= scored.lead.qualification_score <= 69
    assert scored.lead.recommended_action == "nurture"
    assert scored.lead.routing_destination == "marketing_queue"


def test_score_lead_low_score_for_short_cold_inquiry(conn):
    result = _lead(conn, lead_source="cold_outreach",
                   inquiry_text="Hi, saw your site. Contact me.")
    scored = score_lead(conn, A, result.lead.lead_id)
    assert scored.lead.qualification_score < 40
    assert scored.lead.recommended_action == "review"
    assert scored.lead.routing_destination is None


def test_score_lead_unknown_source_gets_fallback(conn):
    result = _lead(conn, lead_source="tiktok_comment",
                   inquiry_text="How much for a coffee roaster?")
    scored = score_lead(conn, A, result.lead.lead_id)
    # tiktok_comment is not in the source scores map → fallback 5
    assert scored.lead.qualification_score == 30 + 5 + 15  # base + fallback + product keyword


def test_score_lead_missing_lead_returns_error(conn):
    result = score_lead(conn, A, "lead_nonexistent1234")
    assert result.error is not None
    assert result.error.error_code == "LEAD_NOT_FOUND"


def test_score_lead_cross_tenant_rejected(conn):
    created = _lead(conn)
    result = score_lead(conn, B, created.lead.lead_id)
    assert result.error is not None
    assert result.error.error_code == "LEAD_NOT_FOUND"


def test_score_lead_updates_the_database(conn):
    result = _lead(conn, lead_source="referral",
                   inquiry_text="Urgent: need 20 solar panels with installation for our factory.")
    scored = score_lead(conn, A, result.lead.lead_id)
    row = conn.execute("SELECT qualification_score FROM customer_leads WHERE lead_id = ?",
                       (result.lead.lead_id,)).fetchone()
    assert row["qualification_score"] == scored.lead.qualification_score
    with tempfile.TemporaryDirectory() as scratch:
        index = build_index(Path(scratch) / "chroma", LocalHashEmbedder())
        leads = []
        for pack in (ROOT / "brand_packs").iterdir():
            if pack.is_dir() and (pack / "synthetic_leads.json").exists():
                leads += json.loads((pack / "synthetic_leads.json").read_text(encoding="utf-8"))
        assert leads
        for lead in leads:
            _lead(conn, company_id=lead["company_id"], inquiry_text=lead["inquiry_text"],
                  contact_masked=lead["contact_masked"], consent_flag=lead["consent_flag"], lead_source=lead["lead_source"])
            texts = " ".join(p.text for p in index.passages_for(lead["company_id"]))
            assert lead["inquiry_text"] not in texts
