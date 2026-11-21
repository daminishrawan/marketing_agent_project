"""Retention clock: finds and (on request) removes records past their retention limit.

Retention limits follow memory_policy.md section 2. The default is a dry run: nothing is deleted
unless apply=True. Leads go through purge_lead, so they get the same verification and receipts.

Categories without a backing store yet are reported as not_built, never as swept.
Brand profiles are flagged for human review, never deleted automatically.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from src.mm.campaign_checks import BRAND_PACKS_DIR
from src.mm.cascade import purge_lead
from src.mm.index import Index
from src.mm.ingest import ROOT

DAYS = {
    "knowledge": 365,
    "brand_profile": 1825,
    "campaign_dispatch": 730,
    "lead_unqualified": 365,
    "lead_qualified": 1095,
    "checkpoint": 30,
    "trace": 90,
}
UNQUALIFIED_STATUSES = ("new", "disqualified")


class CategoryResult(BaseModel):
    category: str
    retention_days: int | None
    due: int = 0
    deleted: int = 0
    status: Literal["swept", "flagged_for_review", "not_built", "no_expiry"]


class RetentionReport(BaseModel):
    run_at: str
    applied: bool
    categories: dict[str, CategoryResult] = Field(default_factory=dict)
    flagged_profiles: list[str] = Field(default_factory=list)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _age_days(when: datetime, now: datetime) -> float:
    return (now - _utc(when)).total_seconds() / 86400


def sweep(
    conn: sqlite3.Connection,
    index: Index,
    now: datetime | None = None,
    apply: bool = False,
    packs_dir: Path = BRAND_PACKS_DIR,
) -> RetentionReport:
    current = _utc(now or datetime.now(timezone.utc))
    report = RetentionReport(run_at=current.isoformat(timespec="seconds"), applied=apply)

    lead_rows = conn.execute("SELECT lead_id, status, created_at FROM customer_leads").fetchall()
    due_leads = []
    for row in lead_rows:
        limit = DAYS["lead_unqualified"] if row["status"] in UNQUALIFIED_STATUSES else DAYS["lead_qualified"]
        if _age_days(datetime.fromisoformat(row["created_at"]), current) > limit:
            due_leads.append(row["lead_id"])
    lead_deleted = 0
    if apply:
        for lead_id in due_leads:
            receipt = purge_lead(conn, lead_id)
            lead_deleted += receipt.layers["customer_leads"].deleted
    report.categories["lead_records"] = CategoryResult(
        category="lead_records", retention_days=DAYS["lead_unqualified"],
        due=len(due_leads), deleted=lead_deleted, status="swept",
    )

    dispatch_rows = conn.execute("SELECT dispatch_id, dispatched_at FROM campaign_dispatches").fetchall()
    due_dispatches = [
        row["dispatch_id"] for row in dispatch_rows
        if _age_days(datetime.strptime(row["dispatched_at"], "%Y-%m-%d %H:%M:%S"), current) > DAYS["campaign_dispatch"]
    ]
    dispatch_deleted = 0
    if apply and due_dispatches:
        with conn:
            for dispatch_id in due_dispatches:
                dispatch_deleted += conn.execute(
                    "DELETE FROM campaign_dispatches WHERE dispatch_id = ?", (dispatch_id,),
                ).rowcount
    report.categories["campaign_dispatch"] = CategoryResult(
        category="campaign_dispatch", retention_days=DAYS["campaign_dispatch"],
        due=len(due_dispatches), deleted=dispatch_deleted, status="swept",
    )

    cutoff = int((current - timedelta(days=DAYS["knowledge"])).timestamp())
    knowledge_due = sum(index.count_older_than(company, cutoff) for company in index.company_ids())
    knowledge_deleted = 0
    if apply:
        for company in index.company_ids():
            knowledge_deleted += index.delete_older_than(company, cutoff)
    report.categories["knowledge"] = CategoryResult(
        category="knowledge", retention_days=DAYS["knowledge"],
        due=knowledge_due, deleted=knowledge_deleted, status="swept",
    )

    for path in sorted(packs_dir.glob("*/*_profile.json")):
        profile = json.loads(path.read_text(encoding="utf-8"))
        approved_on = datetime.fromisoformat(profile["source_metadata"]["date"]).replace(tzinfo=timezone.utc)
        if _age_days(approved_on, current) > DAYS["brand_profile"]:
            report.flagged_profiles.append(profile["company_id"])
    report.categories["brand_profile"] = CategoryResult(
        category="brand_profile", retention_days=DAYS["brand_profile"],
        due=len(report.flagged_profiles), deleted=0, status="flagged_for_review",
    )

    report.categories["checkpoint"] = CategoryResult(
        category="checkpoint", retention_days=DAYS["checkpoint"], status="not_built")
    report.categories["trace"] = CategoryResult(
        category="trace", retention_days=DAYS["trace"], status="not_built")
    report.categories["reflexion_lessons"] = CategoryResult(
        category="reflexion_lessons", retention_days=None, status="no_expiry")
    return report


DEFAULT_REPORT_DIR = ROOT / "storage" / "reports" / "retention"


def main() -> None:
    import argparse

    from src.mm.embed import make_embedder
    from src.mm.index import DEFAULT_PERSIST_DIR
    from src.mm.records import DEFAULT_DB_PATH, connect

    parser = argparse.ArgumentParser(description="Retention sweep. Dry run unless --apply is given.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = parser.parse_args()
    conn = connect(DEFAULT_DB_PATH)
    index = Index(make_embedder(), persist_dir=DEFAULT_PERSIST_DIR)
    report = sweep(conn, index, apply=args.apply)
    conn.close()
    args.report_dir.mkdir(parents=True, exist_ok=True)
    day = report.run_at[:10]
    mode = "applied" if args.apply else "dryrun"
    path = args.report_dir / f"{day}_{mode}.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(f"retention report written to {path}")


if __name__ == "__main__":
    main()
