# 00 — Charter

## The problem

Enterprise search over mixed-sensitivity documents has one requirement that ranking quality can't
compensate for: a user must never receive a passage, an answer, a citation, a preview, a cached
result or a log line derived from a document they aren't allowed to read. That has to hold for
two users asking the same question, after a document is replaced, and immediately after access is
revoked.

Bolting a permission check onto the output of a generic RAG pipeline doesn't meet that. A
post-filter on a global top-k leaks through counts and timing and returns nothing useful to users
with narrow access. An ACL copied into the index goes stale until re-indexing. A cache keyed only
on the query serves yesterday's permissions. And if unauthorized text ever reaches the model, its
behaviour becomes part of the security boundary.

This project builds the alternative and measures it: authorization decided in trusted server code
**before** any text is selected, with every surface checked against an independent oracle.

## What it does

**Input:** a bearer token (HS256, `sub` + expiry) and a natural-language question.

**Output:**
1. `/search`: ranked chunks from documents the caller can read, with snippets (`search/api.py`).
2. `/answer`: one cited answer or an explicit abstention, produced by the extractive baseline or
   the LLM answerer from the caller's authorized passages only (`answer/service.py`).
3. `/citations/{chunk_id}` and `/docs/{doc_id}/preview`: re-authorized on every request, 404 for
   anything the caller can't read.
4. A metadata-only query log: user, endpoint, query hash, returned doc ids, latency.

## Scope

### In
- A seeded synthetic corpus for a fictional company: 235 documents including 10 OCR-only scanned
  pages and 10 table documents; 12 users across 9 overlapping groups (one, a contractor, in no group);
  114 questions asked by every user (`corpus/generate.py`; counts from the generated corpus and
  `results/summary/eval.json`).
- Keyword (Postgres full-text), vector (BGE-base + pgvector) and hybrid (RRF) retrieval, all
  joined to `readable_docs(user)`.
- Extractive and LLM-written answers with citations and abstention.
- Document versioning, update, delete, grant/revoke and membership changes, all bumping one epoch.
- Evaluation over every (question, user) pair, plus integration tests for the surfaces the
  evaluation doesn't cover.

### Out (deliberately, not from running out of time)
- **Real company documents.** Leak detection works because every fact value is unique across the
  corpus (`corpus/generate.py` docstring). Real documents would make "did a forbidden value appear?"
  unanswerable, and they'd bring a data-handling problem that is orthogonal to the thesis.
- **Multiple identity providers, OAuth/OIDC flows.** The thesis is *where* identity is trusted (the
  token's `sub`, never the body), not how it is federated. One local issuer proves that.
- **A document-management UI.** SPEC.md § Keep out of the first version.
- **Approximate vector indexes.** Filtered ANN can return fewer than k authorized rows
  ([01](01-architecture.md) § Decisions). At 235 documents an exact scan over the authorized set
  is fast enough, so the correctness question doesn't have to be traded against speed.
- **Azure AI Search.** Optional M6. The local version is the one that has to stay runnable
  ([06](06-roadmap.md)).

### Why this scoping holds up in an interview

Each narrowing keeps one property checkable. A synthetic corpus with unique planted values makes
content leaks a string search instead of a judgement. A single local token issuer keeps the
identity test ("forged body fields are ignored") about the server, not about an IdP. An exact
vector scan keeps "ranking only sees authorized chunks" literally true rather than "true unless the
ANN filter drops rows". Widen any of them and the security claims turn into assertions nobody can
test.

## Non-goals

- **Proving the system leak-proof.** The evaluation is finite. "0 observed leaks in N tests" is
  the strongest honest claim (PLAN.md § Leak test matrix).
- **State-of-the-art retrieval quality.** The corpus is templated and the questions are generated
  from the documents. Retrieval numbers compare modes on this corpus. They are not a benchmark
  ([07](07-risks.md) § R1).
- **Model-independent answer quality.** The LLM is one 7B model at temperature 0. The point is that
  authorization doesn't depend on it, not that it is the best answerer.
- **Recalling delivered information.** Revocation can't erase what a user already received
  legitimately (SPEC.md § Definition of done).

## Success criteria

| # | Criterion | How it's checked |
|---|---|---|
| S1 | SQL authorization agrees with an independent oracle for every user × document | `tests/test_security.py::test_sql_authorization_matches_independent_oracle_for_every_user_and_document` |
| S2 | No unauthorized document or confidential value in retrieval results, any mode | `eval/run.py`: `retrieval_leaks` = 0/0 for all 4 modes, dev and test (`results/summary/eval.json`) |
| S3 | No confidential value in answers, no unauthorized citation | `content_leaks` / `citation_leaks` = 0 for extractive (`eval.json`) and LLM (`llm_answers_*.json`) |
| S4 | Identity comes only from the token; body fields are ignored | `test_identity_comes_only_from_the_token` |
| S5 | Citation links and previews can't bypass authorization | `test_citation_links_and_previews_cannot_bypass_authorization` (404 for alice, 200 for carol) |
| S6 | A revocation after a cached answer takes effect on the next request | `test_revocation_after_a_cached_answer`; `eval/revocation.py`: 3 scenarios, `doc_cited_after` false, `served_from_cache_after` false (`results/summary/revocation.json`) |
| S7 | Update and delete remove outdated retrievable content immediately | `test_document_update_removes_old_content_and_delete_removes_document` |
| S8 | Logs hold no query text or content | `test_logs_store_no_query_text_or_content` |
| S9 | A planted instruction grants no access | `test_injected_document_grants_no_access`; `eval/llm_injection.py`: 0 echoes, 0 forbidden values in 62 calls (`results/summary/llm_injection.json`) |
| S10 | Keyword vs vector vs hybrid compared on held-out questions | `eval.json` § `test.retrieval` ([05](05-evaluation.md)) |
| S11 | Answers cite or abstain; abstention is scored separately from support | `supported_answer_rate`, `appropriate_abstention_rate`, `unsupported_answer_share` in `eval.json` and `llm_answers_*.json` |
| S12 | A role-dependent demo a reader can run | **Open.** Tested and recorded, not packaged ([06](06-roadmap.md)) |

S1–S11 hold on the recorded runs. S12 is the open definition-of-done item in PLAN.md.

## The honest risk to the thesis

The security results are only as strong as the test suite is hard, and this corpus is easy in
specific ways. Questions are templated from the documents. One archived document exists. The test
split has no role-conflicting or shared-by-name questions. And the content-leak check is a string
match for planted values, so it can't see a paraphrased leak. Zero leaks on an easy, string-matched
suite is a weaker statement than it looks. [07](07-risks.md) § R1–R3 says what limits the claim and
what would strengthen it.
