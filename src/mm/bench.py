"""Recall@5 benchmark: single-query baseline vs multi-query, over labelled queries.

Gold labels are phrases from the source text. They are resolved to passage IDs at run time,
so labels survive re-ingestion and a source edit shows up as an unresolved label, not a silent miss.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from src.mm.embed import Embedder, make_embedder
from src.mm.index import Index
from src.mm.ingest import ROOT, ingest_file
from src.mm.models import parse_passage_id
from src.mm.search import CrossEncoderReranker, Reranker, hybrid_search

QUERIES_PATH = ROOT / "evaluation" / "golden_queries.jsonl"
REPORT_PATH = ROOT / "storage" / "reports" / "recall_at_5.md"
DATA_DIR = ROOT / "data_input"
TOP_K = 5
TARGET_RECALL = 0.85


@dataclass
class QueryOutcome:
    query_id: str
    company_id: str
    query: str
    gold_ids: set[str] = field(default_factory=set)
    resolved: bool = False
    baseline_ids: list[str] = field(default_factory=list)
    multi_ids: list[str] = field(default_factory=list)
    baseline_hit: bool = False
    multi_hit: bool = False


def load_queries(path: Path = QUERIES_PATH) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def resolve_gold(index: Index, company_id: str, phrase: str) -> set[str]:
    return {p.passage_id for p in index.passages_for(company_id) if phrase in p.text}


def build_index(persist_dir: Path | str, embedder: Embedder) -> Index:
    index = Index(embedder, persist_dir=persist_dir)
    for company_dir in sorted(p for p in DATA_DIR.iterdir() if p.is_dir()):
        for source in sorted(company_dir.iterdir()):
            if source.suffix.lower() not in {".txt", ".md"}:
                continue
            result = ingest_file(source, company_dir.name)
            if result.error is not None:
                raise RuntimeError(f"{source}: {result.error.error_code}: {result.error.message}")
            index.index_passages(result.passages)
    return index


def run_benchmark(index: Index, queries: list[dict], reranker: Reranker, top_k: int = TOP_K) -> list[QueryOutcome]:
    outcomes: list[QueryOutcome] = []
    for item in queries:
        company_id = item["company_id"]
        gold = resolve_gold(index, company_id, item["gold_phrase"])
        outcome = QueryOutcome(
            query_id=item["query_id"], company_id=company_id, query=item["query"],
            gold_ids=gold, resolved=bool(gold),
        )
        baseline = hybrid_search(index, company_id, item["query"], top_k=top_k, use_multi_query=False, reranker=reranker)
        multi = hybrid_search(index, company_id, item["query"], top_k=top_k, use_multi_query=True, reranker=reranker)
        outcome.baseline_ids = [h.passage_id for h in baseline.hits]
        outcome.multi_ids = [h.passage_id for h in multi.hits]
        outcome.baseline_hit = bool(gold & set(outcome.baseline_ids))
        outcome.multi_hit = bool(gold & set(outcome.multi_ids))
        outcomes.append(outcome)
    return outcomes


def recall_at_k(outcomes: list[QueryOutcome], field_name: str) -> float:
    resolved = [o for o in outcomes if o.resolved]
    if not resolved:
        return 0.0
    return sum(1 for o in resolved if getattr(o, field_name)) / len(resolved)


def source_id_validity(index: Index, outcomes: list[QueryOutcome]) -> tuple[int, int]:
    valid = total = 0
    known = {p.passage_id for company_id in {o.company_id for o in outcomes} for p in index.passages_for(company_id)}
    for outcome in outcomes:
        for pid in outcome.baseline_ids + outcome.multi_ids:
            total += 1
            try:
                company, _, _ = parse_passage_id(pid)
            except ValueError:
                continue
            if company == outcome.company_id and pid in known:
                valid += 1
    return valid, total


def format_report(outcomes: list[QueryOutcome], validity: tuple[int, int], embedder_name: str, reranker_name: str) -> str:
    baseline = recall_at_k(outcomes, "baseline_hit")
    multi = recall_at_k(outcomes, "multi_hit")
    resolved = sum(1 for o in outcomes if o.resolved)
    unresolved = [o.query_id for o in outcomes if not o.resolved]
    valid, total = validity
    lines = [
        "# Recall@5 — MarketMind AI retrieval benchmark",
        "",
        f"- Embedder: `{embedder_name}`",
        f"- Reranker: `{reranker_name}`",
        f"- Queries: {len(outcomes)} ({resolved} with resolved gold labels)",
        "- Label status: self-drafted; not evaluation-grade until reviewed by Dr. Ashutosh Verma",
        "",
        "## Summary",
        "",
        "| Configuration | Recall@5 | Target 0.85 |",
        "|---|---|---|",
        f"| Baseline (hybrid + RRF + rerank, single query) | {baseline:.3f} | {'met' if baseline >= TARGET_RECALL else 'not met'} |",
        f"| Post-change (+ multi-query expansion) | {multi:.3f} | {'met' if multi >= TARGET_RECALL else 'not met'} |",
        f"| Delta (post - baseline) | {multi - baseline:+.3f} | |",
        "",
        f"Source-ID validity: {valid}/{total} returned passage IDs parse, belong to the queried company and exist in the index.",
        "",
        "## Per query",
        "",
        "| ID | Company | Query | Gold resolved | Baseline hit | Multi-query hit |",
        "|---|---|---|---|---|---|",
    ]
    for o in outcomes:
        lines.append(
            f"| {o.query_id} | {o.company_id} | {o.query} | {'yes' if o.resolved else 'NO'} | "
            f"{'yes' if o.baseline_hit else 'no'} | {'yes' if o.multi_hit else 'no'} |"
        )
    if unresolved:
        lines += ["", f"Unresolved gold labels (excluded from recall): {', '.join(unresolved)}"]
    misses = [o for o in outcomes if o.resolved and not (o.baseline_hit or o.multi_hit)]
    if misses:
        lines += ["", "Missed by both configurations:"]
        lines += [f"- {o.query_id}: {o.query}" for o in misses]
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Recall@5 benchmark")
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    args = parser.parse_args()

    embedder = make_embedder()
    reranker = CrossEncoderReranker()
    queries = load_queries()
    with tempfile.TemporaryDirectory() as scratch:
        index = build_index(Path(scratch) / "chroma", embedder)
        outcomes = run_benchmark(index, queries, reranker)
        report = format_report(outcomes, source_id_validity(index, outcomes), embedder.name, reranker.name)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
