"""test-publisher: approval-gated mock dispatch.

Only mock destinations exist here. No live channel is reachable from this module.
This process never holds HMAC_SECRET_KEY. It asks the approval service to verify each token.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Callable

import requests
from fastmcp import FastMCP
from pydantic import BaseModel, Field

from src.mm.approval import CampaignPayload
from src.mm.campaign_checks import validate_channel
from src.mm.models import ErrorPayload
from src.mm.records import DEFAULT_DB_PATH, connect

ALLOWED_DESTINATIONS = ("mock_email_inbox", "mock_sms_sink", "mock_social_queue")
DEFAULT_APPROVAL_URL = "https://127.0.0.1:8801"
READ_ONLY = {"readOnlyHint": False, "destructiveHint": False}

Verifier = Callable[[CampaignPayload, str, str], ErrorPayload | None]


class DispatchEvent(BaseModel):
    dispatch_id: str
    channel_id: str
    destination: str
    status: str


class PublishOutput(BaseModel):
    status: str | None = None
    events: list[DispatchEvent] = Field(default_factory=list)
    error: ErrorPayload | None = None


class _Duplicate(Exception):
    pass


class HttpVerifier:
    """Asks the approval service over TLS. Fails closed when unreachable, untrusted, or refusing this publisher."""

    def __init__(self, base_url: str, publisher_token: str, ca_bundle: str | None = None, timeout: float = 5.0) -> None:
        if not publisher_token:
            raise ValueError("publisher_token is required to call the approval service")
        if not base_url.startswith("https://"):
            raise ValueError("the approval service must be reached over https")
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {publisher_token}"}
        self._verify = ca_bundle if ca_bundle else True
        self._timeout = timeout

    def __call__(self, payload: CampaignPayload, approval_token: str, claimed_hash: str) -> ErrorPayload | None:
        try:
            response = requests.post(
                f"{self._base_url}/verify",
                json={"payload": payload.model_dump(mode="json"),
                      "approval_token": approval_token,
                      "payload_hash": claimed_hash},
                headers=self._headers,
                verify=self._verify,
                timeout=self._timeout,
            )
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError):
            return ErrorPayload(
                error_code="APPROVAL_SERVICE_UNAVAILABLE",
                message="approval service could not confirm this approval",
                remediation_hint="Start the approval service and retry. Dispatch is refused until it answers.",
            )
        return ErrorPayload(**body["error"]) if body.get("error") else None


def _error(code: str, message: str, hint: str) -> ErrorPayload:
    return ErrorPayload(error_code=code, message=message, remediation_hint=hint)


def publish(
    conn: sqlite3.Connection,
    company_id: str,
    payload: CampaignPayload,
    approval_token: str,
    claimed_hash: str,
    idempotency_key: str,
    verify: Verifier,
) -> PublishOutput:
    if not idempotency_key.strip():
        return PublishOutput(error=_error("MISSING_IDEMPOTENCY_KEY", "idempotency_key is required",
                                          "Pass a unique idempotency_key per dispatch attempt."))
    if payload.destination not in ALLOWED_DESTINATIONS:
        return PublishOutput(error=_error("DESTINATION_NOT_ALLOWED", f"{payload.destination} is not a mock destination",
                                          f"Use one of: {', '.join(ALLOWED_DESTINATIONS)}"))
    error = verify(payload, approval_token, claimed_hash)
    if error is not None:
        return PublishOutput(error=error)

    for channel_id, copy in payload.channel_copy.items():
        check = validate_channel(copy, channel_id, subject=payload.email_subject)
        if check.error is not None or not check.is_valid:
            violations = check.violations or [check.error.message]
            return PublishOutput(error=_error(
                "CHANNEL_VALIDATION_FAILED", f"{channel_id}: {'; '.join(violations)}",
                "Edit the copy, then request a new approval.",
            ))

    events: list[DispatchEvent] = []
    try:
        with conn:
            if conn.execute(
                "SELECT 1 FROM campaign_dispatches WHERE company_id = ? AND payload_hash = ? LIMIT 1",
                (company_id, claimed_hash),
            ).fetchone():
                raise _Duplicate
            for channel_id, copy in payload.channel_copy.items():
                dispatch_id = f"disp_{uuid.uuid4().hex[:12]}"
                conn.execute(
                    "INSERT INTO campaign_dispatches (dispatch_id, company_id, campaign_id, channel_id, payload_hash, "
                    "approval_token, idempotency_key, copy_text, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'dispatched')",
                    (dispatch_id, company_id, payload.campaign_id, channel_id, claimed_hash,
                     approval_token, f"{idempotency_key}:{channel_id}", copy),
                )
                events.append(DispatchEvent(dispatch_id=dispatch_id, channel_id=channel_id,
                                            destination=payload.destination, status="dispatched"))
    except (_Duplicate, sqlite3.IntegrityError):
        return PublishOutput(error=_error(
            "DUPLICATE_DISPATCH_REJECTED", "this payload or idempotency key was already dispatched",
            "Do not resend. Check campaign_dispatches before retrying.",
        ))
    return PublishOutput(status="dispatched", events=events)


def create_server(
    db_path: Path | str = DEFAULT_DB_PATH,
    verify: Verifier | None = None,
    clock: Callable[[], float] = time.time,
) -> FastMCP:
    if verify is None:
        raise ValueError("a verifier is required (the approval service)")
    mcp = FastMCP("test-publisher")

    @mcp.tool(
        name="publish_test_campaign",
        description="Dispatch an approved campaign to mock destinations only. Requires a valid approval for this exact payload.",
        annotations=READ_ONLY,
    )
    def publish_test_campaign(
        company_id: str,
        approval_token: str,
        payload_hash: str,
        idempotency_key: str,
        campaign_data: CampaignPayload,
    ) -> PublishOutput:
        if campaign_data.company_id != company_id:
            return PublishOutput(error=_error("COMPANY_MISMATCH", "campaign_data belongs to another company",
                                              "Pass the company_id that owns the campaign."))
        conn = None
        try:
            conn = connect(db_path)
            return publish(conn, company_id, campaign_data, approval_token, payload_hash, idempotency_key, verify)
        except sqlite3.Error as exc:
            return PublishOutput(error=_error("DATABASE_ERROR", str(exc), "Check records.db is initialised."))
        finally:
            if conn is not None:
                conn.close()

    return mcp


def main() -> None:
    url = os.environ.get("APPROVAL_SERVICE_URL", DEFAULT_APPROVAL_URL)
    publisher_token = os.environ.get("PUBLISHER_TOKEN", "")
    if not publisher_token:
        sys.exit("PUBLISHER_TOKEN is not set; the publisher cannot confirm approvals")
    ca_bundle = os.environ.get("APPROVAL_CA_BUNDLE") or None
    create_server(DEFAULT_DB_PATH, HttpVerifier(url, publisher_token, ca_bundle)).run()


if __name__ == "__main__":
    main()
