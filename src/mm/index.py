"""Dense (Chroma) and lexical (BM25) index, partitioned by company_id.

Chroma is the source of truth. BM25 is rebuilt per company from Chroma on open and after every write.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Callable

import chromadb
from rank_bm25 import BM25Okapi

from src.mm.embed import Embedder
from src.mm.models import Passage

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PERSIST_DIR = ROOT / "storage" / "chroma_db"
COLLECTION_NAME = "marketmind_kb"
TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


class EmbedderMismatchError(RuntimeError):
    pass


def tokenize(text: str) -> list[str]:
    return TOKEN_PATTERN.findall(text.lower())


class Index:
    def __init__(self, embedder: Embedder, persist_dir: Path | str = DEFAULT_PERSIST_DIR,
                 clock: Callable[[], float] = time.time) -> None:
        self.embedder = embedder
        self._clock = clock
        self._client = chromadb.PersistentClient(path=str(persist_dir))
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine", "embedder": embedder.name},
            embedding_function=None,
        )
        stored = (self._collection.metadata or {}).get("embedder")
        if stored != embedder.name:
            raise EmbedderMismatchError(
                f"collection was built with {stored!r}, refusing to query with {embedder.name!r}"
            )
        self._passages: dict[str, list[Passage]] = {}
        self._bm25: dict[str, BM25Okapi] = {}
        for company_id in self._stored_company_ids():
            self._reload(company_id)

    def index_passages(self, passages: list[Passage]) -> int:
        if not passages:
            return 0
        company_ids = {p.company_id for p in passages}
        if len(company_ids) != 1:
            raise ValueError("index_passages accepts one company per call")
        company_id = company_ids.pop()

        for source_ref in {p.source_ref for p in passages}:
            keep = {p.passage_id for p in passages if p.source_ref == source_ref}
            stored = self._ids_for_source(company_id, source_ref)
            stale = [pid for pid in stored if pid not in keep]
            if stale:
                self._collection.delete(ids=stale)

        vectors = self.embedder.embed_documents([p.text for p in passages])
        self._collection.upsert(
            ids=[p.passage_id for p in passages],
            documents=[p.text for p in passages],
            metadatas=[self._metadata(p) for p in passages],
            embeddings=vectors,
        )
        self._reload(company_id)
        return len(passages)

    def search_dense(self, company_id: str, query: str, n: int = 50) -> list[tuple[str, float]]:
        passages = self._passages.get(company_id)
        if not passages:
            return []
        result = self._collection.query(
            query_embeddings=[self.embedder.embed_query(query)],
            n_results=min(n, len(passages)),
            where={"company_id": company_id},
            include=["distances"],
        )
        ids = result["ids"][0]
        distances = result["distances"][0]
        return [(pid, 1.0 - distance) for pid, distance in zip(ids, distances)]

    def search_lexical(self, company_id: str, query: str, n: int = 50) -> list[tuple[str, float]]:
        bm25 = self._bm25.get(company_id)
        if bm25 is None:
            return []
        scores = bm25.get_scores(tokenize(query))
        passages = self._passages[company_id]
        ranked = sorted(
            ((index, float(score)) for index, score in enumerate(scores) if score > 0),
            key=lambda item: item[1],
            reverse=True,
        )[:n]
        return [(passages[index].passage_id, score) for index, score in ranked]

    def passages_for(self, company_id: str) -> list[Passage]:
        return list(self._passages.get(company_id, []))

    def count_company(self, company_id: str) -> int:
        return len(self._stored_ids_for_company(company_id))

    def delete_company(self, company_id: str) -> int:
        ids = self._stored_ids_for_company(company_id)
        if ids:
            self._collection.delete(ids=ids)
        self._passages.pop(company_id, None)
        self._bm25.pop(company_id, None)
        return len(ids)

    def _stored_ids_for_company(self, company_id: str) -> list[str]:
        return self._collection.get(where={"company_id": company_id}, include=[])["ids"]

    def _stored_company_ids(self) -> set[str]:
        rows = self._collection.get(include=["metadatas"])
        return {meta["company_id"] for meta in rows["metadatas"]}

    def _ids_for_source(self, company_id: str, source_ref: str) -> list[str]:
        rows = self._collection.get(
            where={"$and": [{"company_id": company_id}, {"source_ref": source_ref}]},
            include=[],
        )
        return rows["ids"]

    def _reload(self, company_id: str) -> None:
        rows = self._collection.get(
            where={"company_id": company_id},
            include=["documents", "metadatas"],
        )
        passages = sorted(
            (self._from_row(pid, doc, meta) for pid, doc, meta in zip(rows["ids"], rows["documents"], rows["metadatas"])),
            key=lambda p: (p.source_ref, p.chunk_index),
        )
        if passages:
            self._passages[company_id] = passages
            self._bm25[company_id] = BM25Okapi([tokenize(p.text) for p in passages])
        else:
            self._passages.pop(company_id, None)
            self._bm25.pop(company_id, None)

    def company_ids(self) -> list[str]:
        return sorted(self._passages)

    def count_older_than(self, company_id: str, cutoff_epoch: int) -> int:
        return len(self._ids_older_than(company_id, cutoff_epoch))

    def delete_older_than(self, company_id: str, cutoff_epoch: int) -> int:
        ids = self._ids_older_than(company_id, cutoff_epoch)
        if ids:
            self._collection.delete(ids=ids)
            self._reload(company_id)
        return len(ids)

    def _ids_older_than(self, company_id: str, cutoff_epoch: int) -> list[str]:
        rows = self._collection.get(
            where={"$and": [{"company_id": company_id}, {"ingested_epoch": {"$lt": cutoff_epoch}}]},
            include=[],
        )
        return rows["ids"]

    def _metadata(self, passage: Passage) -> dict:
        return {
            "company_id": passage.company_id,
            "source_ref": passage.source_ref,
            "source_sha12": passage.source_sha12,
            "chunk_index": passage.chunk_index,
            "source_type": passage.source_type,
            "language": passage.language,
            "approved_flag": passage.approved_flag,
            "ingested_epoch": int(self._clock()),
        }

    @staticmethod
    def _from_row(passage_id: str, document: str, meta: dict) -> Passage:
        return Passage(
            passage_id=passage_id,
            company_id=meta["company_id"],
            text=document,
            source_ref=meta["source_ref"],
            source_sha12=meta["source_sha12"],
            chunk_index=int(meta["chunk_index"]),
            source_type=meta["source_type"],
            language=meta["language"],
            approved_flag=bool(meta["approved_flag"]),
        )


# ── CLI: re-index ─────────────────────────────────────────────────────────────


def reindex(persist_dir: Path | str = DEFAULT_PERSIST_DIR) -> None:
    """Destroy the current index and rebuild from data_input using the current embedder."""
    from src.mm.embed import make_embedder
    from src.mm.ingest import ROOT, ingest_file

    DATA_DIR = ROOT / "data_input"

    path = Path(persist_dir)
    if path.exists():
        import shutil
        shutil.rmtree(path)
        print(f"Removed existing index at {path}")

    embedder = make_embedder()
    print(f"Building index with embedder: {embedder.name}")
    index = Index(embedder, persist_dir=path)

    for company_dir in sorted(p for p in DATA_DIR.iterdir() if p.is_dir()):
        company_id = company_dir.name
        print(f"  {company_id} ...", end="", flush=True)
        for source in sorted(company_dir.iterdir()):
            if source.suffix.lower() not in {".txt", ".md", ".html", ".pdf"}:
                continue
            result = ingest_file(source, company_id)
            if result.error is not None:
                print(f"\n  ERROR on {source}: {result.error.error_code}: {result.error.message}")
                continue
            index.index_passages(result.passages)
        print(f" {index.count_company(company_id)} passages")

    print(f"Done. Companies: {', '.join(index.company_ids())}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Index management for MarketMind AI")
    parser.add_argument("--reindex", action="store_true", help="Destroy and rebuild the index")
    parser.add_argument("--persist-dir", type=Path, default=DEFAULT_PERSIST_DIR, help="Chroma persist directory")
    args = parser.parse_args()
    if args.reindex:
        reindex(persist_dir=args.persist_dir)
    else:
        print("No action specified. Use --reindex to rebuild the index.")
