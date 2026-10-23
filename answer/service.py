"""Answer service: authorized retrieval -> answer generator -> cache.

Generator: `extractive` (default, no model) or `llm` (answer.llm via project 01's endpoint), from
$ANSWER_GENERATOR or the `generator` argument. Both receive only the user's authorized hits. The
generator is part of the cache key, so switching it never serves the other one's answer. The
extractive threshold defaults to the one chosen on dev (`results/summary/eval.json`), so the service
answers with the setting that was evaluated, and it is part of the extractive cache key too.

Cache entries are keyed by (user, query hash) and record the epoch at write time. They are served
only while the epoch is unchanged (any permission or content change bumps it) AND every cited
document is still readable (re-checked on each hit, defence in depth). Revocation therefore can't
be bypassed through the cache.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from answer import llm
from answer.extractive import answer as extract
from search.db import connect
from search.retrieve import can_read, search

ROOT = Path(__file__).resolve().parents[1]
THRESHOLD = json.loads((ROOT / "results/summary/eval.json").read_text())["chosen_threshold"]


def query_sha(query: str) -> str:
    return hashlib.sha256(" ".join(query.lower().split()).encode()).hexdigest()


def ask(user: str, query: str, use_cache: bool = True, threshold: float | None = None, qvec=None,
        generator: str | None = None) -> dict:
    generator = generator or os.environ.get("ANSWER_GENERATOR", "extractive")
    threshold = THRESHOLD if threshold is None else threshold
    if generator not in ("extractive", "llm"):
        raise ValueError(f"unknown answer generator {generator!r}")
    sha = query_sha(f"extractive:{threshold}:{query}") if generator == "extractive" else \
        query_sha(f"{generator}:{llm.PROMPT_VERSION}:{query}")
    with connect() as conn:
        epoch = conn.execute("SELECT value FROM epoch WHERE id = 1").fetchone()[0]
        if use_cache:
            row = conn.execute("SELECT epoch, response, doc_ids FROM answer_cache WHERE user_id = %s AND query_sha = %s",
                               (user, sha)).fetchone()
            if row and row[0] == epoch and all(can_read(user, d) for d in row[2]):
                return {**row[1], "cached": True}
    hits = search(user, query, "hybrid", k=10, qvec=qvec)
    result = extract(query, hits, threshold=threshold) if generator == "extractive" else llm.answer(query, hits)
    result["generator"] = generator
    if use_cache and not result.get("model_error"):   # a failed model call is not an answer to keep
        doc_ids = [c["doc_id"] for c in result["citations"]]
        with connect() as conn:
            conn.execute("INSERT INTO answer_cache VALUES (%s, %s, %s, %s, %s) ON CONFLICT (user_id, query_sha) "
                         "DO UPDATE SET epoch = EXCLUDED.epoch, response = EXCLUDED.response, doc_ids = EXCLUDED.doc_ids",
                         (user, sha, epoch, json.dumps(result), doc_ids))
    return {**result, "cached": False}
