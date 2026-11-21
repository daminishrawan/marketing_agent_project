"""Approval tokens for campaign dispatch.

Issuing and verifying live here so the publisher can import only verify_approval.
The publisher must never import issue_approval. Whatever process holds the secret can still issue tokens,
so the approval service should be the only process given HMAC_SECRET_KEY.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from functools import lru_cache

import yaml
from pydantic import BaseModel, Field

from src.mm.ingest import ROOT
from src.mm.models import ErrorPayload


class CampaignPayload(BaseModel):
    company_id: str
    campaign_id: str
    campaign_name: str
    destination: str
    schedule: str
    email_subject: str | None = None
    channel_copy: dict[str, str] = Field(min_length=1)


class ApprovalGrant(BaseModel):
    approval_token: str
    payload_hash: str
    expires_at: int


@lru_cache(maxsize=1)
def default_ttl_seconds() -> int:
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["system"]["approval_token_expiry_seconds"]


def canonical_json(payload: CampaignPayload) -> str:
    return json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_hash(payload: CampaignPayload) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _sign(secret: str, company_id: str, digest: str, expires_at: int) -> str:
    message = f"{company_id}|{digest}|{expires_at}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def issue_approval(payload: CampaignPayload, secret: str, now: int, ttl: int | None = None) -> ApprovalGrant:
    if not secret:
        raise ValueError("HMAC secret is required")
    digest = payload_hash(payload)
    expires_at = now + (ttl if ttl is not None else default_ttl_seconds())
    token = f"{expires_at}.{_sign(secret, payload.company_id, digest, expires_at)}"
    return ApprovalGrant(approval_token=token, payload_hash=digest, expires_at=expires_at)


def verify_approval(
    payload: CampaignPayload,
    approval_token: str,
    claimed_hash: str,
    secret: str,
    now: int,
) -> ErrorPayload | None:
    digest = payload_hash(payload)
    mismatch = ErrorPayload(
        error_code="APPROVAL_HASH_MISMATCH",
        message="approval does not match this exact payload",
        remediation_hint="Request a new approval for the current payload.",
    )
    if not hmac.compare_digest(digest, claimed_hash):
        return mismatch
    try:
        expires_text, signature = approval_token.split(".", 1)
        expires_at = int(expires_text)
    except ValueError:
        return mismatch
    expected = _sign(secret, payload.company_id, digest, expires_at)
    if not hmac.compare_digest(expected, signature):
        return mismatch
    if now > expires_at:
        return ErrorPayload(
            error_code="APPROVAL_EXPIRED",
            message="approval token has expired",
            remediation_hint="Request a new approval for the current payload.",
        )
    return None
