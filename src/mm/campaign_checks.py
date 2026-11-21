"""campaign-checks: deterministic channel and offer checks, exposed as a read-only FastMCP server.

Rules live in code and config, never in model output. Every failure returns a structured result.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from fastmcp import FastMCP
from pydantic import BaseModel, Field

from src.mm.ingest import ROOT
from src.mm.models import ErrorPayload
from src.mm.records import DEFAULT_DB_PATH, connect, seed_catalogue

BRAND_PACKS_DIR = ROOT / "brand_packs"
READ_ONLY = {"readOnlyHint": True}
HASHTAG = re.compile(r"#\w+")
DiscountType = Literal["percentage", "flat", "bundle", "bogo"]


class ValidateChannelOutput(BaseModel):
    is_valid: bool = False
    char_count: int = 0
    max_allowed: int | None = None
    violations: list[str] = Field(default_factory=list)
    error: ErrorPayload | None = None


class OfferConflictOutput(BaseModel):
    has_conflict: bool = False
    conflicting_offer_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    error: ErrorPayload | None = None


@lru_cache(maxsize=1)
def channel_rules() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["channels"]


def validate_channel(content: str, channel_id: str, subject: str | None = None) -> ValidateChannelOutput:
    rules = channel_rules()
    if channel_id not in rules:
        return ValidateChannelOutput(error=ErrorPayload(
            error_code="UNKNOWN_CHANNEL", message=f"no rules for channel {channel_id!r}",
            remediation_hint=f"Use one of: {', '.join(sorted(rules))}",
        ))
    rule = rules[channel_id]
    violations: list[str] = []

    if channel_id == "email":
        subject_limit = rule["max_subject_characters"]
        body_limit = rule["max_body_characters"]
        subject_text = subject or ""
        if not subject_text.strip():
            violations.append("email subject is empty")
        if len(subject_text) > subject_limit:
            violations.append(f"subject has {len(subject_text)} characters, limit is {subject_limit}")
        if not content.strip():
            violations.append("email body is empty")
        if len(content) > body_limit:
            violations.append(f"body has {len(content)} characters, limit is {body_limit}")
        return ValidateChannelOutput(
            is_valid=not violations,
            char_count=len(content),
            max_allowed=body_limit,
            violations=violations,
        )

    limit = rule["max_characters"]
    if not content.strip():
        violations.append("content is empty")
    if len(content) > limit:
        violations.append(f"{len(content)} characters, limit is {limit}")
    hashtag_cap = rule.get("max_hashtags")
    if hashtag_cap is not None:
        hashtags = len(HASHTAG.findall(content))
        if hashtags > hashtag_cap:
            violations.append(f"{hashtags} hashtags, limit is {hashtag_cap}")
    return ValidateChannelOutput(
        is_valid=not violations,
        char_count=len(content),
        max_allowed=limit,
        violations=violations,
    )


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def check_offer_conflict(
    conn: sqlite3.Connection,
    company_id: str,
    product_id: str,
    discount_type: DiscountType,
    discount_value: float,
    start_date: str,
    end_date: str,
) -> OfferConflictOutput:
    product = conn.execute(
        "SELECT * FROM products WHERE company_id = ? AND product_id = ?", (company_id, product_id),
    ).fetchone()
    if product is None:
        return OfferConflictOutput(error=ErrorPayload(
            error_code="PRODUCT_NOT_FOUND", message=f"{product_id} is not in the catalogue for {company_id}",
            remediation_hint="Check product_id against the company catalogue.",
        ))
    start, end = _parse_date(start_date), _parse_date(end_date)
    if start is None or end is None:
        return OfferConflictOutput(error=ErrorPayload(
            error_code="INVALID_DATE", message="dates must be ISO 8601 (YYYY-MM-DD)",
            remediation_hint="Pass start_date and end_date as YYYY-MM-DD.",
        ))
    if start > end:
        return OfferConflictOutput(error=ErrorPayload(
            error_code="INVALID_DATE_RANGE", message="start_date is after end_date",
            remediation_hint="Swap the dates or correct the range.",
        ))

    reasons: list[str] = []
    conflicting: list[str] = []

    cap_unit = "percent" if discount_type == "percentage" else "inr" if discount_type == "flat" else None
    if cap_unit is not None and cap_unit == product["max_allowed_unit"]:
        cap = product["max_allowed_discount"]
        if discount_value > cap:
            reasons.append(f"discount {discount_value:g} exceeds the {cap:g} {cap_unit} cap for {product_id}")

    overlapping = conn.execute(
        "SELECT offer_id, discount_type FROM product_offers "
        "WHERE company_id = ? AND product_id = ? AND start_date <= ? AND end_date >= ?",
        (company_id, product_id, end.isoformat(), start.isoformat()),
    ).fetchall()
    for row in overlapping:
        pair = {row["discount_type"], discount_type}
        if pair & {"bogo", "bundle"} and "percentage" in pair:
            reasons.append(f"{discount_type} cannot stack with {row['discount_type']} offer {row['offer_id']}")
            conflicting.append(row["offer_id"])
        elif pair == {"bogo", "bundle"}:
            reasons.append(f"bogo cannot stack with bundle offer {row['offer_id']}")
            conflicting.append(row["offer_id"])

    return OfferConflictOutput(
        has_conflict=bool(reasons),
        conflicting_offer_ids=sorted(set(conflicting)),
        reasons=reasons,
    )


def load_catalogues(packs_dir: Path = BRAND_PACKS_DIR) -> list[dict]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(packs_dir.glob("*/catalogue.json"))]


def seed_all(conn: sqlite3.Connection, packs_dir: Path = BRAND_PACKS_DIR) -> None:
    for catalogue in load_catalogues(packs_dir):
        seed_catalogue(conn, catalogue["company_id"], catalogue["products"], catalogue["offers"])


def create_server(db_path: Path | str = DEFAULT_DB_PATH) -> FastMCP:
    mcp = FastMCP("campaign-checks")

    @mcp.tool(
        name="validate_channel",
        description="Check draft copy against channel length and hashtag limits. Deterministic.",
        annotations=READ_ONLY,
    )
    def validate_channel_tool(company_id: str, channel_id: str, content: str, subject: str | None = None) -> ValidateChannelOutput:
        if not company_id.strip():
            return ValidateChannelOutput(error=ErrorPayload(
                error_code="MISSING_COMPANY", message="company_id is required", remediation_hint="Pass company_id.",
            ))
        return validate_channel(content, channel_id, subject)

    @mcp.tool(
        name="check_offer_conflict",
        description="Check a proposed discount against the product cap, overlapping offers and stacking rules.",
        annotations=READ_ONLY,
    )
    def check_offer_conflict_tool(
        company_id: str,
        product_id: str,
        discount_type: DiscountType,
        discount_value: float,
        start_date: str,
        end_date: str,
    ) -> OfferConflictOutput:
        conn = None
        try:
            conn = connect(db_path)
            return check_offer_conflict(conn, company_id, product_id, discount_type, discount_value, start_date, end_date)
        except sqlite3.Error as exc:
            return OfferConflictOutput(error=ErrorPayload(
                error_code="DATABASE_ERROR", message=str(exc), remediation_hint="Check records.db is initialised.",
            ))
        finally:
            if conn is not None:
                conn.close()

    return mcp


def main() -> None:
    conn = connect()
    seed_all(conn)
    conn.close()
    create_server().run()


if __name__ == "__main__":
    main()
