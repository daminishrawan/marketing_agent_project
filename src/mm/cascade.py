"""Forget-me cascade: purge_company_data and purge_lead, per memory_policy.md section 5.

Each layer is verified after it runs. Layers that do not exist yet are reported as not_built,
never as purged. A trace backend that is not configured is reported as not_configured.
Repeating a call on already-purged data returns zero deletions and no error.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Callable, Literal, Protocol

from pydantic import BaseModel, Field

from src.mm.index import Index

Status = Literal["purged", "not_built", "not_configured"]
NOT_BUILT = "not_built"


class LayerResult(BaseModel):
    status: Status
    deleted: int = 0
    remaining: int | None = None


class DeletionReceipt(BaseModel):
    company_id: str
    layers: dict[str, LayerResult]
    timestamp: str
    digest: str
    signature: str | None = None
    verified: bool = False


class LeadPurgeReceipt(BaseModel):
    lead_id: str
    layers: dict[str, LayerResult] = Field(default_factory=dict)
    verified: bool = False


class TraceSink(Protocol):
    def delete_tagged(self, company_id: str) -> int: ...

    def remaining_tagged(self, company_id: str) -> int: ...


class NullTraceSink:
    """Used when no LangSmith project is configured. Reports not_configured, never a pass."""

    def delete_tagged(self, company_id: str) -> int:
        return 0

    def remaining_tagged(self, company_id: str) -> int:
        return 0


def _digest(company_id: str, layers: dict[str, LayerResult], timestamp: str) -> str:
    body = {"company_id": company_id, "layers": {k: v.model_dump() for k, v in layers.items()}, "timestamp": timestamp}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


def _sign(digest: str, key: str | None) -> str | None:
    if not key:
        return None
    return hmac.new(key.encode("utf-8"), digest.encode("utf-8"), hashlib.sha256).hexdigest()


def _relational_purge(conn: sqlite3.Connection, company_id: str) -> LayerResult:
    tables = ("campaign_dispatches", "customer_leads", "product_offers", "products")
    deleted = 0
    with conn:
        for table in tables:
            deleted += conn.execute(f"DELETE FROM {table} WHERE company_id = ?", (company_id,)).rowcount
    remaining = sum(
        conn.execute(f"SELECT COUNT(*) FROM {table} WHERE company_id = ?", (company_id,)).fetchone()[0]
        for table in tables
    )
    return LayerResult(status="purged", deleted=deleted, remaining=remaining)


def _vector_purge(index: Index, company_id: str) -> LayerResult:
    deleted = index.delete_company(company_id)
    remaining = index.count_company(company_id)
    return LayerResult(status="purged", deleted=deleted, remaining=remaining)


def _trace_purge(sink: TraceSink | None, company_id: str) -> LayerResult:
    if sink is None or isinstance(sink, NullTraceSink):
        return LayerResult(status="not_configured")
    deleted = sink.delete_tagged(company_id)
    return LayerResult(status="purged", deleted=deleted, remaining=sink.remaining_tagged(company_id))


def purge_company_data(
    conn: sqlite3.Connection,
    index: Index,
    company_id: str,
    trace_sink: TraceSink | None = None,
    signing_key: str | None = None,
    clock: Callable[[], float] = time.time,
) -> DeletionReceipt:
    if not company_id.strip():
        raise ValueError("company_id is required")
    layers: dict[str, LayerResult] = {
        "langgraph_store": LayerResult(status=NOT_BUILT),
        "checkpoints": LayerResult(status=NOT_BUILT),
        "relational": _relational_purge(conn, company_id),
        "vector_and_bm25": _vector_purge(index, company_id),
        "traces": _trace_purge(trace_sink, company_id),
    }
    timestamp = datetime.fromtimestamp(clock(), timezone.utc).isoformat(timespec="seconds")
    digest = _digest(company_id, layers, timestamp)
    verified = all(
        layer.remaining in (None, 0)
        for layer in layers.values()
        if layer.status == "purged"
    )
    return DeletionReceipt(
        company_id=company_id,
        layers=layers,
        timestamp=timestamp,
        digest=digest,
        signature=_sign(digest, signing_key),
        verified=verified,
    )


def purge_lead(conn: sqlite3.Connection, lead_id: str, clock: Callable[[], float] = time.time) -> LeadPurgeReceipt:
    if not lead_id.strip():
        raise ValueError("lead_id is required")
    with conn:
        deleted = conn.execute("DELETE FROM customer_leads WHERE lead_id = ?", (lead_id,)).rowcount
    remaining = conn.execute("SELECT COUNT(*) FROM customer_leads WHERE lead_id = ?", (lead_id,)).fetchone()[0]
    layers = {
        "customer_leads": LayerResult(status="purged", deleted=deleted, remaining=remaining),
        "langgraph_store": LayerResult(status=NOT_BUILT),
        "checkpoints": LayerResult(status=NOT_BUILT),
    }
    return LeadPurgeReceipt(lead_id=lead_id, layers=layers, verified=remaining == 0)
