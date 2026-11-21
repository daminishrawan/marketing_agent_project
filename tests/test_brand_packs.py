import json
import re
from pathlib import Path

import pytest
import yaml

from src.mm.models import BrandProfile

ROOT = Path(__file__).resolve().parent.parent
PACK_DIRS = sorted(p for p in (ROOT / "brand_packs").iterdir() if p.is_dir() and any(p.glob("*_profile.json")))
MASKED_CONTACT = re.compile(r"^[a-z]\*\*\*@example\.(com|org|net)$")


def _profile_path(pack_dir: Path) -> Path:
    return next(pack_dir.glob("*_profile.json"))


def test_three_brand_packs_exist():
    assert len(PACK_DIRS) == 3


@pytest.mark.parametrize("pack_dir", PACK_DIRS, ids=lambda p: p.name)
def test_profile_validates_and_is_unapproved(pack_dir):
    profile = BrandProfile(**json.loads(_profile_path(pack_dir).read_text(encoding="utf-8")))
    assert profile.approved_flag is False


@pytest.mark.parametrize("pack_dir", PACK_DIRS, ids=lambda p: p.name)
def test_source_file_exists_and_is_non_empty(pack_dir):
    data = json.loads(_profile_path(pack_dir).read_text(encoding="utf-8"))
    source = ROOT / data["source_file"]
    assert source.is_file(), source
    assert source.read_text(encoding="utf-8").strip()


def test_company_ids_are_unique():
    ids = [json.loads(_profile_path(p).read_text(encoding="utf-8"))["company_id"] for p in PACK_DIRS]
    assert len(ids) == len(set(ids)) == 3


@pytest.mark.parametrize("pack_dir", PACK_DIRS, ids=lambda p: p.name)
def test_synthetic_leads_are_masked_and_well_formed(pack_dir):
    leads = json.loads((pack_dir / "synthetic_leads.json").read_text(encoding="utf-8"))
    assert leads
    lead_ids = [lead["lead_id"] for lead in leads]
    assert len(lead_ids) == len(set(lead_ids))
    for lead in leads:
        assert MASKED_CONTACT.match(lead["contact_masked"]), lead["contact_masked"]
        assert isinstance(lead["consent_flag"], bool)


def test_lead_files_never_live_in_data_input():
    assert not list((ROOT / "data_input").rglob("*lead*"))


def test_no_lead_text_in_ingestion_sources():
    lead_texts = []
    for pack_dir in PACK_DIRS:
        for lead in json.loads((pack_dir / "synthetic_leads.json").read_text(encoding="utf-8")):
            lead_texts.append(lead["inquiry_text"])
    for source in (ROOT / "data_input").rglob("*"):
        if source.is_file():
            body = source.read_text(encoding="utf-8", errors="replace")
            for text in lead_texts:
                assert text not in body, f"lead text found in {source}"


def test_allowed_domains_configured():
    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert config["ingestion"]["allowed_domains"]
