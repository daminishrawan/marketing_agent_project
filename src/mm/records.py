"""Relational store (SQLite): leads, products and offers.

Must never be imported by ingestion or indexing. Lead text never enters the vector store.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from src.mm.ingest import ROOT
from src.mm.models import ErrorPayload

DEFAULT_DB_PATH = ROOT / "storage" / "records.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS customer_leads (
    lead_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL,
    lead_source TEXT NOT NULL,
    inquiry_text TEXT NOT NULL,
    contact_masked TEXT NOT NULL,
    consent_flag BOOLEAN NOT NULL CHECK (consent_flag IN (0, 1)),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT CHECK(status IN ('new', 'qualified', 'disqualified')) DEFAULT 'new',
    qualification_score INTEGER CHECK(qualification_score BETWEEN 0 AND 100),
    recommended_action TEXT,
    routing_destination TEXT
);
CREATE INDEX IF NOT EXISTS idx_leads_company ON customer_leads(company_id);

CREATE TABLE IF NOT EXISTS products (
    company_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    name TEXT NOT NULL,
    max_allowed_discount REAL NOT NULL,
    max_allowed_unit TEXT NOT NULL CHECK(max_allowed_unit IN ('percent', 'inr')),
    PRIMARY KEY (company_id, product_id)
);
CREATE TABLE IF NOT EXISTS product_offers (
    offer_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    discount_type TEXT NOT NULL CHECK(discount_type IN ('percentage', 'flat', 'bundle', 'bogo')),
    discount_value REAL NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL CHECK(end_date >= start_date),
    terms TEXT NOT NULL,
    approved_flag BOOLEAN NOT NULL DEFAULT 0,
    FOREIGN KEY (company_id, product_id) REFERENCES products(company_id, product_id)
);
CREATE INDEX IF NOT EXISTS idx_offers_company ON product_offers(company_id);

CREATE TABLE IF NOT EXISTS campaign_dispatches (
    dispatch_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    approval_token TEXT NOT NULL,
    idempotency_key TEXT UNIQUE NOT NULL,
    copy_text TEXT NOT NULL,
    dispatched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT CHECK(status IN ('dispatched', 'failed')) DEFAULT 'dispatched',
    UNIQUE (company_id, payload_hash, channel_id)
);
CREATE INDEX IF NOT EXISTS idx_dispatches_company ON campaign_dispatches(company_id);
"""

EMAIL_MASK = re.compile(r"^[A-Za-z]\*{3}@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
PHONE_MASK = re.compile(r"^\+?[0-9 ]{0,4}\*{3,}[0-9]{2,4}$")
RAW_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
RAW_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\s-]?){9,}\d(?!\d)")


class LeadRecord(BaseModel):
    lead_id: str
    company_id: str
    lead_source: str
    inquiry_text: str
    contact_masked: str
    consent_flag: bool
    created_at: str
    status: str = "new"
    qualification_score: int | None = None
    recommended_action: str | None = None
    routing_destination: str | None = None


class LeadResult(BaseModel):
    lead: LeadRecord | None = None
    error: ErrorPayload | None = None


def connect(path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def seed_catalogue(conn: sqlite3.Connection, company_id: str, products: list[dict], offers: list[dict]) -> None:
    conn.execute("DELETE FROM product_offers WHERE company_id = ?", (company_id,))
    conn.execute("DELETE FROM products WHERE company_id = ?", (company_id,))
    conn.executemany(
        "INSERT INTO products (company_id, product_id, name, max_allowed_discount, max_allowed_unit) VALUES (?, ?, ?, ?, ?)",
        [(company_id, p["product_id"], p["name"], p["max_allowed_discount"], p["max_allowed_unit"]) for p in products],
    )
    conn.executemany(
        "INSERT INTO product_offers (offer_id, company_id, product_id, discount_type, discount_value, start_date, end_date, terms, approved_flag) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (o["offer_id"], company_id, o["product_id"], o["discount_type"], o["discount_value"],
             o["start_date"], o["end_date"], o["terms"], int(o["approved_flag"]))
            for o in offers
        ],
    )
    conn.commit()


def _error(code: str, message: str, hint: str) -> ErrorPayload:
    return ErrorPayload(error_code=code, message=message, remediation_hint=hint)


