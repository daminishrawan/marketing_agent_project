import asyncio
import tempfile
from pathlib import Path

import pytest
from fastmcp import Client

from src.mm.bench import build_index
from src.mm.embed import LocalHashEmbedder
from src.mm.mcp_kb import create_server, load_profiles

A, B = "comp_cloudvane_01", "comp_pulsepeak_02"


class FakeReranker:
    def predict(self, pairs):
        return [float(len(text)) for _, text in pairs]


@pytest.fixture(scope="module")
def index():
    with tempfile.TemporaryDirectory() as scratch:
        yield build_index(Path(scratch) / "chroma", LocalHashEmbedder())


@pytest.fixture(scope="module")
def profiles():
    return load_profiles()


@pytest.fixture(scope="module")
def server(index, profiles):
    return create_server(index, profiles, reranker=FakeReranker())


def call(server, tool: str, args: dict) -> dict:
    async def run():
        async with Client(server) as client:
            result = await client.call_tool(tool, args)
            return result.structured_content

    return asyncio.run(run())


def test_search_knowledge_returns_cited_passages_for_one_company(server):
    out = call(server, "search_knowledge", {"company_id": A, "query": "solar hybrid cooling", "top_k": 5})
    assert out["error"] is None
    assert out["passages"]
    for passage in out["passages"]:
        assert passage["passage_id"].startswith(f"{A}:")
        assert passage["source_ref"] == "brandA_moseta_source.txt"
        assert passage["source_sha12"]
        assert passage["source_date"] == "2026-11-20"


def test_search_knowledge_rejects_out_of_range_top_k(server):
    out = call(server, "search_knowledge", {"company_id": A, "query": "solar", "top_k": 50})
    assert out["passages"] == []
    assert out["error"]["error_code"] == "INVALID_TOP_K"


def test_search_knowledge_unknown_company_is_empty_not_error(server):
    out = call(server, "search_knowledge", {"company_id": "comp_missing_99", "query": "solar", "top_k": 3})
    assert out["total_found"] == 0
    assert out["error"] is None


def test_get_passage_returns_the_passage(server, index):
    pid = index.passages_for(B)[0].passage_id
    out = call(server, "get_passage", {"company_id": B, "passage_id": pid})
    assert out["error"] is None
    assert out["passage"]["passage_id"] == pid


def test_get_passage_rejects_another_tenants_id(server, index):
    pid_from_a = index.passages_for(A)[0].passage_id
    out = call(server, "get_passage", {"company_id": B, "passage_id": pid_from_a})
    assert out["passage"] is None
    assert out["error"]["error_code"] == "TENANT_MISMATCH"


def test_get_passage_rejects_malformed_id(server):
    out = call(server, "get_passage", {"company_id": A, "passage_id": "not-an-id"})
    assert out["error"]["error_code"] == "INVALID_PASSAGE_ID"


def test_get_passage_unknown_id_is_not_found(server):
    out = call(server, "get_passage", {"company_id": A, "passage_id": f"{A}:0123456789ab:999"})
    assert out["error"]["error_code"] == "PASSAGE_NOT_FOUND"


def test_unapproved_brand_profile_is_not_returned(server):
    out = call(server, "get_brand_profile", {"company_id": A})
    assert out["profile"] is None
    assert out["is_active"] is False
    assert out["error"]["error_code"] == "PROFILE_PENDING_APPROVAL"
    assert out["error"]["remediation_hint"] == "Brand profile is pending human sign-off."


def test_approved_brand_profile_is_returned(index, profiles):
    approved = dict(profiles)
    approved[A] = profiles[A].model_copy(update={"approved_flag": True})
    server = create_server(index, approved, reranker=FakeReranker())
    out = call(server, "get_brand_profile", {"company_id": A})
    assert out["error"] is None
    assert out["is_active"] is True
    assert out["profile"]["brand"] == "MOSETA"


def test_unknown_company_profile_returns_structured_error(server):
    out = call(server, "get_brand_profile", {"company_id": "comp_missing_99"})
    assert out["error"]["error_code"] == "UNKNOWN_COMPANY"


def test_every_tool_is_marked_read_only(server):
    async def run():
        async with Client(server) as client:
            return await client.list_tools()

    tools = asyncio.run(run())
    names = {t.name for t in tools}
    assert names == {"search_knowledge", "get_passage", "get_brand_profile"}
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
