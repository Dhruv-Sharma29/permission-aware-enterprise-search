"""LLM answers vs the extractive baseline (PLAN.md M4), same questions, same hits, same scorer.

    AGENT_LLM_URL=http://127.0.0.1:21091 python -m eval.run_llm --split dev    # prompt work: dev only
    python -m eval.run_llm --split test                                          # once, after dev

Hybrid retrieval exactly as in eval.run (top 10 per (question, user); both answerers read the top
5). Users with the same permissions often get identical passages, so each distinct (question,
passages) input is sent to the model once (temperature 0). Every model call, including failures, is
written to results/raw/<timestamp>-llm-<split>.jsonl. The extractive baseline uses the
dev-chosen threshold from results/summary/eval.json.

Pairs within a question are correlated (same passages for many users), so the paired test is also
reported per distinct input, which is closer to the independent unit.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx

from answer import llm
from answer.extractive import answer as extract
from eval.run import CORPUS, ROOT, citation_check, score_answers, split_of
from identity.oracle import Oracle
from search.embed import embed_query
from search.retrieve import search

TOP = 5


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value for b vs c discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n
    return round(min(1.0, 2 * tail), 4)


def model_meta() -> dict:
    meta = {"endpoint": llm.ENDPOINT, "model": llm.MODEL, "prompt_version": llm.PROMPT_VERSION,
            "config_hash": llm.config_hash(), "temperature": 0, "seed": 0}
    try:   # llama.cpp server behind the gateway: record the exact weights file (name carries the revision)
        props = httpx.get("http://127.0.0.1:21000/props", timeout=5).json()
        meta["model_file"] = props.get("model_path")
    except (httpx.HTTPError, ValueError):
        meta["model_file"] = None
    return meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test"], required=True)
    ap.add_argument("--workers", type=int, default=4)   # = llama-server --parallel
    args = ap.parse_args()

    oracle = Oracle(CORPUS)
    users = sorted(oracle.principals["users"])
    questions = [q for q in json.loads((CORPUS / "questions.json").read_text()) if split_of(q["qid"]) == args.split]
    forbidden = {u: oracle.forbidden_values(u) for u in users}
    threshold = json.loads((ROOT / "results/summary/eval.json").read_text())["chosen_threshold"]

    hits_by_pair, inputs = {}, {}
    for q in questions:
        qvec = embed_query(q["text"])
        for u in users:
            hits = search(u, q["text"], "hybrid", k=10, qvec=qvec)
            gold_readable = [d for d in q["gold_docs"] if oracle.can_read(u, d)]
            hits_by_pair[(q["qid"], u)] = (hits, bool(q["answers"]) and bool(gold_readable))
            key = (q["text"], tuple(h.chunk_id for h in hits[:TOP]))
            inputs.setdefault(key, {"qid": q["qid"], "hits": hits[:TOP], "users": []})["users"].append(u)

    meta = {**model_meta(), "split": args.split, "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "questions": len(questions), "pairs": len(hits_by_pair), "distinct_inputs": len(inputs)}
    print(json.dumps(meta))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_path = ROOT / f"results/raw/{stamp}-llm-{args.split}.jsonl"
    raw_path.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    with httpx.Client(base_url=llm.ENDPOINT, timeout=600) as client, raw_path.open("w") as raw, \
            ThreadPoolExecutor(args.workers) as pool:
        raw.write(json.dumps({"meta": meta}) + "\n")
        futures = {key: pool.submit(llm.call, key[0], inp["hits"], client) for key, inp in inputs.items()}
        for i, (key, fut) in enumerate(futures.items(), 1):
            inputs[key]["rec"] = rec = fut.result()
            raw.write(json.dumps({"qid": inputs[key]["qid"], "chunk_ids": list(key[1]), "users": inputs[key]["users"],
                                  **rec}) + "\n")
            raw.flush()
            if i % 25 == 0:
                print(f"  {i}/{len(inputs)} calls, {time.monotonic() - t0:.0f} s", flush=True)
    wall = time.monotonic() - t0

    def llm_fn(text, hits):
        return llm.to_answer(inputs[(text, tuple(h.chunk_id for h in hits[:TOP]))]["rec"], hits[:TOP])

    def ext_fn(text, hits):
        return extract(text, hits, threshold=threshold)

    report = {"meta": {**meta, "raw": str(raw_path.relative_to(ROOT)), "wall_s": round(wall, 1),
                       "extractive_threshold": threshold},
              "llm": score_answers(oracle, questions, hits_by_pair, forbidden, threshold, answer_fn=llm_fn),
              "extractive": score_answers(oracle, questions, hits_by_pair, forbidden, threshold, answer_fn=ext_fn)}

    # Paired outcomes: answerable pairs -> supported?; not-answerable pairs -> abstained?
    qtext = {q["qid"]: q for q in questions}
    paired = defaultdict(lambda: [0, 0])      # outcome -> [llm only, extractive only]
    paired_inputs = defaultdict(lambda: [0, 0])
    disagreements = []
    for key, inp in inputs.items():
        q = qtext[inp["qid"]]
        groups = defaultdict(list)
        for u in inp["users"]:
            groups[hits_by_pair[(q["qid"], u)][1]].append(u)
        for answerable, us in groups.items():
            hits = hits_by_pair[(q["qid"], us[0])][0]
            a, b = llm_fn(q["text"], hits), ext_fn(q["text"], hits)
            if answerable:
                ok_a = not a["abstained"] and any(x in (a["answer"] or "") for x in q["answers"])
                ok_b = not b["abstained"] and any(x in (b["answer"] or "") for x in q["answers"])
                outcome = "supported"
            else:
                ok_a, ok_b, outcome = a["abstained"], b["abstained"], "abstained_when_not_answerable"
            if ok_a != ok_b:
                paired[outcome][0 if ok_a else 1] += len(us)
                paired_inputs[outcome][0 if ok_a else 1] += 1
                disagreements.append({"qid": q["qid"], "category": q["category"], "users": us, "answerable": answerable,
                                      "gold": q["answers"], "question": q["text"],
                                      "llm": a.get("answer") or f"[abstained: {a.get('reason')}]",
                                      "llm_numbers_not_in_citations": citation_check(a, hits)["numbers_not_in_citations"]
                                      if not a["abstained"] else None,
                                      "extractive": b.get("answer") or "[abstained]"})
    report["paired"] = {o: {"llm_only": v[0], "extractive_only": v[1], "p_pairs": mcnemar_exact(*v),
                            "distinct_inputs": {"llm_only": paired_inputs[o][0], "extractive_only": paired_inputs[o][1],
                                                "p": mcnemar_exact(*paired_inputs[o])}}
                        for o, v in paired.items()}
    report["disagreements"] = disagreements
    rec = [inp["rec"] for inp in inputs.values()]
    lat = sorted(r["latency_s"] for r in rec)
    report["calls"] = {"n": len(rec), "errors": sum("error" in r for r in rec),
                       "truncated": sum(r.get("finish") == "length" for r in rec),
                       "completion_tokens": sum(r.get("usage", {}).get("completion_tokens", 0) for r in rec),
                       "latency_s_p50": lat[len(lat) // 2], "latency_s_p95": lat[int(0.95 * (len(lat) - 1))]}

    out = ROOT / f"results/summary/llm_answers_{args.split}_{llm.PROMPT_VERSION}.json"
    out.write_text(json.dumps(report, indent=1, default=str))
    for name in ("extractive", "llm"):
        r = report[name]
        print(f"{name:10} supported {r['supported_answer_rate']:.3f}  abstention {r['appropriate_abstention_rate']:.3f}  "
              f"unsupported share {r['unsupported_answer_share']:.3f}  leaks {r['content_leaks']}/{r['citation_leaks']}  "
              f"citations {r['citations']}  echoes injection {r['answer_echoes_injection']}  errors {r['model_errors']}")
        print("           by category:", r["supported_by_category"])
    print("paired:", json.dumps(report["paired"]))
    print("calls:", report["calls"], "->", out.relative_to(ROOT))


if __name__ == "__main__":
    main()
