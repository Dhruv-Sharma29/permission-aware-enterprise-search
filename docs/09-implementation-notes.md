# 09 — Implementation notes: where the code departs from the plan

The docs are the design record, so the places where the code differs from
[`PLAN.md`](../PLAN.md), from the README, or from what the evaluation measured belong here, each
with its reason. Nothing here changes the thesis: no entry weakens an authorization result. Two
entries (§ 2, § 3) mean the served system is not exactly the evaluated one.

## Structure

### 1. Flatter layout than planned

PLAN.md § Target layout has `corpus/generator/`, `corpus/acl/` (versioned grants), `ingestion/`,
`cache/` and a rerank stage in `search/`. The code has `corpus/generate.py` (ACLs are fields of
each document in `documents.json`), ingestion and every write operation in `search/ingest.py`, and
the cache inside `answer/service.py`. Reason: each of those was a few dozen lines, and keeping the
cache rule next to the code that reads it made it easier to keep the cache from becoming a second
place where permissions live.

## Served behaviour vs evaluated behaviour

### 2. `/answer` served the extractive answerer at 0.5; the evaluation chose 0.7 (fixed)

`answer.service.ask()` defaulted to `threshold=0.5` and `search/api.py` calls `ask(user, q.query)`,
while `eval/run.py` chose 0.7 on dev (`eval.json` § `chosen_threshold`) and every published
extractive number is at 0.7. At 0.5 the test split's appropriate abstention is 0.843 instead of
1.000 and the unsupported share 0.399 instead of 0.056 (`eval.json` § `test.answers_by_threshold`).
Authorization was unaffected: leaks are 0 at every threshold.

**Fixed 2026-10-23.** `ask()` now defaults to `chosen_threshold` read from `eval.json` (as
`eval/run_llm.py` already did), and the threshold is part of the extractive cache key, so an answer
cached at another threshold is never served. The 15 tests pass, and the revocation demo was re-run
on the fixed service: all three scenarios still answer and cite before the change and drop the
document on the first request after it (42.8–59.0 ms, `results/summary/revocation.json`).

### 3. Hybrid is the default mode though vector measured best

`/search` falls back to `hybrid` for any unknown mode, and the answer service always retrieves with
`hybrid`. On test, vector recall@5 is 1.000 vs hybrid 0.962. On dev, hybrid had the better MRR
(0.950 vs 0.897). The README's "Hybrid is kept as an option, not the default claim" refers to the
claim, not the code default. Changing it should be a dev-split decision ([06](06-roadmap.md) item 3).

## Design departures

### 4. ACLs are current-state rows, not versions

PLAN.md M5 says "version docs and ACLs" and "caches keyed on ACL version". Documents are versioned
(`documents.version`, `chunks.doc_version`). ACLs and memberships are not. They are mutable rows,
and one global `epoch`, bumped by permission **and** content changes, stands in for an ACL version
in the cache key. Reason: per-ACL versions would only be needed to answer "what could user U read
at time T?", which nothing here asks. For cache validity a single counter is simpler and strictly
more conservative ([03](03-freshness-and-revocation.md) § Why one global epoch).

## Measurement corrections and honest downgrades

### 5. `requests_until_effective` is a constant

`eval/revocation.py` writes `"requests_until_effective": 1` without counting anything. The
measured evidence is `doc_cited_after: false` and `served_from_cache_after: false` on the first
post-change request, and those fields are real. `delay_to_first_correct_response_ms` is that
request's wall time, not a propagation delay. [03](03-freshness-and-revocation.md) and
[05](05-evaluation.md) report it that way.

### 6. The citation checker checks numbers only

PLAN.md M4 asks that each cited passage "supports the claim". `citation_check` implements that as
"every number in the answer appears in a cited passage". Answers without digits are covered only by
the gold-string match. PLAN.md M4 already records the limitation, and [04](04-grounded-answers.md)
repeats it.

