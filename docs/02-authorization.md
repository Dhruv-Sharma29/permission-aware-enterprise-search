# 02 — Authorization

Who is asking, what they may read, and how every surface asks the same question the same way.

## The permission model

Two kinds of grant, both rows in `doc_acl` (`search/db.py`):

| Grant | Row | Example from the corpus (`corpus/generate.py`) |
|---|---|---|
| Group | `(doc_id, 'group', group_id)` | "Engineering salary bands" → `hr` |
| User, by name | `(doc_id, 'user', user_id)` | "Contractor onboarding" → `all_staff` **and** user `ken` |

A user can read a document if it isn't deleted and either grant applies through their **current**
memberships. That is the entire rule, in one SQL function:

```sql
CREATE OR REPLACE FUNCTION readable_docs(p_user text) RETURNS TABLE (doc_id text) LANGUAGE sql STABLE AS $$
    SELECT d.doc_id FROM documents d
    WHERE NOT d.deleted AND EXISTS (
        SELECT 1 FROM doc_acl a
        WHERE a.doc_id = d.doc_id AND (
            (a.principal_kind = 'user' AND a.principal_id = p_user) OR
            (a.principal_kind = 'group' AND a.principal_id IN
                (SELECT m.group_id FROM memberships m WHERE m.user_id = p_user))))
$$;
```

There are no deny rules, no role hierarchy and no inheritance between groups. The overlap is in
who belongs to what, which makes it testable:

| User | Groups | Why they're in the fixture |
|---|---|---|
| alice | all_staff, engineering | Baseline engineer |
| bob | + eng_managers | Sees HR calibration guidelines through a non-HR group |
| carol | all_staff, hr | Sees salary bands and personnel records |
| dan | all_staff, finance | Finance tables, scanned invoices |
| erin | all_staff, sales | Contract summaries but not liability caps |
| frank | all_staff, legal | Full contracts |
| grace | all_staff, executives | Board minutes, security reports, budgets |
| heidi | all_staff, engineering, security | Security reports via a second group |
| ivan | all_staff, sales, eng_managers | Cross-department overlap |
| judy | all_staff, hr, eng_managers | Two independent paths to the same document |
| ken | *(none)* | Contractor: only what is shared with him by name |
| lena | all_staff, finance, executives | Two paths to finance documents |

Source: `USERS` in `corpus/generate.py`. Judy matters for revocation: removing one of her two
paths to "Performance calibration guidelines" must not be mistaken for revoking her access, which
is why the third revocation scenario removes both grants ([03](03-freshness-and-revocation.md)).

## Identity: only from the token

`identity/tokens.py` issues HS256 JWTs carrying `sub`, `iat` and `exp` (default TTL 3600 s). The
secret is 32 random bytes in `$SEARCH_RUN_DIR/token_secret`, created on first use with mode 600.

`search/api.py: caller()`:

1. MUST reject a missing or non-`Bearer` header with 401.
2. MUST reject a token that fails signature, expiry or required-claim (`sub`, `exp`) checks with 401.
3. MUST reject a `sub` that isn't a row in `users` with 401, so a validly signed token for an
   unknown user gets nothing. (There is no user-removal operation in the write path yet.)
4. MUST NOT read any user, role or group field from the request body. The `Query` model doesn't
   even declare them, so FastAPI drops them.

Groups are never in the token, so a token can't carry stale or forged roles. The test sends
`{"user": "carol", "role": "hr", "user_id": "carol"}` in the body with alice's token and checks
every result against alice's permissions (`test_identity_comes_only_from_the_token`).

**What this is not.** There is no token revocation list and no refresh flow: a token stays valid
until expiry, but it only proves *who* is asking, and what that user can read is looked up per
request. Issuing tokens has no CLI or endpoint. Tests and the evaluation call `issue()` directly
([`RUNBOOK.md`](../RUNBOOK.md) § 5).

## Every surface asks the same question

