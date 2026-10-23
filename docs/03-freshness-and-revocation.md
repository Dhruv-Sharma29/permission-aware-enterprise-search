# 03 — Freshness and revocation

PLAN.md M5 asked for the revocation guarantee to be written down. This is that document: what is
guaranteed, the mechanism, what was measured, and what the guarantee does not cover.

## The guarantee

> **A permission or content change applies to every request whose authorization queries run after
> the change commits. No cached answer, stale chunk or deleted document is served after that point.
> Nothing already delivered is recalled.**

Concretely:

| Change | API (`search/ingest.py`) | Effect on the next request |
|---|---|---|
| Remove a user from a group | `set_membership(user, group, False)` | `readable_docs()` no longer includes the group's documents; cached answers miss |
| Revoke a per-user grant | `set_acl(doc, 'user', u, False)` | Same |
| Remove a group grant | `set_acl(doc, 'group', g, False)` | Same, for every member who had no other path |
| Replace a document | `update_document(doc, body)` | Only the new version's chunks match; old text unretrievable |
| Delete a document | `delete_document(doc)` | Excluded from `readable_docs()`; chunks dropped; preview 404 |
| Grant or add membership | `set_acl(..., True)`, `set_membership(..., True)` | New access visible on the next request; epoch bump also discards cached abstentions |

Each operation runs in one transaction that also calls `bump_epoch()`, so the change and the epoch
increment are visible together or not at all.

## Mechanism

### 1. Permissions are never copied

`readable_docs(user)` reads `doc_acl` and `memberships` directly ([02](02-authorization.md)). There
is no ACL in the chunk rows and no ACL snapshot in the cache, so there is nothing to re-index after
a permission change. This is what makes "next request" possible at all.

### 2. Versioned chunks fail closed

Every chunk records `doc_version`. Retrieval, citations and previews all join on
`d.version = c.doc_version` (`search/retrieve.py: AUTHORIZED`, `search/api.py`). `update_document`
increments the version, deletes the old chunks and inserts the new ones in one transaction, so a
reader sees the old version or the new one, never a mix. The version join is defence in depth: if
a chunk ever survived a replacement, it would stop matching instead of being served. That is the
"stale index metadata → deny until reconciled" item from PLAN.md M5.

### 3. The answer cache can't outlive a change

`answer_cache` rows are keyed `(user_id, query_sha)` and record the epoch at write time and the
cited doc ids (`answer/service.py`). A row is served only if **both** hold:

1. `row.epoch == current epoch` (any change anywhere bumps it), and
2. `can_read(user, d)` is true for every cited `d`, re-checked on this request.

Either condition alone would be enough for the revocation scenarios tested here. Having both means
a bug that forgets to bump the epoch still doesn't serve a revoked citation.

Three more rules keep the cache honest:

- The key includes the generator and prompt version for LLM answers
  (`query_sha(f"{generator}:{PROMPT_VERSION}:{query}")`), so switching generators or prompts never
  serves the other one's answer.
- A failed model call is never cached (`if use_cache and not result.get("model_error")`).
- The epoch is read **before** retrieval. If a change commits mid-request, the answer is stored
  under the older epoch and is already invalid for the next request. That is conservative by
  construction.

### Why one global epoch

Per-document invalidation would need to know, for every cached answer, which documents *could have*
changed its outcome, including documents that were not retrieved because the user couldn't read
them. A grant can change an abstention into an answer. The global counter avoids that question
entirely. The cost is that every change invalidates every cached answer for every user. At this
scale that's free. At enterprise write rates it would make the cache useless (see
[07](07-risks.md) § R6).

## What was measured

`eval/revocation.py` runs three scenarios against the ingested corpus: ask, ask again (must be a
cache hit), commit the change, ask once more, then restore. Results from
`results/summary/revocation.json`:

| Scenario | User | Answered before | Cache hit before | Cited after | In results after | From cache after | First post-change request |
|---|---|---|---|---|---|---|---|
| Group membership removed (`eng_managers`) | bob | yes | yes | no | no | no | 75.5 ms |
| Per-document user grant revoked ("Contractor onboarding") | ken | yes | yes | no | no | no | 58.5 ms |
| Group grant removed from document (`hr` and `eng_managers`) | judy | yes | yes | no | no | no | 61.2 ms |

How to read the last column: it is the wall time of the first `ask()` after the commit (retrieval,
answering and cache write included). It is **not** a propagation delay waiting for something to
catch up, because nothing has to catch up. The evidence that the change took effect on the first
request is the `cited after = no` and `from cache after = no` columns for that same request.
`requests_until_effective: 1` in the JSON is a constant written by the script, not a measured count
([09](09-implementation-notes.md) § 5).

The integration test `test_revocation_after_a_cached_answer` checks the same property through the
HTTP API for bob. The answer after revocation MUST be an abstention, MUST NOT be cached, and MUST
contain neither a forbidden value nor the previous answer text.
`test_document_update_removes_old_content_and_delete_removes_document` checks freshness. After an
update the old "days per week" value is absent from snippets and the new one is present. After a
delete the document is absent from results and its preview is 404.

## What the guarantee does not cover

| Gap | Why it's there | Consequence |
|---|---|---|
| Information already delivered | A response is a copy the server no longer controls | A revoked user keeps what they saw before the change. SPEC.md calls this out explicitly |
| In-flight requests | A request whose SQL ran before the commit uses the old permissions | Bounded by one request's latency (tens of ms here) |
| Stale cache rows are not deleted | Invalidation is by check-on-read, not by purge | `answer_cache` still holds answer text for a user who lost access. It is never served, but it is at rest in the database. Purging on epoch bump would close this |
| `query_log` keeps doc ids | Audit needs them | A log reader can see which confidential document ids a user retrieved before revocation (no content) |
| Concurrency | Tests and the demo are sequential | The reasoning above about mid-request commits comes from reading the code; no concurrent-writer test exists |
| Source-of-truth correctness | Permissions are as right as `doc_acl` and `memberships` | A wrong group sync upstream is served faithfully |
| Token lifetime | Tokens are valid until `exp` | Not a revocation gap for documents (groups are looked up live), but a user can't be locked out before expiry except by removing the `users` row, which has no write-path operation yet |
