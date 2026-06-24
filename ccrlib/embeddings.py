"""Embedding provider for the `embedding` routing baseline.

Two backends, chosen by environment so the same code runs locally and in the cloud:

  - **default (offline):** the dependency-free hashing embedding in `domain.embed`
    (deterministic, hermetic — used for local runs and CI).
  - **real (cloud):** Azure OpenAI embeddings, used when BOTH
    `AZURE_OPENAI_EMBEDDINGS_ENDPOINT` and `AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT` are set.
    Auth is via the Function App's managed identity (DefaultAzureCredential), matching the
    rest of the lab — no keys. Each unique text is embedded once (process cache), so a full
    run embeds only the ~handful of distinct card/task strings.

This upgrades the `embedding` baseline from a lexical hash to a genuine semantic router in
the cloud variant, so a reviewer cannot dismiss it as a toy. It is still deterministic for
a fixed model snapshot; the only loss of hermeticity is the dependency on the AOAI model
version, which we pin via the deployment + api-version and record in the run metadata.
"""
from __future__ import annotations

import math
import os
from functools import lru_cache

from . import domain


def _use_aoai() -> bool:
    return bool(os.environ.get("AZURE_OPENAI_EMBEDDINGS_ENDPOINT")
                and os.environ.get("AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT"))


_client = None


def _aoai_client():
    global _client
    if _client is None:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        from openai import AzureOpenAI

        token_provider = get_bearer_token_provider(
            DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
        _client = AzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_EMBEDDINGS_ENDPOINT"],
            azure_ad_token_provider=token_provider,
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        )
    return _client


@lru_cache(maxsize=8192)
def embed(text: str) -> tuple[float, ...]:
    """Return an embedding vector for `text` (cached). Tuple so it is hashable/cacheable."""
    if not _use_aoai():
        return tuple(domain.embed(text))
    deployment = os.environ["AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT"]
    resp = _aoai_client().embeddings.create(model=deployment, input=text)
    return tuple(resp.data[0].embedding)


def cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Full cosine similarity (real AOAI vectors are not pre-normalized like domain.embed)."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def backend() -> str:
    return "azure_openai" if _use_aoai() else "hash_bow"
