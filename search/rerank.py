"""Optional cross-encoder reranking for the `vector+rerank` and `hybrid+rerank` search modes.

The cross-encoder only re-orders hits that search() already returned for the authenticated user, so
it cannot surface a document the user can't read. Measured in eval/rerank.py
(results/summary/rerank.json): MRR@10 1.000 on dev and test for both modes, at about 40 ms per
(question, chunk) pair on CPU, i.e. roughly 0.8 s per query for 20 candidates. That cost is why the
served default stays plain `hybrid`.
"""

from __future__ import annotations

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

MODEL, REVISION = "BAAI/bge-reranker-base", "2cfc18c9415c912f9d8155881c133215df768a70"   # MIT
DEPTH = 20   # candidates taken from the base mode before reranking

_tok = _model = None


def _load():
    global _tok, _model
    if _model is None:
        _tok = AutoTokenizer.from_pretrained(MODEL, revision=REVISION, local_files_only=True)
        _model = AutoModelForSequenceClassification.from_pretrained(MODEL, revision=REVISION,
                                                                    local_files_only=True).eval()
    return _tok, _model


@torch.no_grad()
def scores(query: str, hits: list) -> list[float]:
    if not hits:
        return []
    tok, model = _load()
    enc = tok([query] * len(hits), [f"{h.title}\n{h.text}" for h in hits], padding=True, truncation=True,
              max_length=512, return_tensors="pt")
    return model(**enc).logits.view(-1).tolist()


def rerank(query: str, hits: list) -> list:
    ranked = sorted(zip(scores(query, hits), range(len(hits)), hits), key=lambda t: (-t[0], t[1]))
    return [h for _, _, h in ranked]
