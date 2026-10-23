"""Which retrieval mode should the answer path use? Decided on dev, reported on test once.

    python -m eval.answer_path        # needs the ingested corpus; CPU only, ~10 min

The extractive answerer (at the served threshold) is scored, exactly as in eval/run.py, on the
hits of each candidate mode: hybrid (served today), vector, hybrid+rerank, vector+rerank.
Rule, fixed before running: switch away from hybrid only to the mode with the highest dev
supported-answer rate, and only if it beats hybrid there by at least 0.02 without a higher
unsupported-answer share or a lower appropriate-abstention rate. Test is reported for every mode.
Writes results/summary/answer_path.json.
"""

from __future__ import annotations

import json

from answer.service import THRESHOLD
from eval.rerank import Reranker
from eval.run import CORPUS, ROOT, score_answers, split_of
from identity.oracle import Oracle
from search.embed import embed_query
from search.retrieve import search

MODES = ["hybrid", "vector", "hybrid+rerank", "vector+rerank"]


def hits_by_mode(oracle: Oracle, questions: list[dict], users: list[str], rr: Reranker) -> dict:
    out = {m: {} for m in MODES}
    for q in questions:
        qvec = embed_query(q["text"])
        for u in users:
            answerable = bool(q["answers"]) and any(oracle.can_read(u, d) for d in q["gold_docs"])
            for m in MODES:
                if m.endswith("+rerank"):
                    deep = search(u, q["text"], m.removesuffix("+rerank"), k=20, qvec=qvec)
                    hits = rr.order(q["qid"], q["text"], deep)[:10]
                else:
                    hits = search(u, q["text"], m, k=10, qvec=qvec)
                out[m][(q["qid"], u)] = (hits, answerable)
    return out


def main() -> None:
    oracle = Oracle(CORPUS)
    users = sorted(oracle.principals["users"])
    questions = json.loads((CORPUS / "questions.json").read_text())
    forbidden = {u: oracle.forbidden_values(u) for u in users}
    rr = Reranker()
    report = {"threshold": THRESHOLD, "rule": "switch from hybrid only to the best dev mode, if it beats hybrid's dev "
              "supported-answer rate by >= 0.02 with no higher unsupported share and no lower abstention"}
    for split in ("dev", "test"):
        qs = [q for q in questions if split_of(q["qid"]) == split]
        hits = hits_by_mode(oracle, qs, users, rr)
        report[split] = {}
        for m in MODES:
            s = score_answers(oracle, qs, hits[m], forbidden, THRESHOLD)
            report[split][m] = {k: s[k] for k in ("supported_answer_rate", "appropriate_abstention_rate",
                                                  "unsupported_answer_share", "content_leaks", "citation_leaks")}
    dev, base = report["dev"], report["dev"]["hybrid"]
    best = max(MODES, key=lambda m: dev[m]["supported_answer_rate"])
    ok = (best != "hybrid" and dev[best]["supported_answer_rate"] - base["supported_answer_rate"] >= 0.02
          and dev[best]["unsupported_answer_share"] <= base["unsupported_answer_share"]
          and dev[best]["appropriate_abstention_rate"] >= base["appropriate_abstention_rate"])
    report["decision"] = best if ok else "hybrid"
    (ROOT / "results/summary/answer_path.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
