"""Text embeddings with BAAI/bge-base-en-v1.5 (local Hugging Face cache, CPU).

BGE v1.5 expects queries prefixed with a retrieval instruction; passages are embedded as-is.
CLS pooling, L2-normalised, so cosine distance is the right pgvector operator (<=>).
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np

MODEL = "BAAI/bge-base-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


@lru_cache(maxsize=1)
def _model():
    import torch
    from transformers import AutoModel, AutoTokenizer
    torch.set_num_threads(4)
    tok = AutoTokenizer.from_pretrained(MODEL, local_files_only=True)
    model = AutoModel.from_pretrained(MODEL, local_files_only=True).eval()
    return tok, model


def embed(texts: list[str], batch: int = 32) -> np.ndarray:
    import torch
    tok, model = _model()
    out = []
    for i in range(0, len(texts), batch):
        enc = tok(texts[i:i + batch], padding=True, truncation=True, max_length=512, return_tensors="pt")
        with torch.no_grad():
            cls = model(**enc).last_hidden_state[:, 0]
        out.append(torch.nn.functional.normalize(cls, dim=-1).numpy())
    return np.vstack(out) if out else np.zeros((0, 768), dtype=np.float32)


def embed_query(q: str) -> np.ndarray:
    return embed([QUERY_PREFIX + q])[0]


def to_pgvector(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"
