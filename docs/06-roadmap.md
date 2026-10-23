# 06 — Roadmap

Milestones from [`PLAN.md`](../PLAN.md), with status as recorded there on 2026-10-08 and checked
against the code and `results/`. "Done" means the exit criterion has evidence in the repo, not
that every sub-bullet was built as planned ([09](09-implementation-notes.md)).

## What changed from the original plan

- **Store:** pgvector chosen at the M2 decision gate ("fewer moving parts"). OpenSearch was not
  tried.
- **Answer order:** extractive baseline first, LLM second, behind the same contract. That way
  authorization and retrieval were measured before any model was involved.
- **Layout:** flatter than planned. Cache logic lives in `answer/service.py`, ingestion in
  `search/ingest.py`, ACL fixtures in the generator ([09](09-implementation-notes.md) § 1).
- **Revocation guarantee:** written as [03](03-freshness-and-revocation.md), not
  `docs/revocation.md`.

## M1 — Corpus + identity · **Done**

Exit: permission expectations are explicit and machine-checkable.

| Acceptance | Evidence |
|---|---|
| 200–500 seeded docs incl. tables and scanned pages | 235 docs, 10 scanned, 10 table documents; seed 7 (`corpus/generate.py`) |
| ≥ 3 roles with deliberate overlap | 12 users, 9 groups; bob, ivan and judy hold cross-department groups ([02](02-authorization.md)) |
| 100–200 questions with per-role expectations | 114 questions × 12 users; answerability from the oracle |
| `can_read` testable without a model | `identity/oracle.py` |

## M2 — Keyword baseline with authz · **Done**

Exit: cross-role access tests pass.

| Acceptance | Evidence |
|---|---|
| Filter inside the retrieval query | `AUTHORIZED` join in `search/retrieve.py` |
| Citation preview re-checks authorization | `api.citation`, `api.preview`; link test |
| Identity from verified token only | `caller()`; identity test |
| Cross-role tests | 8 integration tests in `tests/test_security.py` (README § Tests) |

## M3 — Hybrid retrieval · **Done, with one item not attempted**

| Acceptance | Evidence |
|---|---|
| Embedding model on arm64, batched at ingestion | BGE-base-en-v1.5 on CPU, batch 32 (`search/embed.py`; `env/ENVIRONMENT.md`) |
| Keyword vs vector vs hybrid on held-out questions | `eval.json` § `test.retrieval` ([05](05-evaluation.md)) |
| Reranker attempt recorded either way | **Not attempted, not recorded.** Open |

## M4 — Grounded answers · **Done**

| Acceptance | Evidence |
|---|---|
| Authorized passages only; citations per claim | `answer/llm.py`, `answer/extractive.py` |
| Citation checker | `eval/run.py: citation_check` (numbers only; limit in [04](04-grounded-answers.md)) |
| Abstention scored | 100% appropriate abstention on 504 test pairs, both answerers |
| Planted instruction grants nothing | `eval/llm_injection.py`: 62 calls, 0 echoes, 0 forbidden values |

## M5 — Freshness + revocation · **Done**

| Acceptance | Evidence |
|---|---|
| Versioned docs and ACLs; written guarantee | `documents.version`, `chunks.doc_version`, epoch; [03](03-freshness-and-revocation.md). ACLs are not versioned. They are current-state rows and the epoch versions the whole permission state |
| Stale metadata → deny | Version join in every retrieval/citation/preview query |
| Delete/replace removes old chunks | `update_document`, `delete_document`; freshness test |
| Permission-update delay measured | `revocation.json` (per-request; [03](03-freshness-and-revocation.md)) |
| Revocation demo | `eval/revocation.py`, 3 scenarios |

## M6 — Optional Azure AI Search variant · **Not started**

SPEC.md says some of Azure AI Search's document-level access features are marked preview and need
current verification. Nothing in this repo has checked them: **[verify]** the current API version
and preview status before starting. The local version MUST stay runnable.

## Definition of done (PLAN.md)

| Item | State |
|---|---|
| Role-dependent demo | **Open.** Behaviour is tested and recorded per user (`llm_answers_test_v2.json` § `disagreements` lists users per outcome), but there is no standalone demo |
| Revocation demo | Done (`results/summary/revocation.json`) |
| Retrieval baseline comparison | Done ([05](05-evaluation.md)) |
| Authorization test results | Done (8 integration tests; exhaustive SQL-vs-oracle agreement) |
| Residual-risk section | Done (README § Security model; [03](03-freshness-and-revocation.md), [07](07-risks.md)) |

## Next, in order

1. **Role-dependent demo.** The smallest version that closes the item: one script that issues
   tokens for, say, alice, carol and ken, asks the same questions through the API, and prints each
   user's results and citations side by side. Everything it needs exists (`identity.tokens.issue`,
   `/search`, `/answer`). Only the script is missing.
2. ~~**Serve at the evaluated threshold.**~~ Done 2026-10-23: `/answer` reads `chosen_threshold`
   (0.7) from `eval.json` ([09](09-implementation-notes.md) § 2).
3. **Vector-only answer path, decided on dev.** Test showed vector ahead of hybrid; dev didn't
   agree on MRR. Decide on dev, report on test once, as was done for the prompt.
4. **Reranker attempt**, recorded either way (closes M3).
5. **Harder corpus**: more archived versions, role-conflicting questions in both splits, an
   instruction that redirects answers using data the user *can* read ([07](07-risks.md)).
6. **Purge stale cache rows on epoch bump** ([03](03-freshness-and-revocation.md) § gaps).
7. M6, optional.

## The cut line

If only one more thing gets done, it is item 1. It is the only open definition-of-done item, and it
is the artefact a reviewer can run in a minute. Items 2 and 3 are correctness-of-claims fixes and
come next. Everything below them is improvement, not completion.
