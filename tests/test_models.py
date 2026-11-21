import pytest
from pydantic import ValidationError

from src.mm.models import (
    BrandProfile,
    ErrorPayload,
    Passage,
    SearchResult,
    make_passage_id,
    parse_passage_id,
)

SHA = "0123456789ab"


def _passage(**overrides):
    data = dict(
        passage_id=make_passage_id("comp_cloudvane_01", SHA, 0),
        company_id="comp_cloudvane_01",
        text="Solar-hybrid AC with lower electricity use.",
        source_ref="brandA_moseta_source.txt",
        source_sha12=SHA,
        chunk_index=0,
    )
    data.update(overrides)
    return data


def test_passage_id_round_trip():
    pid = make_passage_id("comp_cloudvane_01", SHA, 7)
    assert pid == "comp_cloudvane_01:0123456789ab:7"
    assert parse_passage_id(pid) == ("comp_cloudvane_01", SHA, 7)


@pytest.mark.parametrize("bad", ["comp_x:short:0", "comp_x:0123456789ab", "Comp:0123456789ab:0", "comp_x:0123456789ab:-1"])
def test_parse_rejects_malformed_ids(bad):
    with pytest.raises(ValueError):
        parse_passage_id(bad)


def test_passage_defaults_to_approved_chunk():
    assert Passage(**_passage()).approved_flag is True


def test_passage_rejects_bad_id_format():
    with pytest.raises(ValidationError):
        Passage(**_passage(passage_id="not-an-id"))


def test_passage_rejects_empty_text():
    with pytest.raises(ValidationError):
        Passage(**_passage(text=""))


def test_error_payload_defaults():
    err = ErrorPayload(error_code="RETRIEVAL_ERROR", message="boom")
    assert err.is_error is True
    assert err.remediation_hint is None


def test_search_result_empty_default():
    result = SearchResult()
    assert result.hits == []
    assert result.total_found == 0
    assert result.error is None


def test_brand_profile_defaults_to_unapproved():
    profile = BrandProfile(
        schema_version="marketmind-brand-pack-1.0",
        brand_id="brandA_moseta",
        company_id="comp_cloudvane_01",
        brand="MOSETA",
        company="Oxyneuron India Pvt Ltd",
        country="India",
        company_profile={"tagline": "Renewable Luxury"},
        source_metadata={
            "source_type": "synthetic_brand_pack",
            "licence": "Project-created (synthetic)",
            "owner": "Damini Shrawan",
            "version": "1.0",
            "date": "2026-11-20",
        },
    )
    assert profile.approved_flag is False
