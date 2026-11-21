"""Embedding backends. Documents and queries must always use the same backend."""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import time
from typing import Protocol

import requests

log = logging.getLogger(__name__)

# ── env var identifiers (not evaluated at import) ─────────────────────────────

MM_EMBEDDER_OPENROUTER = "openrouter"
MM_EMBEDDER_LOCAL = "local"

# ── protocol ──────────────────────────────────────────────────────────────────


class Embedder(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


# ── local (deterministic hash stub) ───────────────────────────────────────────


class LocalHashEmbedder:
    """Deterministic signed feature-hashing embedder for offline tests. Not semantic."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim
        self.name = f"local-hash-{dim}"

    def _vector(self, text: str) -> list[float]:
        tokens = re.findall(r"[a-z0-9]+", text.lower())
        features = tokens + [t[i:i + 3] for t in tokens for i in range(max(0, len(t) - 2))]
        vector = [0.0] * self.dim
        for feature in features:
            digest = int.from_bytes(hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest(), "big")
            sign = 1.0 if (digest >> 63) == 0 else -1.0
            vector[digest % self.dim] += sign
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


# ── OpenRouter (baai/bge-m3) ─────────────────────────────────────────────────


class OpenRouterEmbedder:
    """Hosted baai/bge-m3 via OpenRouter. Requires OPENROUTER_API_KEY."""

    URL = "https://openrouter.ai/api/v1/embeddings"
    MAX_RETRIES = 3
    BACKOFF_SEC = 5.0

    def __init__(self, api_key: str, model: str = "baai/bge-m3", dim: int = 1024,
                 batch_size: int = 64, timeout: float = 60.0) -> None:
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is required for the OpenRouter embedder")
        self._api_key = api_key
        self._model = model
        self.dim = dim
        self._batch_size = batch_size
        self._timeout = timeout
        self.name = f"openrouter-{model}"

    def _call(self, texts: list[str]) -> list[list[float]]:
        last_error: Exception | None = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                response = requests.post(
                    self.URL,
                    json={"model": self._model, "input": texts},
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=self._timeout,
                )
                if response.status_code == 429 and attempt < self.MAX_RETRIES:
                    wait = self.BACKOFF_SEC * attempt
                    log.warning("OpenRouter rate limited (attempt %d/%d), retrying in %.1fs", attempt, self.MAX_RETRIES, wait)
                    time.sleep(wait)
                    continue
                response.raise_for_status()
                rows = sorted(response.json()["data"], key=lambda row: row["index"])
                return [row["embedding"] for row in rows]
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self.MAX_RETRIES:
                    wait = self.BACKOFF_SEC * attempt
                    log.warning("OpenRouter call failed (attempt %d/%d): %s — retrying in %.1fs", attempt, self.MAX_RETRIES, exc, wait)
                    time.sleep(wait)
                else:
                    raise
        raise RuntimeError(f"OpenRouter embedding failed after {self.MAX_RETRIES} attempts") from last_error

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        total = len(texts)
        for start in range(0, total, self._batch_size):
            batch = texts[start:start + self._batch_size]
            log.info("Embedding batch %d/%d (%d docs)", start // self._batch_size + 1, (total + self._batch_size - 1) // self._batch_size, len(batch))
            vectors.extend(self._call(batch))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._call([text])[0]


# ── factory ───────────────────────────────────────────────────────────────────


def make_embedder() -> Embedder:
    """Return an embedder based on env vars.

    Priority:
    1. ``MM_EMBEDDER=local`` or ``MM_EMBEDDER=openrouter`` forces one if set.
    2. ``OPENROUTER_API_KEY`` set → ``OpenRouterEmbedder``.
    3. Else → ``LocalHashEmbedder``.

    Set ``MM_EMBEDDER=local`` to use the hash embedder even when a key is
    present (e.g. during development or tests).
    """
    explicit = os.environ.get("MM_EMBEDDER", "").strip().lower()

    key = os.environ.get("OPENROUTER_API_KEY", "").strip()

    # honour an explicit override
    if explicit == MM_EMBEDDER_LOCAL:
        log.info("Embedder: forced local (MM_EMBEDDER=local)")
        return LocalHashEmbedder()
    if explicit == MM_EMBEDDER_OPENROUTER:
        if not key:
            raise ValueError("MM_EMBEDDER=openrouter but OPENROUTER_API_KEY is not set")
        log.info("Embedder: forced openrouter (MM_EMBEDDER=openrouter)")
        return OpenRouterEmbedder(key)

    # automatic selection
    if key:
        log.info("Embedder: openrouter (OPENROUTER_API_KEY present)")
        return OpenRouterEmbedder(key)

    log.info("Embedder: local (no OPENROUTER_API_KEY)")
    return LocalHashEmbedder()