| Surface | Code | How it's authorized | Unauthorized result |
|---|---|---|---|
| Keyword retrieval | `retrieve.keyword` | `JOIN readable_docs(user)` + current version | Row never ranked |
| Vector retrieval | `retrieve.vector` | Same join, exact `<=>` scan over the joined set | Row never ranked |
| Hybrid | `retrieve.hybrid` | Fuses only the two authorized lists | — |
| `/search` snippets | `api.search_endpoint` | First 300 chars of authorized hits | — |
| `/answer` | `answer.service.ask` | Generator receives authorized hits only | Abstention |
| Cached answer | `answer.service.ask` | Epoch match **and** `can_read` per cited doc | Recomputed |
| `/citations/{chunk_id}` | `api.citation` | Current-version chunk, then `can_read` | 404 |
| `/docs/{doc_id}/preview` | `api.preview` | `can_read`, then first current chunk | 404 |
| Query log | `api.log` | Stores hash + doc ids, never text | — |

Two details worth defending:

- **404, not 403**, for citations and previews. A 403 confirms the document exists. The body is
  the same `"not found"` in both cases, so a client probing chunk ids learns nothing.
- **Snippets are chunk text.** The content-leak check in the evaluation searches the hit text for
  forbidden values, so it covers what `/search` returns ([05](05-evaluation.md)).

## The independent oracle

`identity/oracle.py` reads `principals.json`, `documents.json` and `scan_truth.json` and answers
three questions in plain Python:

| Method | Used for |
|---|---|
| `can_read(user, doc_id)` | Is a returned or cited document allowed? Is a question answerable for this user? |
| `readable(user)` | The set behind `forbidden_values` |
| `forbidden_values(user)` | Every planted fact value that appears **only** in documents this user can't read. Any appearance in any output is a leak |

It supports `deleted` and `revoked_grants` overrides so a test can describe the expected state
after a change. The current integration tests restore state after each change, so those overrides
are not exercised yet ([09](09-implementation-notes.md) § 7).

**Why it exists.** If the tests compared the API against `readable_docs()`, a wrong join would
agree with itself. The oracle shares no code with the SQL, and
`test_sql_authorization_matches_independent_oracle_for_every_user_and_document` checks all
12 × 235 = 2,820 (user, document) pairs. The mismatch list MUST be empty.

**Why `forbidden_values` works.** The generator makes every fact value unique across the corpus
(`Builder.unique`). So "did this user see something they shouldn't?" reduces to "does any value in
`forbidden_values(user)` occur in the output string?". The limit is that only *planted fact values*
are detectable. A paraphrase, a title or a non-fact sentence from a forbidden document wouldn't be
caught by this check. It is caught by the document-level checks instead (`retrieval_leaks.doc`,
`citation_leaks`), which work for any content ([07](07-risks.md) § R2).

## Failure modes designed against

| Failure | Where it would appear | What stops it | Test |
|---|---|---|---|
| Post-filter starves a narrow user | ken's results | Join before `LIMIT` | Indirectly: recall@5 is averaged over every user's answerable pairs ([05](05-evaluation.md)); no per-user breakdown is reported |
| Group membership change not seen | Retrieval, cache | Read at query time + epoch | `test_revocation_after_a_cached_answer` |
| Forged identity in body | All endpoints | `caller()` ignores the body | `test_identity_comes_only_from_the_token` |
| Guessable citation link | `/citations/{id}` | `can_read` per request | `test_citation_links_and_previews_cannot_bypass_authorization` |
| Deleted doc still readable | Preview, retrieval | `NOT d.deleted` in `readable_docs()` + chunks dropped | `test_document_update_removes_old_content_and_delete_removes_document` |
| Instruction in a document widens access | `/answer` | Access decided before text is selected | `test_injected_document_grants_no_access` |
| Query text in logs | `query_log` | Hash only | `test_logs_store_no_query_text_or_content` |
