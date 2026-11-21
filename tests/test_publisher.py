import ast
import asyncio
import sqlite3

import pytest
from fastmcp import Client

from src.mm.approval import CampaignPayload, issue_approval, payload_hash, verify_approval
from src.mm.ingest import ROOT
from src.mm.publisher import create_server, publish
from src.mm.records import connect

B = "comp_pulsepeak_02"
SECRET = "test-secret-not-for-production"
NOW = 1_800_000_000
TTL = 3600


def _verifier(secret=SECRET, now=NOW):
    return lambda payload, token, claimed: verify_approval(payload, token, claimed, secret, now)


def _payload(**overrides) -> CampaignPayload:
    data = dict(
        company_id=B,
        campaign_id="camp_monsoon_01",
        campaign_name="Monsoon Restart",
        destination="mock_email_inbox",
        schedule="2026-12-01T09:00:00+05:30",
        email_subject="Monsoon Restart at PulsePeak",
        channel_copy={
            "sms": "Monsoon Restart: 20% off Core Monthly. Book a free trial at PulsePeak.",
            "email": "Train hard, recover smarter. Book a free trial session this month.",
        },
    )
    data.update(overrides)
    return CampaignPayload(**data)


@pytest.fixture()
def conn(tmp_path):
    db = connect(tmp_path / "records.db")
    yield db
    db.close()


def _publish(conn, payload, grant, key="idem-1", now=NOW, secret=SECRET, company=B, token=None, claimed=None):
    return publish(
        conn, company, payload,
        approval_token=token if token is not None else grant.approval_token,
        claimed_hash=claimed if claimed is not None else grant.payload_hash,
        idempotency_key=key, verify=_verifier(secret, now),
    )


def _count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM campaign_dispatches").fetchone()[0]


def test_approved_payload_dispatches_one_row_per_channel(conn):
    payload = _payload()
    grant = issue_approval(payload, SECRET, NOW, TTL)
    out = _publish(conn, payload, grant)
    assert out.error is None
    assert out.status == "dispatched"
    assert {e.channel_id for e in out.events} == {"sms", "email"}
    assert all(e.destination == "mock_email_inbox" for e in out.events)
    assert _count(conn) == 2


def test_changed_copy_after_approval_is_rejected(conn):
    original = _payload()
    grant = issue_approval(original, SECRET, NOW, TTL)
    edited = _payload(channel_copy={**original.channel_copy, "sms": "Monsoon Restart: 30% off everything."})
    out = _publish(conn, edited, grant)
    assert out.error.error_code == "APPROVAL_HASH_MISMATCH"
    assert _count(conn) == 0


def test_changed_schedule_after_approval_is_rejected(conn):
    original = _payload()
    grant = issue_approval(original, SECRET, NOW, TTL)
    out = _publish(conn, _payload(schedule="2026-12-02T09:00:00+05:30"), grant)
    assert out.error.error_code == "APPROVAL_HASH_MISMATCH"


def test_changed_destination_after_approval_is_rejected(conn):
    original = _payload()
    grant = issue_approval(original, SECRET, NOW, TTL)
    out = _publish(conn, _payload(destination="mock_sms_sink"), grant)
    assert out.error.error_code == "APPROVAL_HASH_MISMATCH"


def test_wrong_claimed_hash_is_rejected(conn):
    payload = _payload()
    grant = issue_approval(payload, SECRET, NOW, TTL)
    out = _publish(conn, payload, grant, claimed="0" * 64)
    assert out.error.error_code == "APPROVAL_HASH_MISMATCH"


def test_forged_token_is_rejected(conn):
    payload = _payload()
    grant = issue_approval(payload, SECRET, NOW, TTL)
    forged = f"{grant.expires_at}.{'a' * 64}"
    out = _publish(conn, payload, grant, token=forged)
    assert out.error.error_code == "APPROVAL_HASH_MISMATCH"


def test_token_signed_with_another_secret_is_rejected(conn):
    payload = _payload()
    grant = issue_approval(payload, "some-other-secret", NOW, TTL)
    out = _publish(conn, payload, grant)
    assert out.error.error_code == "APPROVAL_HASH_MISMATCH"


def test_expired_approval_is_rejected(conn):
    payload = _payload()
    grant = issue_approval(payload, SECRET, NOW, TTL)
    out = _publish(conn, payload, grant, now=NOW + TTL + 1)
    assert out.error.error_code == "APPROVAL_EXPIRED"
    assert _count(conn) == 0


def test_replay_with_same_idempotency_key_is_rejected(conn):
    payload = _payload()
    grant = issue_approval(payload, SECRET, NOW, TTL)
    assert _publish(conn, payload, grant, key="idem-1").error is None
    out = _publish(conn, payload, grant, key="idem-1")
    assert out.error.error_code == "DUPLICATE_DISPATCH_REJECTED"
    assert _count(conn) == 2


