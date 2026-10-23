"""Reranker attempt (PLAN.md M3: "Reranker only if it improves held-out results — record the attempt either way").

    python -m eval.rerank          # needs the ingested corpus; CPU only

A cross-encoder (BAAI/bge-reranker-base, MIT, pinned revision) re-orders the top 20 authorized hits
of the vector and hybrid modes and keeps 10. It only re-orders what search() already returned for
that user, so it cannot add a document the user can't read; leaks are still counted. Retrieval
metrics are computed exactly as in eval/run.py (document-level recall@5 and MRR@10 over the
answerable (question, user) pairs).

Decision rule, fixed before running: adopt reranking for a mode only if on **dev** it raises
MRR@10 by at least 0.01 without lowering recall@5. Test is reported once, either way.
Writes results/summary/rerank.json.
"""

from __future__ import annotations

import json
import statistics
import time
from collections import defaultdict
from pathlib import Path

from eval.run import CORPUS, ROOT, split_of
from identity.oracle import Oracle
from search import rerank as cross
from search.embed import embed_query
from search.retrieve import search

MODEL, REVISION, DEPTH = cross.MODEL, cross.REVISION, cross.DEPTH
MODES = ["vector", "hybrid"]
KEEP = 10


class Reranker:
    """search.rerank's scoring, cached per (question, chunk): the score doesn't depend on the user."""

    def __init__(self):
        self.cache: dict[tuple[str, int], float] = {}
        self.seconds = 0.0

    def order(self, qid: str, query: str, hits: list) -> list:
        todo = [h for h in hits if (qid, h.chunk_id) not in self.cache]
        if todo:
            t0 = time.perf_counter()
            for h, s in zip(todo, cross.scores(query, todo)):
                self.cache[(qid, h.chunk_id)] = s
            self.seconds += time.perf_counter() - t0
        ranked = sorted(zip((self.cache[(qid, h.chunk_id)] for h in hits), range(len(hits)), hits),
                        key=lambda t: (-t[0], t[1]))
        return [h for _, _, h in ranked]


def metrics(oracle: Oracle, questions: list[dict], users: list[str], rr: Reranker) -> dict:
    out = {f"{m}{s}": defaultdict(list) for m in MODES for s in ("", "+rerank")}
    leaks = defaultdict(int)
    for q in questions:
        qvec = embed_query(q["text"])
        for u in users:
            gold = [d for d in q["gold_docs"] if oracle.can_read(u, d)]
            for m in MODES:
                deep = search(u, q["text"], m, k=DEPTH, qvec=qvec)
                for name, hits in ((m, search(u, q["text"], m, k=KEEP, qvec=qvec)),
                                   (f"{m}+rerank", rr.order(q["qid"], q["text"], deep)[:KEEP])):
                    leaks[name] += sum(not oracle.can_read(u, h.doc_id) for h in hits)
                    if q["answers"] and gold:
                        docs = list(dict.fromkeys(h.doc_id for h in hits))
                        out[name]["recall@5"].append(any(d in gold for d in docs[:5]))
                        rank = next((i + 1 for i, d in enumerate(docs) if d in gold), None)
                        out[name]["mrr@10"].append(1 / rank if rank else 0.0)
    return {name: {"recall@5": round(statistics.mean(v["recall@5"]), 3), "mrr@10": round(statistics.mean(v["mrr@10"]), 3),
                   "pairs": len(v["mrr@10"]), "leaks": leaks[name]} for name, v in out.items()}


def main() -> None:
    oracle = Oracle(CORPUS)
    questions = json.loads((CORPUS / "questions.json").read_text())
    users = sorted(oracle.principals["users"])
    rr = Reranker()
    report = {"model": MODEL, "revision": REVISION, "depth": DEPTH, "keep": KEEP,
              "rule": "adopt for a mode only if dev MRR@10 rises by >= 0.01 and dev recall@5 does not fall"}
    for split in ("dev", "test"):
        report[split] = metrics(oracle, [q for q in questions if split_of(q["qid"]) == split], users, rr)
    report["decision"] = {}
    for m in MODES:
        base, rer = report["dev"][m], report["dev"][f"{m}+rerank"]
        report["decision"][m] = "adopt" if rer["mrr@10"] - base["mrr@10"] >= 0.01 and rer["recall@5"] >= base["recall@5"] \
            else "reject"
    report["cross_encoder_pairs_scored"] = len(rr.cache)
    report["cross_encoder_ms_per_pair"] = round(1000 * rr.seconds / max(1, len(rr.cache)), 1)
    (ROOT / "results/summary/rerank.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
