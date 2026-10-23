# 08 — Learning checklist

Ordered by the milestone that forces each skill. "Done when" points at the artefact in this repo
that shows it was actually learned, not read about.

## M1 — Corpus and identity

| Skill | Why | Done when |
|---|---|---|
| Seeded synthetic data with planted unique values | Makes content leaks a string search instead of a judgement | `Builder.unique` guarantees every fact value is unique; `forbidden_values` depends on it |
| Writing hard cases on purpose | An easy corpus produces "0 leaks" for free | Archived contradicting policy, rounded public figure vs exact finance figure, contractor shared by name, planted instruction, unanswerable questions (`corpus/generate.py`) |
| Group-based ACLs with deliberate overlap | Revocation bugs hide in users with two paths to one document | judy (hr + eng_managers) in the third revocation scenario |
| A test oracle independent of the system under test | A wrong SQL join agrees with itself | `identity/oracle.py`; exhaustive agreement test over 2,820 pairs |
| JWT basics: HS256, required claims, expiry | Identity must come from a verified claim, never from the body | `identity/tokens.py`; forged-body test |

## M2 — Keyword baseline with authorization

| Skill | Why | Done when |
|---|---|---|
| Postgres full-text search (`tsvector`, `to_tsquery`, `ts_rank_cd`, GIN) | The baseline every other mode is compared to | `retrieve.keyword`; keyword row in `eval.json` |
| Pre-filtering vs post-filtering in retrieval | Post-filtering leaks through counts and starves narrow users | `AUTHORIZED` join before `LIMIT` |
| SQL functions as the single policy point | One definition of "can read", used by every surface | `readable_docs()` in `search/db.py` |
| 404-vs-403 and existence oracles | A status code can confirm a secret exists | Citation and preview endpoints |
| Running Postgres in user space | No root, no system install, reproducible | `scripts/setup_postgres.sh`, `search/db.py up` (drops to `nobody` via `setpriv` when run as root) |

## M3 — Hybrid retrieval

| Skill | Why | Done when |
|---|---|---|
| Dense embeddings with BGE (query prefix, CLS pooling, L2 norm) | Wrong pooling or a missing prefix silently costs recall | `search/embed.py` |
| pgvector operators and exact vs approximate search | Filtered ANN can starve results | Exact `<=>` scan; the decision row in [01](01-architecture.md) |
| Reciprocal-rank fusion | The standard way to combine incomparable scores | `retrieve.hybrid` (k = 60) |
| Recall@k and MRR on held-out questions | Comparing modes honestly | `eval.json` § `test.retrieval`; dev and test reported separately |
| Reporting a negative result | Hybrid lost to vector on test | README and [05](05-evaluation.md) say so |

## M4 — Grounded answers

| Skill | Why | Done when |
|---|---|---|
| Schema-constrained decoding (llama.cpp `response_format`) | Every reply parses; no regex on model output | `answer/llm.py: SCHEMA`; 0 errors in 349 test calls |
| Abstention as a scored outcome | "Answered wrongly" and "refused correctly" are different metrics | `appropriate_abstention_rate` vs `unsupported_answer_share` |
| Checking citations against cited text, not model claims | Models cite confidently and wrongly | `citation_check` |
| Dev/test discipline for prompts | A prompt tuned on test is a leaked label | v1 → v2 on dev only; test run once |
| Paired tests and the independence of units | 14 vs 1 per pair becomes 6 vs 1 per distinct input | `mcnemar_exact`; both reported |
| Deduplicating identical model inputs | Cheaper, and it exposes the correlated-unit problem | 349 distinct inputs for 660 test pairs |
| Prompt-injection evaluation design | Knowing what a probe can and cannot show | [04](04-grounded-answers.md) § Planted instructions |

## M5 — Freshness and revocation

| Skill | Why | Done when |
|---|---|---|
| Cache invalidation by version counter | The cache is the easiest place to keep a revoked answer alive | Epoch in every cache entry |
| Defence in depth on cache hits | A missed epoch bump must not serve revoked content | Per-hit `can_read` on cited docs |
| Transactional content replacement | Readers must never see a half-replaced document | `update_document` in one transaction |
| Fail-closed versioning | Stale index data should deny, not serve | `d.version = c.doc_version` join |
| Stating a guarantee precisely | "Next request" is checkable; "fast" isn't | [03](03-freshness-and-revocation.md) § The guarantee |

## Interview talking points, mapped to what was built

| Talking point | Where it's backed |
|---|---|
| "Authorization is a JOIN, not a filter, and here's why post-filtering leaks." | `search/retrieve.py: AUTHORIZED`; [01](01-architecture.md) Rule 1 |
| "The tests can't be fooled by the SQL, because the oracle doesn't use it." | `identity/oracle.py`; exhaustive agreement test |
| "Revocation works on the next request because there is nothing to re-index." | `revocation.json`; [03](03-freshness-and-revocation.md) |
| "Vector beat hybrid here, and I kept that result." | `eval.json` § `test.retrieval` |
| "The LLM's gain shrinks from p = 0.001 to p = 0.125 once correlated pairs are collapsed." | `llm_answers_test_v2.json` § `paired` |
| "My injection probe can't fail in the interesting way, and here's the one that could." | [07](07-risks.md) § R4 |
| "Here's where the served system differs from the evaluated one." | [09](09-implementation-notes.md) § 2–3 |
