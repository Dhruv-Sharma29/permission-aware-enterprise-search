# Permission-aware enterprise search — Documentation

Hybrid (keyword + vector) search and cited answers over a synthetic company's documents, where the
asking user's permissions are enforced inside the retrieval query, before any passage reaches an
answerer, a cache, a citation link or a log.

These docs are the plan and the design record. Read them in order the first time and use them as
reference afterwards. [`start.md`](../start.md) gets you to a result; [`RUNBOOK.md`](../RUNBOOK.md)
holds the steps that need the Postgres daemon or a model server.

## Reading order

| # | Doc | What it settles |
|---|---|---|
| 00 | [Charter](00-charter.md) | Problem, scope, non-goals, success criteria and how each is checked |
| 01 | [Architecture](01-architecture.md) | Layers, the data model, the request path, the decisions table, repo layout |
| 02 | [Authorization](02-authorization.md) | Identity from tokens, `readable_docs()`, the independent oracle, links and logs |
| 03 | [Freshness and revocation](03-freshness-and-revocation.md) | Epoch, versioned chunks, the cache, the revocation guarantee and its limits |
| 04 | [Grounded answers](04-grounded-answers.md) | Extractive baseline, LLM answerer, citation checks, planted instructions |
| 05 | [Evaluation](05-evaluation.md) | Corpus, splits, metrics, every published number and where it comes from |
| 06 | [Roadmap](06-roadmap.md) | Milestones M1–M6 with honest status, and what is still open |
| 07 | [Risks](07-risks.md) | Thesis vs delivery risks, claim hygiene, the question to have an answer for |
| 08 | [Learning checklist](08-learning-checklist.md) | Skills per milestone and interview talking points mapped to the build |
| 09 | [Implementation notes](09-implementation-notes.md) | Where the code departs from [`PLAN.md`](../PLAN.md), and why |

## The one-paragraph version

Most retrieval-augmented demos treat permissions as a filter on the output: retrieve a global
top-k, drop what the user can't see, hand the rest to a model. That leaks through result counts and
timing, starves users with narrow access, and leaves the model, the cache and the logs holding text
they should never have seen. This project starts from the opposite rule: **authorization is part
of the retrieval query itself** (`JOIN readable_docs(user)` in every keyword and vector query), so
ranking, answering and caching only ever touch authorized chunks. What makes it non-trivial is
everything around that join. Identity comes only from a signed token. Permissions are read at
query time, never copied into the index. A single epoch counter plus per-hit re-authorization makes
cached answers die on the next request after a revocation. Stale chunk versions fail closed. And
the whole thing is scored against an **independent plain-Python oracle**, so a bug in the SQL
can't grade itself. The LLM answerer is deliberately downstream of all of this: it can word an
answer badly, but it cannot widen access.

## Status

**As of 2026-10-08 ([`PLAN.md`](../PLAN.md) § Progress): M1–M5 done, M6 (optional Azure AI Search
variant) not started, and one definition-of-done item open: there is no standalone role-dependent
demo yet.** Same-question-different-user behaviour is tested (`tests/test_security.py`) and visible
per user in `results/summary/llm_answers_test_v2.json`, but nothing packages it as a demo. No
reranker was tried, although the plan asked for the attempt to be recorded either way. The macOS
code paths have never been run on a Mac (README § macOS). See [06](06-roadmap.md) for what is next
and [09](09-implementation-notes.md) for the places where the build departs from the plan. One of
them mattered: until 2026-10-23 the live answer service ran the extractive answerer at 0.5, not
the 0.7 the evaluation chose; it now serves the evaluated threshold.

Headline results (held-out test split, `results/summary/eval.json` and
`results/summary/llm_answers_test_v2.json`): vector recall@5 1.000 vs hybrid 0.962 vs keyword
0.885; 0 unauthorized documents or confidential values returned in any retrieval mode across 1,368
(question, user) pairs; LLM answers 95.5% supported vs 87.2% extractive. That gain is **not**
statistically demonstrated once correlated pairs are collapsed (6 vs 1 distinct inputs,
p = 0.125). Full table and provenance: [05](05-evaluation.md).

## Conventions used in these docs

- **MUST / SHOULD / MAY** are load-bearing. MUST items are acceptance criteria.
- **[verify]** marks a claim not confirmed against a primary source or a results file. Each one
  says what would confirm it.
- Every number cites the file it comes from. `results/summary/*.json` outranks prose. Where the
  README and a results file disagree, the docs use the results file and say so.
- "0 observed leaks" always means *on this test suite*. It is never a security proof.
