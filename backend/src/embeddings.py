"""Local sentence-transformers embeddings for the optional pgvector path.

Only imported when the vector shortlist is actually used, so the core pipeline
runs without loading torch. Model is loaded once and cached.
"""

from __future__ import annotations

from functools import lru_cache

from .config import get_settings

EMBED_DIM = 384  # all-MiniLM-L6-v2


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(get_settings().embed_model)


def embed_text(text: str) -> list[float]:
    vec = _model().encode(text, normalize_embeddings=True)
    return [float(x) for x in vec]
