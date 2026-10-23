# 03 — Permission-aware enterprise search

**Build third.** Reuse the model endpoint and observability from projects 1–2.

## Objective

Build enterprise search where every retrieved passage, answer, and citation respects the requesting user's document permissions. Support different users asking the same question, document updates, and access revocation.

## Why this fits Amazon and Microsoft

[Azure AI Search's access-control documentation](https://learn.microsoft.com/en-us/azure/search/search-document-level-access-overview) describes permission-aware retrieval; some native capabilities are marked preview and require current verification. [AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html) addresses identity and connections between agents, enterprise tools, and data.

2028+ assessment: search quality, authorization, freshness, and supported answers should remain useful regardless of model choice. Microsoft is the clearest direct alignment for this project.

## Minimum viable scope

- A fictional company corpus of roughly 200–500 documents.
- Three or more roles with intentionally overlapping permissions.
- Text documents plus a small collection of tables and scanned pages.
- Keyword and vector retrieval, citations, and an insufficient-evidence response.
- Permission updates and a repeatable access-revocation demo.

## Proposed stack and architecture

Python, FastAPI, PostgreSQL/pgvector or OpenSearch, a compatible embedding model, optional reranker, OCR, and the local generation endpoint. Use synthetic or appropriately licensed public documents with synthetic permissions.

```text
Documents + ACLs -> Ingestion -> Versioned index
                                      |
Authenticated user -> Server authorization -> Allowed retrieval
                                                   |
                                          Reranking -> Model -> Citations
```

Enforce access in trusted server/retrieval code before passages reach the model or a client. Apply it to citation previews, caches, history, and other endpoints too.

## Implementation stages

1. **Corpus and identity:** create documents, answerable questions, and explicit permission expectations. Exit when authorization can be checked without an LLM.
2. **Keyword baseline:** implement authorized search and citation previews. Exit when cross-role access tests pass.
3. **Hybrid retrieval:** add vectors and compare with keyword search. Add reranking only if it improves held-out results.
4. **Grounded answers:** provide citations and abstain when evidence is insufficient. Validate answer claims separately from retrieval quality.
5. **Freshness and revocation:** implement versioning, permission updates, cache invalidation, and a defined revocation guarantee. Handle stale index metadata conservatively.
6. **Optional Azure variant:** add Azure AI Search and verify current API/version/preview status. Keep the local version runnable.

## Data and evaluation

Create approximately 100–200 questions with relevant documents and expected access for each role. Include unanswerable questions, outdated documents, role-conflicting evidence, and scanned tables.

Report retrieval recall@k, citation correctness, supported-answer rate, appropriate abstention, response latency, and permission-update delay. Measure unauthorized exposure across retrieval, answers, previews, logs, and caches. Zero observed leaks in a finite test suite is not a universal security proof.

## Important tests

- Two users ask the same question and receive only their allowed evidence.
- Permission is revoked after an answer is cached; the cache cannot serve restricted material again.
- A user supplies a forged role/user ID; the backend relies on authenticated identity.
- A document instructs the model to reveal other documents; it grants no new access.
- Direct citation links cannot bypass document authorization.
- Deleting or replacing a document removes outdated retrievable content according to the documented freshness guarantee.

## GX10 and deployment

Batch embeddings and OCR during ingestion; avoid running several large models at once. A small generation model is sufficient to establish authorization and retrieval behavior. Validate native OCR and embedding dependencies on Arm.

## Definition of done

Publish the role-dependent search demo, revocation demo, retrieval baseline comparison, and authorization test results. Document residual risks around stale metadata and previously delivered information: revocation cannot erase content a user already received legitimately.

Deliver the corpus generator, permission fixtures, ingestion/search services, evaluation cases, results, and architecture diagram. Résumé template: “Built permission-aware hybrid search over [actual corpus], measuring [quality result] and validating access revocation across retrieval, answers, and caches.”

## Keep out of the first version

Real confidential company documents, numerous identity providers, and a large document-management UI.
