"""Shared contracts for MarketMind ingestion and retrieval.

Contract version: 1.0. Bump this version and update dependent tests on any field change.
"""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator

CONTRACT_VERSION = "1.0"

PASSAGE_ID_PATTERN = re.compile(r"^(?P<company_id>[a-z0-9_]+):(?P<source_sha12>[0-9a-f]{12}):(?P<chunk_index>\d+)$")


def make_passage_id(company_id: str, source_sha12: str, chunk_index: int) -> str:
    return f"{company_id}:{source_sha12}:{chunk_index}"


def parse_passage_id(passage_id: str) -> tuple[str, str, int]:
    match = PASSAGE_ID_PATTERN.match(passage_id)
    if not match:
        raise ValueError(f"invalid passage_id: {passage_id!r}")
    return match["company_id"], match["source_sha12"], int(match["chunk_index"])


class ErrorPayload(BaseModel):
    is_error: bool = True
    error_code: str
    message: str
    remediation_hint: Optional[str] = None


class Passage(BaseModel):
    passage_id: str
    company_id: str
    text: str = Field(min_length=1)
    source_ref: str
    source_sha12: str = Field(pattern=r"^[0-9a-f]{12}$")
    chunk_index: int = Field(ge=0)
    source_type: str = "source_text"
    language: str = "en"
    approved_flag: bool = True

    @field_validator("passage_id")
    @classmethod
    def _id_matches_fields(cls, value: str) -> str:
        parse_passage_id(value)
        return value


class SearchHit(BaseModel):
    passage_id: str
    score: float
    passage: Passage


class SearchResult(BaseModel):
    hits: list[SearchHit] = Field(default_factory=list)
    total_found: int = 0
    error: Optional[ErrorPayload] = None


class SourceMetadata(BaseModel):
    source_type: str
    licence: str
    owner: str
    version: str
    date: str


class BrandProfile(BaseModel):
    schema_version: str
    brand_id: str
    company_id: str
    brand: str
    company: str
    country: str
    status: str = "synthetic"
    approved_flag: bool = False
    company_profile: dict[str, str]
    source_metadata: SourceMetadata