### 7. The oracle's override hooks are unused

`Oracle.deleted` and `Oracle.revoked_grants` exist so tests can state the expected state after a
change. The integration tests instead restore state in `finally` blocks and assert directly on the
response. The hooks are ready for a test that compares the full post-revocation state against the
oracle, which would be stronger than the current single-question checks.

### 8. LLM calls are deduplicated per distinct input

Not in the plan. `eval/run_llm.py` sends each distinct (question, top-5 chunk ids) to the model
once: 349 calls for 660 test pairs. At temperature 0 that changes nothing about outcomes and cuts the
number of model calls by nearly half. It also made visible that pairs aren't independent, which is why the paired test is
reported both ways ([05](05-evaluation.md) § Is the LLM gain real?).

## Bugs found while building

### 9. Extractive answerer: `-ed` stemming and "long"

Two fixes chosen on dev (README § Design notes): a `-ed` suffix rule in `stem()` and adding "long"
to the stop list, because in "how long did…" it is question phrasing, not content
(`answer/extractive.py`). Neither touched test.

### 10. Prompt v1 dropped units

v1 answered "4" for "4 days per week". v2 asks for the unit and completing words, with an example
that appears nowhere in the corpus. Dev went from 0.935 to 0.986 supported. Both runs are kept
(`llm_answers_dev_v1.json`, `llm_answers_dev_v2.json`).

### 11. numpy 2 broke the host's torch

Installing `rapidocr-onnxruntime` without a pin pulled numpy 2.x into the venv, and the host's
torch/scipy failed with `numpy.core.multiarray failed to import`. `pyproject.toml` pins
`numpy==1.26.4` (`env/ENVIRONMENT.md`).

## Documentation drift found while writing these docs

### 12. Things the repo says that the code doesn't do

| Where | Says | Reality |
|---|---|---|
| `search/ingest.py` docstring | Write path "used by the admin CLI and the tests" | There is no admin CLI. The write path is Python functions, called by tests and `eval/revocation.py` |
| README architecture diagram | "Admin: grant, revoke, update, delete" | Same: functions, no endpoint or CLI |
| README results table | Search p50/p95 9/11, 11/14, 19/24 ms | `eval.json`: 9.9/16.8, 11.8/18.3, 21.7/34.4 ms |
| PLAN.md M1–M3, M5 checklists | Were unticked; ticked 2026-10-23 to match § Progress | All ticked, including the reranker attempt; evidence in [06](06-roadmap.md) |
| PLAN.md M5 | Guarantee "in `docs/revocation.md`" | Written as [03](03-freshness-and-revocation.md) |
| README § macOS | macOS paths "tested on Linux" | `tests/test_security.py` skips unless the **Linux** `.tools/pg/...` binary exists, so on a Homebrew-only Mac the 8 integration tests would skip rather than run |

These are recorded, not fixed in code. The PLAN.md link was updated to point at
[03](03-freshness-and-revocation.md).

## Deferred, not dropped

### 13. Reranker (attempted and recorded 2026-10-23)

PLAN.md M3: "Reranker only if it improves held-out results — record the attempt either way."
`eval/rerank.py` ran the attempt with a pre-declared dev rule; it improves both modes to MRR@10
1.000 on dev and test ([05](05-evaluation.md) § Results: reranker). It is an opt-in search mode,
not the default, because it adds about 0.8 s per query on CPU.

### 14. Role-dependent demo (closed 2026-10-23)

The last open definition-of-done item. `eval/role_demo.py` calls `/search` and `/answer` with real
bearer tokens for 5 users. The oracle picks the questions (one per distinct access pattern, so 3
qualified), not the system, and checks every result. Output: `results/summary/role_demo.{json,md}`.

### 15. Purging stale cache rows

Invalidated `answer_cache` rows are never served but never deleted
([03](03-freshness-and-revocation.md) § gaps).
