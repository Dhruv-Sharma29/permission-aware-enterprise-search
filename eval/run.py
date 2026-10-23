"""Retrieval, answer and leakage evaluation over every (question, user) pair.

    python -m eval.run

Questions are split dev/test by a hash of their id. The answer threshold and the archived-document
weight are chosen on dev; everything reported is on test. All 12 users ask every question.
Authorization is checked against identity.oracle (fixtures, plain Python), not the database.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
import time
from collections import defaultdict
from pathlib import Path

from answer.extractive import INJECTION, answer as extract
from identity.oracle import Oracle
from search.embed import embed_query
from search.retrieve import search

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / ".run/corpus"
MODES = ["keyword", "vector", "hybrid", "hybrid-no-archive-penalty"]


def split_of(qid: str) -> str:
    return "dev" if int(hashlib.sha1(qid.encode()).hexdigest(), 16) % 2 == 0 else "test"


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1) if xs else None


def evaluate(oracle: Oracle, questions: list[dict], users: list[str]) -> dict:
    qvecs = {q["qid"]: embed_query(q["text"]) for q in questions}
    forbidden = {u: oracle.forbidden_values(u) for u in users}
    retrieval = {m: defaultdict(list) for m in MODES}
    leaks = {m: {"doc": 0, "content": 0} for m in MODES}
    hits_by_pair = {}
    latency = defaultdict(list)
    for q in questions:
        for u in users:
            gold_readable = [d for d in q["gold_docs"] if oracle.can_read(u, d)]
            answerable = bool(q["answers"]) and bool(gold_readable)
            for m in MODES:
                t0 = time.perf_counter()
                hits = search(u, q["text"], m, k=10, qvec=qvecs[q["qid"]])
                latency[m].append((time.perf_counter() - t0) * 1000)
                leaks[m]["doc"] += sum(1 for h in hits if not oracle.can_read(u, h.doc_id))
                leaks[m]["content"] += sum(1 for h in hits for v in forbidden[u] if v in h.text)
                if answerable:
                    docs = list(dict.fromkeys(h.doc_id for h in hits))
                    retrieval[m]["recall@5"].append(any(d in gold_readable for d in docs[:5]))
                    rank = next((i + 1 for i, d in enumerate(docs) if d in gold_readable), None)
                    retrieval[m]["mrr@10"].append(1 / rank if rank else 0.0)
                    retrieval[m][f"recall@5:{q['category']}"].append(any(d in gold_readable for d in docs[:5]))
                if m == "hybrid":
                    hits_by_pair[(q["qid"], u)] = (hits, answerable)
    retrieval_summary = {m: {k: round(statistics.mean(v), 3) for k, v in sorted(r.items())} for m, r in retrieval.items()}
    return {"retrieval": retrieval_summary, "retrieval_leaks": leaks,
            "search_ms": {m: {"p50": pct(v, 0.5), "p95": pct(v, 0.95)} for m, v in latency.items()},
            "_hits": hits_by_pair, "_forbidden": forbidden}


NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def citation_check(r: dict, hits: list) -> dict:
    """Does the cited text back the answer? Checked against the passages themselves, not the model's
    claim: every number in the answer must appear in a cited passage."""
    cited = {c["chunk_id"] for c in r["citations"]}
    cited_text = " ".join(h.text for h in hits if h.chunk_id in cited)
    missing = [n for n in NUMBER.findall(r["answer"] or "") if n not in cited_text]
    return {"uncited": not cited, "numbers_not_in_citations": len(missing), "cited_text": cited_text}


def score_answers(oracle: Oracle, questions: list[dict], hits_by_pair: dict, forbidden: dict, threshold: float,
                  answer_fn=None) -> dict:
    """`answer_fn(question_text, hits) -> answer dict`; default: the extractive baseline at `threshold`."""
    answer_fn = answer_fn or (lambda text, hits: extract(text, hits, threshold=threshold))
    c = defaultdict(int)
    by_cat = defaultdict(lambda: [0, 0])
    for q in questions:
        for (qid, u), (hits, answerable) in hits_by_pair.items():
            if qid != q["qid"]:
                continue
            r = answer_fn(q["text"], hits)
            text = r["answer"] or ""
            c["content_leaks"] += sum(1 for v in forbidden[u] if v in text)
            c["citation_leaks"] += sum(1 for cit in r["citations"] if not oracle.can_read(u, cit["doc_id"]))
            c["model_errors"] += bool(r.get("model_error"))
            correct = any(a in text for a in q["answers"])
            if not r["abstained"]:
                chk = citation_check(r, hits)
                c["answered_uncited"] += chk["uncited"]
                c["answered_numbers_not_in_citations"] += chk["numbers_not_in_citations"] > 0
                c["correct_but_citation_lacks_answer"] += correct and not any(a in chk["cited_text"] for a in q["answers"])
                c["invalid_citations"] += r.get("invalid_citations", 0)
                c["answer_echoes_injection"] += bool(INJECTION.search(text))
            if answerable:
                c["answerable"] += 1
                c["supported"] += (not r["abstained"]) and correct
                c["abstained_wrongly"] += r["abstained"]
                by_cat[q["category"]][0] += (not r["abstained"]) and correct
                by_cat[q["category"]][1] += 1
            else:
                c["unanswerable"] += 1
                c["abstained_correctly"] += r["abstained"]
            if not r["abstained"]:
                c["answered"] += 1
                c["answered_unsupported"] += not (answerable and correct)
    return {"threshold": threshold,
            "supported_answer_rate": round(c["supported"] / max(1, c["answerable"]), 3),
            "appropriate_abstention_rate": round(c["abstained_correctly"] / max(1, c["unanswerable"]), 3),
            "unsupported_answer_share": round(c["answered_unsupported"] / max(1, c["answered"]), 3),
            "answerable_pairs": c["answerable"], "not_answerable_pairs": c["unanswerable"],
            "content_leaks": c["content_leaks"], "citation_leaks": c["citation_leaks"],
            "citations": {k: c[k] for k in ("answered_uncited", "answered_numbers_not_in_citations",
                                            "correct_but_citation_lacks_answer", "invalid_citations")},
            "answer_echoes_injection": c["answer_echoes_injection"], "model_errors": c["model_errors"],
            "supported_by_category": {k: f"{a}/{n}" for k, (a, n) in sorted(by_cat.items())}}


def main() -> None:
    oracle = Oracle(CORPUS)
    users = sorted(oracle.principals["users"])
    questions = json.loads((CORPUS / "questions.json").read_text())
    report = {}
    for split in ("dev", "test"):
        qs = [q for q in questions if split_of(q["qid"]) == split]
        ev = evaluate(oracle, qs, users)
        hits, forbidden = ev.pop("_hits"), ev.pop("_forbidden")
        answers = {t: score_answers(oracle, qs, hits, forbidden, t) for t in (0.4, 0.5, 0.6, 0.7)}
        report[split] = {"questions": len(qs), "pairs": len(qs) * len(users), **ev, "answers_by_threshold": answers}
    dev = report["dev"]["answers_by_threshold"]
    chosen = max(dev, key=lambda t: dev[t]["supported_answer_rate"] + dev[t]["appropriate_abstention_rate"])
    report["chosen_threshold"] = chosen
    report["test_answers"] = report["test"]["answers_by_threshold"][chosen]
    (ROOT / "results/summary").mkdir(parents=True, exist_ok=True)
    (ROOT / "results/summary/eval.json").write_text(json.dumps(report, indent=1, default=str))
    t = report["test"]
    print("test pairs", t["pairs"], "| chosen threshold (dev)", chosen)
    for m in MODES:
        r = t["retrieval"][m]
        print(f"  {m:27} recall@5 {r['recall@5']:.3f} mrr@10 {r['mrr@10']:.3f} leaks {t['retrieval_leaks'][m]} "
              f"p50 {t['search_ms'][m]['p50']} ms p95 {t['search_ms'][m]['p95']} ms")
    print("  answers:", {k: v for k, v in report["test_answers"].items() if k != "supported_by_category"})
    print("  supported by category:", report["test_answers"]["supported_by_category"])


if __name__ == "__main__":
    main()
