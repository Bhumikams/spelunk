"""Embedding models.

`FastEmbedEmbedder` is the real one: all-MiniLM-L6-v2 run through ONNX Runtime
(via fastembed). It gives the same 384-dim sentence embeddings as the
sentence-transformers version, but needs ~250 MB of RAM instead of ~1 GB of
PyTorch, so it fits on free hosting tiers.

`HashEmbedder` is a tiny deterministic stand-in used by the tests so they run
offline without downloading a model.
"""
from __future__ import annotations

import hashlib
import re
from typing import Protocol

import numpy as np


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


def _normalise(vecs: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (vecs / norms).astype(np.float32)


class FastEmbedEmbedder:
    def __init__(self, model_name: str, cache_dir: str | None = None):
        from fastembed import TextEmbedding

        self.model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)
        self.dim = len(next(iter(self.model.embed(["dimension probe"]))))

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        vecs = np.array(list(self.model.embed(texts, batch_size=32)), dtype=np.float32)
        return _normalise(vecs)


class HashEmbedder:
    """Bag-of-words hashed into a fixed vector. Good enough for keyword-level tests."""

    def __init__(self, dim: int = 384):
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for tok in re.findall(r"[a-z0-9]+", t.lower()):
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                out[i, h % self.dim] += 1.0
        return _normalise(out)