def _validate(company_id: str, lead_source: str, inquiry_text: str, contact_masked: str, consent_flag: bool) -> ErrorPayload | None:
    if not all([company_id.strip(), lead_source.strip(), inquiry_text.strip(), contact_masked.strip()]):
        return _error("MISSING_FIELD", "company_id, lead_source, inquiry_text and contact_masked are required",
                      "Fill every required field")
    if not consent_flag:
        return _error("CONSENT_REQUIRED", "lead has no recorded consent", "Only store leads with consent_flag=True")
    if not (EMAIL_MASK.match(contact_masked) or PHONE_MASK.match(contact_masked)):
        return _error("CONTACT_NOT_MASKED", "contact is not in masked form",
                      "Mask the contact, e.g. r***@example.com or +91 ******42")
    if RAW_EMAIL.search(inquiry_text) or RAW_PHONE.search(inquiry_text):
        return _error("INQUIRY_CONTAINS_PII", "inquiry text contains a raw email or phone number",
                      "Remove personal contact details from the inquiry")
    return None


def _row_to_lead(row: sqlite3.Row) -> LeadRecord:
    data = dict(row)
    data["consent_flag"] = bool(data["consent_flag"])
    return LeadRecord(**data)


def create_lead(
    conn: sqlite3.Connection,
    company_id: str,
    lead_source: str,
    inquiry_text: str,
    contact_masked: str,
    consent_flag: bool,
) -> LeadResult:
    error = _validate(company_id, lead_source, inquiry_text, contact_masked, consent_flag)
    if error is not None:
        return LeadResult(error=error)
    lead_id = f"lead_{uuid.uuid4().hex[:12]}"
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO customer_leads (lead_id, company_id, lead_source, inquiry_text, contact_masked, consent_flag, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (lead_id, company_id, lead_source, inquiry_text, contact_masked, int(consent_flag), created_at),
    )
    conn.commit()
    return get_lead(conn, company_id, lead_id)


def get_lead(conn: sqlite3.Connection, company_id: str, lead_id: str) -> LeadResult:
    row = conn.execute(
        "SELECT * FROM customer_leads WHERE lead_id = ? AND company_id = ?",
        (lead_id, company_id),
    ).fetchone()
    if row is None:
        return LeadResult(error=_error("LEAD_NOT_FOUND", f"no lead {lead_id} for this company",
                                       "Check lead_id and company_id"))
    return LeadResult(lead=_row_to_lead(row))


# ── lead scoring ──────────────────────────────────────────────────────────────

_INQUIRY_LENGTHS = [(200, 20), (50, 10)]
_SOURCE_SCORES = {
    "referral": 20,
    "web_form": 10,
    "chatbot": 5,
    "cold_outreach": 0,
    "event": 15,
    "partner": 15,
}
_PRODUCT_KEYWORDS = [
    "product", "model", "ac", "heater", "coffee", "roast", "solar",
    "inverter", "battery", "panel", "offer", "discount", "bulk", "wholesale",
]
_URGENCY_WORDS = ["urgent", "now", "today", "asap", "immediately", "quick", "rush"]

_BASE_SCORE = 30


def _compute_score(inquiry_text: str, lead_source: str) -> int:
    score = _BASE_SCORE
    length = len(inquiry_text.strip())
    for threshold, points in _INQUIRY_LENGTHS:
        if length > threshold:
            score += points
            break
    score += _SOURCE_SCORES.get(lead_source, 5)
    words = set(inquiry_text.lower().split())
    if words & set(_PRODUCT_KEYWORDS):
        score += 15
    if words & set(_URGENCY_WORDS):
        score += 10
    return max(0, min(100, score))


def _recommended_action(score: int) -> tuple[str | None, str | None]:
    if score >= 70:
        return "route_to_sales", "sales_team"
    if score >= 40:
        return "nurture", "marketing_queue"
    return "review", None


def score_lead(conn: sqlite3.Connection, company_id: str, lead_id: str) -> LeadResult:
    result = get_lead(conn, company_id, lead_id)
    if result.error is not None:
        return result
    lead = result.lead
    score = _compute_score(lead.inquiry_text, lead.lead_source)
    action, destination = _recommended_action(score)
    conn.execute(
        "UPDATE customer_leads SET qualification_score = ?, recommended_action = ?, routing_destination = ? WHERE lead_id = ?",
        (score, action, destination, lead_id),
    )
    conn.commit()
    return get_lead(conn, company_id, lead_id)
