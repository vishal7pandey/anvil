"""Embedding client for the LiteLLM gateway.

The KB only knows the gateway alias (``anvil-embed``); swapping the provider is
a gateway config change. The OpenAI-compatible ``/v1/embeddings`` shape keeps
this client small and lets tests point it at a fake transport.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Protocol, Self

import httpx

from .config import KBSettings

log = logging.getLogger(__name__)


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class GatewayEmbedder:
    def __init__(
        self,
        settings: KBSettings,
        *,
        client: httpx.Client | None = None,
        max_retries: int = 3,
    ) -> None:
        self._settings = settings
        self._client = client or httpx.Client(timeout=60.0)
        self._owns_client = client is None
        self._max_retries = max_retries

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._settings.embed_batch_size):
            batch = list(texts[start : start + self._settings.embed_batch_size])
            vectors.extend(self._embed_batch(batch))
        return vectors

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        if not batch:
            return []
        payload = {"model": self._settings.embed_model, "input": batch}
        url = f"{self._settings.gateway_url.rstrip('/')}/v1/embeddings"
        headers = {"Authorization": f"Bearer {self._settings.gateway_key}"}
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = self._client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()["data"]
                vectors = [
                    item["embedding"]
                    for item in sorted(data, key=lambda item: item["index"])
                ]
                if len(vectors) != len(batch):
                    raise ValueError(
                        f"expected {len(batch)} embeddings, got {len(vectors)}"
                    )
                for vector in vectors:
                    if len(vector) != self._settings.embed_dim:
                        raise ValueError(
                            f"embedding dimension {len(vector)} != {self._settings.embed_dim}"
                        )
                return vectors
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                last_error = exc
                if attempt == self._max_retries:
                    break
                delay = 0.5 * 2 ** (attempt - 1)
                log.warning(
                    "embedding attempt %d failed (%s); retrying in %.1fs",
                    attempt,
                    exc,
                    delay,
                )
                time.sleep(delay)
        raise RuntimeError(
            f"embedding request failed after {self._max_retries} attempts: {last_error}"
        )


class DeterministicEmbedder:
    """Tiny offline embedder for unit tests and smoke runs without a gateway.

    Not for production: it hashes tokens into buckets, so only exact-ish lexical
    overlap retrieves. It keeps the store tests hermetic.
    """

    def __init__(self, dim: int = 1536) -> None:
        self.dim = dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        import hashlib
        import math
        import re

        vectors: list[list[float]] = []
        for value in texts:
            vector = [0.0] * self.dim
            for token in re.findall(r"[a-z0-9_]+", value.lower()):
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "big") % self.dim
                vector[index] += 1.0
            norm = math.sqrt(sum(component * component for component in vector)) or 1.0
            vectors.append([component / norm for component in vector])
        return vectors
