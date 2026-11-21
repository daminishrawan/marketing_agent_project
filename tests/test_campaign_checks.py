import asyncio

import pytest
from fastmcp import Client

from src.mm.campaign_checks import (
    check_offer_conflict,
    create_server,
    seed_all,
    validate_channel,
)
from src.mm.records import connect

B, C = "comp_pulsepeak_02", "comp_terraroast_03"


@pytest.fixture()
def conn(tmp_path):
    db = connect(tmp_path / "records.db")
    seed_all(db)
    yield db
    db.close()


def call(server, tool: str, args: dict) -> dict:
    async def run():
        async with Client(server) as client:
            return (await client.call_tool(tool, args)).structured_content

    return asyncio.run(run())


# validate_channel

def test_sms_at_limit_is_valid():
    assert validate_channel("x" * 160, "sms").is_valid is True


def test_sms_over_limit_reports_count_and_limit():
    out = validate_channel("x" * 161, "sms")
    assert out.is_valid is False
    assert out.char_count == 161
    assert out.max_allowed == 160
    assert any("161 characters" in v for v in out.violations)


def test_instagram_hashtag_cap_is_enforced():
    within = " ".join(f"#tag{i}" for i in range(30))
    over = " ".join(f"#tag{i}" for i in range(31))
    assert validate_channel(within, "instagram").is_valid is True
    out = validate_channel(over, "instagram")
    assert out.is_valid is False
    assert any("31 hashtags" in v for v in out.violations)


def test_email_subject_and_body_limits_checked_separately():
    assert validate_channel("Body text", "email", subject="s" * 78).is_valid is True
    out = validate_channel("Body text", "email", subject="s" * 79)
    assert out.is_valid is False
    assert any("subject has 79" in v for v in out.violations)


def test_empty_content_is_rejected():
    out = validate_channel("   ", "x")
    assert out.is_valid is False
    assert "content is empty" in out.violations


def test_unknown_channel_returns_structured_error():
    out = validate_channel("hi", "carrier_pigeon")
    assert out.error.error_code == "UNKNOWN_CHANNEL"
    assert out.is_valid is False


# check_offer_conflict

def test_discount_within_cap_and_no_overlap_is_clean(conn):
    out = check_offer_conflict(conn, B, "core_monthly", "percentage", 20, "2027-02-01", "2027-02-28")
    assert out.has_conflict is False
    assert out.error is None


def test_discount_above_cap_conflicts(conn):
    out = check_offer_conflict(conn, B, "core_monthly", "percentage", 30, "2027-02-01", "2027-02-28")
    assert out.has_conflict is True
    assert any("exceeds the 25 percent cap" in r for r in out.reasons)


def test_bogo_overlapping_a_percentage_offer_conflicts(conn):
    out = check_offer_conflict(conn, B, "core_monthly", "bogo", 100, "2026-12-15", "2026-12-20")
    assert out.has_conflict is True
    assert "monsoon_restart" in out.conflicting_offer_ids


def test_bogo_outside_overlapping_dates_is_clean(conn):
    out = check_offer_conflict(conn, B, "core_monthly", "bogo", 100, "2027-01-01", "2027-01-10")
    assert out.has_conflict is False


def test_bundle_overlapping_a_percentage_offer_conflicts(conn):
    out = check_offer_conflict(conn, C, "single_origin_1kg", "bundle", 1999, "2026-12-12", "2026-12-14")
    assert out.has_conflict is True
    assert "festive_brew_week" in out.conflicting_offer_ids


def test_flat_discount_is_checked_against_inr_cap(conn):
    out = check_offer_conflict(conn, B, "student_core", "flat", 800, "2027-01-01", "2027-01-31")
    assert out.has_conflict is True
    assert any("inr cap" in r for r in out.reasons)


def test_unknown_product_is_not_a_conflict_but_an_error(conn):
    out = check_offer_conflict(conn, B, "no_such_product", "percentage", 5, "2027-01-01", "2027-01-31")
    assert out.error.error_code == "PRODUCT_NOT_FOUND"
    assert out.has_conflict is False


def test_inverted_date_range_is_an_error(conn):
    out = check_offer_conflict(conn, B, "core_monthly", "percentage", 5, "2027-02-10", "2027-02-01")
    assert out.error.error_code == "INVALID_DATE_RANGE"


def test_malformed_date_is_an_error(conn):
    out = check_offer_conflict(conn, B, "core_monthly", "percentage", 5, "next week", "2027-02-01")
    assert out.error.error_code == "INVALID_DATE"


def test_offers_are_scoped_to_their_company(conn):
    out = check_offer_conflict(conn, C, "core_monthly", "bogo", 100, "2026-12-15", "2026-12-20")
    assert out.error.error_code == "PRODUCT_NOT_FOUND"


# MCP surface

@pytest.fixture()
def server(conn, tmp_path):
    return create_server(tmp_path / "records.db")


def test_validate_channel_tool_over_mcp(server):
    out = call(server, "validate_channel", {"company_id": B, "channel_id": "sms", "content": "x" * 200})
    assert out["is_valid"] is False
    assert out["error"] is None


def test_check_offer_conflict_tool_over_mcp(server):
    out = call(server, "check_offer_conflict", {
        "company_id": B, "product_id": "core_monthly", "discount_type": "bogo",
        "discount_value": 100, "start_date": "2026-12-15", "end_date": "2026-12-20",
    })
    assert out["has_conflict"] is True


def test_invalid_discount_type_is_rejected_by_schema(server):
    with pytest.raises(Exception):
        call(server, "check_offer_conflict", {
            "company_id": B, "product_id": "core_monthly", "discount_type": "free_money",
            "discount_value": 5, "start_date": "2027-01-01", "end_date": "2027-01-31",
        })


def test_all_campaign_tools_are_read_only(server):
    async def run():
        async with Client(server) as client:
            return await client.list_tools()

    tools = asyncio.run(run())
    assert {t.name for t in tools} == {"validate_channel", "check_offer_conflict"}
    for tool in tools:
        assert tool.annotations.read_only_hint is True
