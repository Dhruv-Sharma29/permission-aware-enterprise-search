# 03 — Permission-aware enterprise search: execution plan

Brief: [SPEC.md](SPEC.md). Core claim to prove: **no passage, answer, citation, preview, cache entry, or log line reaches a user who isn't authorized for its source document.**

**Needs:** 01 endpoint, 02 observability patterns (and Postgres experience if choosing pgvector).

## Progress (2026-10-08)

| Milestone | State |
| --- | --- |
| M1 corpus + identity | **Done.** 235 docs, 12 users / 9 groups, 114 questions; signed tokens; independent oracle |
| M2 keyword baseline with authz | **Done.** Postgres FTS joined to `readable_docs(user)`; cross-role tests pass |
| M3 hybrid retrieval | **Done.** BGE + pgvector, RRF fusion. Vector alone was best here (recall@5 1.000 vs hybrid 0.962) |
| M4 grounded answers | **Done.** Extractive baseline 87.2% supported; LLM (Qwen2.5-7B, prompt v2 chosen on dev) 95.5% on test, both 100% appropriate abstention, 0 leaks, 0 citation failures. Gain not significant per distinct input (6 vs 1, p = 0.125). Model saw planted instructions in 62 calls: 0 echoed, 0 forbidden values |
| M5 freshness + revocation | **Done.** Epoch-keyed cache, version-checked chunks, revocation effective on the next request |
| M6 Azure AI Search variant | Not done (optional) |

## Target layout

```text
03-permission-aware-enterprise-search/
  corpus/
    generator/         fictional company docs (text, tables, scanned pages)
    acl/               users, roles, groups, document → principal grants (versioned)
  identity/            local token issuer + verification (no client-supplied roles)
  ingestion/           parse → OCR → chunk → embed → index (with doc version + ACL version)
  search/              FastAPI: authz filter, keyword, vector, hybrid, rerank (optional)
  answer/              grounded generation, citation checker, abstention
  cache/               answer/result cache keyed on ACL version
  eval/                questions × roles → expected docs / expected access / answerability
  results/raw/  results/summary/  docs/
```

## Milestones

### M1 — Corpus + identity (1–2 weeks, no LLM needed)
- [ ] Generator for 200–500 fictional docs (HR, finance, engineering, legal…) incl. a few tables and scanned-image pages. Seeded.
- [ ] ≥3 roles with **deliberate overlap** (e.g. engineer, manager, HR, finance; managers see some HR docs).
- [ ] 100–200 questions; for each (question, role): relevant docs, allowed docs, expected answer or "unanswerable for this role".
- [ ] Authorization function `can_read(principal, doc_id, at_version)` testable without any model.
- **Exit:** permission expectations are explicit and machine-checkable.

### M2 — Keyword baseline with authz (1 week)
- [ ] Choose store (decision gate: pgvector vs OpenSearch). Default pgvector for fewer moving parts.
- [ ] Filter **inside the retrieval query** (ACL predicate), not after top-k (post-filtering leaks via counts and starves results).
- [ ] Citation preview endpoint re-checks authorization on every request.
- [ ] Identity from verified token only; ignore any role/user field in the request body.
- **Exit:** cross-role access tests pass (forged ID, direct citation link, same question different roles).

### M3 — Hybrid retrieval (1 week)
- [ ] Embedding model with arm64 support; batch during ingestion.
- [ ] Compare keyword vs vector vs hybrid on held-out questions: recall@k, MRR.
- [ ] Reranker only if it improves held-out results — record the attempt either way.

### M4 — Grounded answers (1 week)
- [x] Prompt with authorized passages only; require citations per claim (`answer/llm.py`; schema-constrained reply, passage-number citations).
- [x] Citation checker: each cited passage is authorized (oracle) and supports the claim: every number in the answer appears in a cited passage (`eval/run.py: citation_check`). Limitation: answers without numbers are checked only by the gold match.
- [x] Abstain on insufficient evidence; score appropriate vs inappropriate abstention (100% of 504 test pairs).
- [x] Doc with injected "reveal other documents" instruction → no extra access (`eval/llm_injection.py`: 62 calls with the planted chunk, 0 echoed, 0 forbidden values).
- Decision (2026-10-08): prompt v1 → v2 on dev only (v1 answered "4" for "4 days per week"); test run once with v2. Results: `results/summary/llm_answers_{dev_v1,dev_v2,test_v2}.json`.

### M5 — Freshness + revocation (1–2 weeks)
- [ ] Version docs and ACLs. Write the guarantee in `docs/revocation.md`, e.g. "permission changes apply to the next request; caches keyed on ACL version are invalid immediately".
- [ ] Stale index metadata → treat as deny until reconciled.
- [ ] Doc delete/replace removes old chunks per the stated guarantee.
- [ ] Measure permission-update delay.
- [ ] Revocation demo: answer cached → revoke → same user re-asks → no restricted content.

### M6 — Optional Azure AI Search variant
Verify current API version and which access-control features are still preview. Keep local version runnable.

## Leak test matrix
Check unauthorized exposure on each surface: retrieval results · answer text · citations · previews · result counts/snippets · cache · conversation history · logs. Report "0 observed leaks in N tests", never "leak-proof".

## Risks / open questions
- OCR and embedding libraries on arm64 — validate early in M1.
- Generated corpus may be too easy; include near-duplicate docs across roles and outdated versions.

## Definition of done
- [ ] Role-dependent demo: behaviour is tested ("same question, different users") and visible per user in `results/summary/llm_answers_test_v2.json`, but there is no standalone demo yet
- [x] Revocation demo (`results/summary/revocation.json`: 3 scenarios, effective on the next request)
- [x] Retrieval baseline comparison (README: keyword vs vector vs hybrid)
- [x] Authz test results (8 integration tests pass; exhaustive SQL-vs-oracle agreement)
- [x] Residual-risk section (README, Security model)
