import ast
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.mm.bench import build_index
from src.mm.embed import LocalHashEmbedder
from src.mm.ingest import ROOT
from src.mm.records import connect, create_lead, get_lead

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
