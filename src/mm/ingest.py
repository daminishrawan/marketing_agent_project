"""Ingestion sieve, parser and chunker.

Stores clean text raw. <source_data> delimiters are never added here; they belong only to prompt assembly.
"""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path

import yaml
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from pypdf import PdfReader

from src.mm.models import ErrorPayload, Passage, make_passage_id

ROOT = Path(__file__).resolve().parents[2]

HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?:\.0+)?(?:px|pt|em|rem|%)?\s*(?:;|$)",
    re.IGNORECASE,
)
STRIP_TAGS = ("script", "style", "meta", "noscript", "template")
MARKDOWN_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

# Heuristic stand-in for a trained Prompt Guard classifier. Same interface, swap later.
INJECTION_RULES: list[tuple[re.Pattern[str], float]] = [
    (re.compile(r"ignore (all |the )?(previous|prior|above) (instructions|prompts?|messages?)", re.I), 0.9),
    (re.compile(r"disregard (all |the )?(previous|prior|above|system)", re.I), 0.9),
    (re.compile(r"(reveal|print|show|leak) (the |your )?system prompt", re.I), 0.8),
    (re.compile(r"you are now (in )?(developer|dan|unrestricted) mode", re.I), 0.8),
    (re.compile(r"</?source_data>", re.I), 0.7),
    (re.compile(r"(send|forward) .{0,40}\b(to|at)\s+\S+@\S+", re.I), 0.6),
]


class IngestResult(BaseModel):
    passages: list[Passage] = Field(default_factory=list)
    quarantined: bool = False
    injection_score: float = 0.0
    error: ErrorPayload | None = None


@lru_cache(maxsize=1)
def _ingestion_config() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["ingestion"]


def sanitize_html(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")
    hidden = [
        tag for tag in soup.find_all(True)
        if tag.has_attr("hidden") or HIDDEN_STYLE.search(tag.get("style", ""))
    ]
    for tag in hidden:
        tag.decompose()
    for name in STRIP_TAGS:
        for tag in soup.find_all(name):
            tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def parse_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        raise ValueError("password-protected PDF")
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(text for text in pages if text.strip())


def score_injection(text: str) -> float:
    hits = [weight for pattern, weight in INJECTION_RULES if pattern.search(text)]
    if not hits:
        return 0.0
    return min(1.0, max(hits) + 0.1 * (len(hits) - 1))


def split_text(text: str, size: int | None = None, overlap: int | None = None, separators: list[str] | None = None) -> list[str]:
    cfg = _ingestion_config()
    size = size or cfg["chunk_size_chars"]
    overlap = overlap if overlap is not None else cfg["chunk_overlap_chars"]
    separators = separators if separators is not None else cfg["separators"]
    text = text.strip()
    if not text:
        return []
    return [chunk.strip() for chunk in _split_recursive(text, separators, size, overlap) if chunk.strip()]


def _split_recursive(text: str, separators: list[str], size: int, overlap: int) -> list[str]:
    if len(text) <= size:
        return [text]
    sep = next(s for s in separators if s == "" or s in text)
    if sep == "":
        step = size - overlap
        return [text[i:i + size] for i in range(0, len(text), step)]
    rest = separators[separators.index(sep) + 1:]
    pieces: list[str] = []
    for part in text.split(sep):
        if not part.strip():
            continue
        if len(part) > size:
            pieces.extend(_split_recursive(part, rest, size, overlap))
        else:
            pieces.append(part)
    return _merge(pieces, sep, size, overlap)


def _merge(pieces: list[str], sep: str, size: int, overlap: int) -> list[str]:
    out: list[str] = []
    current: list[str] = []
    for piece in pieces:
        if current and len(sep.join(current + [piece])) > size:
            out.append(sep.join(current))
            while current and (len(sep.join(current)) > overlap or len(sep.join(current + [piece])) > size):
                current.pop(0)
        current.append(piece)
    if current:
        out.append(sep.join(current))
    return out


def build_passages(text: str, company_id: str, source_ref: str, source_type: str = "source_text") -> list[Passage]:
    normalised = " ".join(text.split())
    source_sha12 = hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:12]
    return [
        Passage(
            passage_id=make_passage_id(company_id, source_sha12, index),
            company_id=company_id,
            text=chunk,
            source_ref=source_ref,
            source_sha12=source_sha12,
            chunk_index=index,
            source_type=source_type,
        )
        for index, chunk in enumerate(split_text(text))
    ]


def _error(code: str, message: str, hint: str) -> ErrorPayload:
    return ErrorPayload(error_code=code, message=message, remediation_hint=hint)


def ingest_file(path: Path, company_id: str) -> IngestResult:
    suffix = path.suffix.lower()
    if suffix not in {".pdf", ".html", ".htm", ".md", ".markdown", ".txt"}:
        return IngestResult(error=_error(
            "UNSUPPORTED_FILE_TYPE", f"unsupported suffix {suffix!r}", "Use .pdf, .html, .md or .txt",
        ))
    try:
        if suffix == ".pdf":
            text = parse_pdf(path)
        elif suffix in {".html", ".htm"}:
            text = sanitize_html(path.read_text(encoding="utf-8", errors="replace"))
        else:
            text = MARKDOWN_HTML_COMMENT.sub("", path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        return IngestResult(error=_error(
            "UNREADABLE_FILE_ERROR", f"{path.name}: {exc}", "Check the file opens and is not password-protected",
        ))

    text = text.strip()
    if not text:
        return IngestResult(error=_error(
            "UNREADABLE_FILE_ERROR", f"{path.name}: no extractable text", "Provide a file with text content",
        ))

    score = score_injection(text)
    if score >= _ingestion_config()["injection_quarantine_threshold"]:
        return IngestResult(quarantined=True, injection_score=score, error=_error(
            "PROMPT_INJECTION_QUARANTINED", f"{path.name} scored {score:.2f}",
            "Review the source for instruction-like text before re-ingesting",
        ))

    return IngestResult(
        passages=build_passages(text, company_id, path.name),
        injection_score=score,
    )