def test_replay_with_a_new_idempotency_key_is_still_rejected(conn):
    payload = _payload()
    grant = issue_approval(payload, SECRET, NOW, TTL)
    assert _publish(conn, payload, grant, key="idem-1").error is None
    out = _publish(conn, payload, grant, key="idem-2")
    assert out.error.error_code == "DUPLICATE_DISPATCH_REJECTED"
    assert _count(conn) == 2


def test_non_mock_destination_is_refused_even_with_valid_approval(conn):
    payload = _payload(destination="live_meta_ads")
    grant = issue_approval(payload, SECRET, NOW, TTL)
    out = _publish(conn, payload, grant)
    assert out.error.error_code == "DESTINATION_NOT_ALLOWED"
    assert _count(conn) == 0


def test_invalid_copy_is_refused_even_with_valid_approval(conn):
    payload = _payload(channel_copy={"sms": "x" * 200})
    grant = issue_approval(payload, SECRET, NOW, TTL)
    out = _publish(conn, payload, grant)
    assert out.error.error_code == "CHANNEL_VALIDATION_FAILED"
    assert _count(conn) == 0


def test_missing_idempotency_key_is_refused(conn):
    payload = _payload()
    grant = issue_approval(payload, SECRET, NOW, TTL)
    out = _publish(conn, payload, grant, key="   ")
    assert out.error.error_code == "MISSING_IDEMPOTENCY_KEY"


def test_hash_is_independent_of_key_order():
    a = _payload()
    b = CampaignPayload(**dict(reversed(list(a.model_dump().items()))))
    assert payload_hash(a) == payload_hash(b)


def test_database_refuses_a_second_dispatch_for_same_payload_and_channel(conn):
    payload = _payload()
    grant = issue_approval(payload, SECRET, NOW, TTL)
    assert _publish(conn, payload, grant).error is None
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO campaign_dispatches (dispatch_id, company_id, campaign_id, channel_id, payload_hash, "
            "approval_token, idempotency_key, copy_text) VALUES ('x', ?, 'c', 'sms', ?, 't', 'other-key', 'c')",
            (B, grant.payload_hash),
        )


def test_publisher_holds_no_secret_and_no_issuer():
    tree = ast.parse((ROOT / "src" / "mm" / "publisher.py").read_text(encoding="utf-8"))
    imported_names = set()
    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_modules.add(node.module)
            imported_names.update(alias.name for alias in node.names)
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
    assert "issue_approval" not in imported_names
    assert "verify_approval" not in imported_names
    assert "src.mm.approval_service" not in imported_modules
    assert "hmac" not in imported_modules


def test_server_requires_a_verifier(tmp_path):
    with pytest.raises(ValueError):
        create_server(tmp_path / "records.db", verify=None)


# MCP surface

def call(server, args: dict) -> dict:
    async def run():
        async with Client(server) as client:
            return (await client.call_tool("publish_test_campaign", args)).structured_content

    return asyncio.run(run())


def test_publish_tool_over_mcp_dispatches_approved_payload(tmp_path):
    connect(tmp_path / "records.db").close()
    payload = _payload()
    grant = issue_approval(payload, SECRET, NOW, TTL)
    server = create_server(tmp_path / "records.db", verify=_verifier(), clock=lambda: NOW)
    out = call(server, {
        "company_id": B,
        "approval_token": grant.approval_token,
        "payload_hash": grant.payload_hash,
        "idempotency_key": "mcp-1",
        "campaign_data": payload.model_dump(),
    })
    assert out["error"] is None
    assert out["status"] == "dispatched"


def test_publish_tool_over_mcp_rejects_company_mismatch(tmp_path):
    connect(tmp_path / "records.db").close()
    payload = _payload()
    grant = issue_approval(payload, SECRET, NOW, TTL)
    server = create_server(tmp_path / "records.db", verify=_verifier(), clock=lambda: NOW)
    out = call(server, {
        "company_id": "comp_cloudvane_01",
        "approval_token": grant.approval_token,
        "payload_hash": grant.payload_hash,
        "idempotency_key": "mcp-2",
        "campaign_data": payload.model_dump(),
    })
    assert out["error"]["error_code"] == "COMPANY_MISMATCH"


def test_publish_tool_is_marked_as_a_write(tmp_path):
    server = create_server(tmp_path / "records.db", verify=_verifier())

    async def run():
        async with Client(server) as client:
            return await client.list_tools()

    tools = asyncio.run(run())
    assert [t.name for t in tools] == ["publish_test_campaign"]
    assert tools[0].annotations.read_only_hint is False
