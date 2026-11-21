"""knowledge-kb: read-only FastMCP server over the hybrid knowledge index."""

from __future__ import annotations

import json
from pathlib import Path

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from src.mm.bench import build_index
from src.mm.embed import make_embedder
from src.mm.index import DEFAULT_PERSIST_DIR, Index
from src.mm.models import BrandProfile, ErrorPayload, parse_passage_id
from src.mm.search import Reranker, hybrid_search
from src.mm.ingest import ROOT

BRAND_PACKS_DIR = ROOT / "brand_packs"
READ_ONLY = {"readOnlyHint": True}


class PassageModel(BaseModel):
    passage_id: str
    text: str
    source_ref: str
    source_sha12: str
    source_date: str | None = None


class SearchKnowledgeOutput(BaseModel):
    passages: list[PassageModel] = Field(default_factory=list)
    total_found: int = 0
    error: ErrorPayload | None = None


class GetPassageOutput(BaseModel):
    passage: PassageModel | None = None
    error: ErrorPayload | None = None


class BrandProfileOutput(BaseModel):
    is_active: bool = False
    profile: dict | None = None
    error: ErrorPayload | None = None


def load_profiles(packs_dir: Path = BRAND_PACKS_DIR) -> dict[str, BrandProfile]:
    profiles: dict[str, BrandProfile] = {}
    for path in sorted(packs_dir.glob("*/*_profile.json")):
        profile = BrandProfile(**json.loads(path.read_text(encoding="utf-8")))
        profiles[profile.company_id] = profile
    return profiles


def create_server(index: Index, profiles: dict[str, BrandProfile], reranker: Reranker | None = None) -> FastMCP:
    mcp = FastMCP("knowledge-kb")
    dates = {company_id: profile.source_metadata.date for company_id, profile in profiles.items()}

    def to_model(passage) -> PassageModel:
        return PassageModel(
            passage_id=passage.passage_id,
            text=passage.text,
            source_ref=passage.source_ref,
            source_sha12=passage.source_sha12,
            source_date=dates.get(passage.company_id),
        )

    @mcp.tool(
        name="search_knowledge",
        description="Hybrid search over one company's knowledge base. Returns cited passages.",
        annotations=READ_ONLY,
    )
    def search_knowledge(company_id: str, query: str, top_k: int = 5) -> SearchKnowledgeOutput:
        try:
            result = hybrid_search(index, company_id, query, top_k=top_k, reranker=reranker)
        except Exception as exc:
            return SearchKnowledgeOutput(error=ErrorPayload(
                error_code="RETRIEVAL_ERROR", message=str(exc),
                remediation_hint="Check company_id and that the index is built.",
            ))
        if result.error is not None:
            return SearchKnowledgeOutput(error=result.error)
        return SearchKnowledgeOutput(
            passages=[to_model(hit.passage) for hit in result.hits],
            total_found=result.total_found,
        )

    @mcp.tool(
        name="get_passage",
        description="Fetch one passage by passage_id. The passage must belong to company_id.",
        annotations=READ_ONLY,
    )
    def get_passage(company_id: str, passage_id: str) -> GetPassageOutput:
        try:
            owner, _, _ = parse_passage_id(passage_id)
        except ValueError as exc:
            return GetPassageOutput(error=ErrorPayload(
                error_code="INVALID_PASSAGE_ID", message=str(exc),
                remediation_hint="Use a passage_id returned by search_knowledge.",
            ))
        if owner != company_id:
            return GetPassageOutput(error=ErrorPayload(
                error_code="TENANT_MISMATCH", message="passage does not belong to the requested company",
                remediation_hint="Pass the company_id that owns this passage.",
            ))
        match = next((p for p in index.passages_for(company_id) if p.passage_id == passage_id), None)
        if match is None:
            return GetPassageOutput(error=ErrorPayload(
                error_code="PASSAGE_NOT_FOUND", message=f"no passage {passage_id}",
                remediation_hint="Re-run search_knowledge; the source may have been re-ingested.",
            ))
        return GetPassageOutput(passage=to_model(match))

    @mcp.tool(
        name="get_brand_profile",
        description="Return the brand profile for company_id. Unapproved profiles are not returned.",
        annotations=READ_ONLY,
    )
    def get_brand_profile(company_id: str) -> BrandProfileOutput:
        profile = profiles.get(company_id)
        if profile is None:
            return BrandProfileOutput(error=ErrorPayload(
                error_code="UNKNOWN_COMPANY", message=f"no brand profile for {company_id}",
                remediation_hint="Check company_id.",
            ))
        if not profile.approved_flag:
            return BrandProfileOutput(error=ErrorPayload(
                error_code="PROFILE_PENDING_APPROVAL", message=f"{company_id} brand profile is not approved",
                remediation_hint="Brand profile is pending human sign-off.",
            ))
        return BrandProfileOutput(is_active=True, profile=profile.model_dump())

    return mcp


def main() -> None:
    index = build_index(DEFAULT_PERSIST_DIR, make_embedder())
    create_server(index, load_profiles()).run()


if __name__ == "__main__":
    main()
