"""Embedding backends. Documents and queries must always use the same backend."""

from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Protocol

import requests


class Embedder(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


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


class OpenRouterEmbedder:
    """Hosted baai/bge-m3 via OpenRouter. Requires OPENROUTER_API_KEY."""

    URL = "https://openrouter.ai/api/v1/embeddings"

    def __init__(self, api_key: str, model: str = "baai/bge-m3", dim: int = 1024,
                 batch_size: int = 64, timeout: float = 30.0) -> None:
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is required for the OpenRouter embedder")
        self._api_key = api_key
        self._model = model
        self.dim = dim
        self._batch_size = batch_size
        self._timeout = timeout
        self.name = f"openrouter-{model}"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start:start + self._batch_size]
            response = requests.post(
                self.URL,
                json={"model": self._model, "input": batch},
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout,
            )
            response.raise_for_status()
            rows = sorted(response.json()["data"], key=lambda row: row["index"])
            vectors.extend(row["embedding"] for row in rows)
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def make_embedder() -> Embedder:
    key = os.environ.get("OPENROUTER_API_KEY")
    return OpenRouterEmbedder(key) if key else LocalHashEmbedder()
