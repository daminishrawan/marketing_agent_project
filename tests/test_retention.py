import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.mm.bench import build_index
from src.mm.campaign_checks import seed_all
from src.mm.approval import CampaignPayload
from src.mm.embed import LocalHashEmbedder
from src.mm.index import Index
from src.mm.publisher import publish
from src.mm.records import connect, create_lead
from src.mm.retention import sweep

B, C = "comp_pulsepeak_02", "comp_terraroast_03"
NOW = datetime(2027, 6, 1, tzinfo=timezone.utc)


def _set_lead_created(conn, lead_id, when: datetime, status: str = "new"):
    conn.execute("UPDATE customer_leads SET created_at = ?, status = ? WHERE lead_id = ?",
                 (when.isoformat(timespec="seconds"), status, lead_id))
    conn.commit()


def _set_dispatch_time(conn, when: datetime):
    conn.execute("UPDATE campaign_dispatches SET dispatched_at = ?",
                 (when.strftime("%Y-%m-%d %H:%M:%S"),))
    conn.commit()


@pytest.fixture()
def world(tmp_path):
    conn = connect(tmp_path / "records.db")
    seed_all(conn)
    index = build_index(tmp_path / "chroma", LocalHashEmbedder())
    yield conn, index, tmp_path
    conn.close()


def _lead(conn, company=B):
    return create_lead(conn, company, "web_form", "Looking for a membership.", "j***@example.com", True).lead.lead_id


def test_unqualified_lead_is_due_after_a_year(world):
    conn, index, _ = world
    old = _lead(conn)
    fresh = _lead(conn)
    _set_lead_created(conn, old, NOW - timedelta(days=400))
    _set_lead_created(conn, fresh, NOW - timedelta(days=100))
    report = sweep(conn, index, now=NOW)
    assert report.categories["lead_records"].due == 1


def test_qualified_lead_is_kept_for_three_years(world):
    conn, index, _ = world
    lead = _lead(conn)
    _set_lead_created(conn, lead, NOW - timedelta(days=400), status="qualified")
    assert sweep(conn, index, now=NOW).categories["lead_records"].due == 0
    _set_lead_created(conn, lead, NOW - timedelta(days=1200), status="qualified")
    assert sweep(conn, index, now=NOW).categories["lead_records"].due == 1


def test_dry_run_deletes_nothing(world):
    conn, index, _ = world
    lead = _lead(conn)
    _set_lead_created(conn, lead, NOW - timedelta(days=400))
    report = sweep(conn, index, now=NOW, apply=False)
    assert report.categories["lead_records"].due == 1
    assert report.categories["lead_records"].deleted == 0
    assert conn.execute("SELECT COUNT(*) FROM customer_leads WHERE lead_id = ?", (lead,)).fetchone()[0] == 1


def test_apply_removes_due_leads_and_is_idempotent(world):
    conn, index, _ = world
    lead = _lead(conn)
    _set_lead_created(conn, lead, NOW - timedelta(days=400))
    first = sweep(conn, index, now=NOW, apply=True)
    assert first.categories["lead_records"].deleted == 1
    second = sweep(conn, index, now=NOW, apply=True)
    assert second.categories["lead_records"].due == 0
    assert second.categories["lead_records"].deleted == 0


def test_old_dispatches_are_removed_after_two_years(world):
    conn, index, _ = world
    payload = CampaignPayload(company_id=B, campaign_id="c", campaign_name="n", destination="mock_email_inbox",
                              schedule="2027-01-01T09:00:00+05:30", channel_copy={"sms": "Short copy."})
    out = publish(conn, B, payload, "unused", "a" * 64, "idem-ret", verify=lambda p, t, h: None)
    assert out.error is None
    _set_dispatch_time(conn, NOW - timedelta(days=800))
    report = sweep(conn, index, now=NOW, apply=True)
    assert report.categories["campaign_dispatch"].deleted == 1
    assert conn.execute("SELECT COUNT(*) FROM campaign_dispatches").fetchone()[0] == 0


def test_knowledge_older_than_a_year_is_removed_per_company(world, tmp_path):
    conn, index, root = world
    old_clock = Index(LocalHashEmbedder(), persist_dir=root / "chroma2", clock=lambda: NOW.timestamp() - 400 * 86400)
    from src.mm.ingest import ROOT, ingest_file
    source = ROOT / "data_input" / C / "bean_origins_and_catalog.md"
    old_clock.index_passages(ingest_file(source, C).passages)
    report = sweep(conn, old_clock, now=NOW, apply=True)
    assert report.categories["knowledge"].deleted > 0
    assert old_clock.count_company(C) == 0


def test_fresh_knowledge_is_kept(world):
    conn, index, _ = world
    before = index.count_company(B)
    report = sweep(conn, index, now=NOW, apply=True)
    assert report.categories["knowledge"].deleted == 0
    assert index.count_company(B) == before


def test_brand_profiles_past_five_years_are_flagged_not_deleted(world, tmp_path):
    conn, index, _ = world
    packs = tmp_path / "packs" / "brandX"
    packs.mkdir(parents=True)
    (packs / "brandX_profile.json").write_text(json.dumps({
        "company_id": "comp_old_99",
        "source_metadata": {"date": "2020-01-01"},
    }), encoding="utf-8")
    report = sweep(conn, index, now=NOW, apply=True, packs_dir=tmp_path / "packs")
    assert report.flagged_profiles == ["comp_old_99"]
    assert report.categories["brand_profile"].status == "flagged_for_review"
    assert report.categories["brand_profile"].deleted == 0


def test_checkpoints_and_traces_report_not_built_and_lessons_no_expiry(world):
    conn, index, _ = world
    report = sweep(conn, index, now=NOW)
    assert report.categories["checkpoint"].status == "not_built"
    assert report.categories["trace"].status == "not_built"
    assert report.categories["reflexion_lessons"].status == "no_expiry"
