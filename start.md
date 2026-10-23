# Start here

A guide to running the permission-aware search and to reading what it produces. The
[README](README.md) makes the case for the design; [docs/](docs/README.md) is the design record;
this file gets you to a result.

Steps 1–3 need **no database and no model**. Everything that needs the Postgres daemon, the
embedding model download or the LLM gateway is in [`RUNBOOK.md`](RUNBOOK.md). Those steps start
processes and overwrite files in `results/`.

---

## 1. Install

Linux (developed on aarch64 Ubuntu 24.04, Python 3.12; `env/ENVIRONMENT.md`). The venv reuses the
host's torch and transformers:

```bash
uv venv --system-site-packages --python /usr/bin/python3 && uv sync
```

macOS, or any machine without a system torch, uses the `standalone` extra from `pyproject.toml`
(README § macOS; never run on a real Mac):

```bash
uv sync --extra standalone
```

---

## 2. See it work without the database or a model

### Generate the corpus

```bash
uv run python -m corpus.generate --out .run/corpus
```

```
235 documents, 114 questions, 10 scanned, 1 archived -> .run/corpus
```

Seeded (`--seed 7` by default), so the output is identical every time. Open
`.run/corpus/principals.json` (12 users, 9 groups) and look up a document in `documents.json`. Its
`groups` and `users` fields are its ACL. The 10 scanned documents have `"body": null` and a
`scan` image path, and their true text sits in `scan_truth.json` for the oracle, never for
ingestion. Everything the evaluation calls "allowed" is computed from these three files by
`identity/oracle.py`, without SQL.

### Run the tests that need nothing

```bash
uv run pytest tests/test_llm_answer.py
```

```
7 passed
```

These check the LLM answer contract with hand-written replies. Out-of-range citation numbers are
dropped, a model error counts as an abstention, abstaining discards any text, planted instructions
are counted, the citation check catches a number missing from the cited passage, the exact
McNemar helper is right, and an unknown generator is rejected.

**Don't run bare `uv run pytest` yet.** If `.tools/pg` exists it also runs the 8 integration
tests, which start their own Postgres ([`RUNBOOK.md`](RUNBOOK.md) § 4). If it doesn't exist they
are skipped with "run scripts/setup_postgres.sh".

### Read the committed results

| File | What it holds |
|---|---|
| `results/summary/eval.json` | Retrieval recall@5 / MRR@10 / leaks / latency per mode, dev and test; extractive answers at 4 thresholds; `chosen_threshold` and `test_answers` |
| `results/summary/llm_answers_test_v2.json` | LLM vs extractive on the same test hits: rates, citation checks, paired McNemar, every disagreement with the users involved |
| `results/summary/llm_answers_dev_v1.json`, `..._dev_v2.json` | The prompt change, on dev only |
| `results/summary/llm_injection.json` | Every model call whose passages included the planted instruction |
| `results/summary/revocation.json` | Three revocation scenarios |
| `results/raw/*.jsonl` | Every LLM call: question id, chunk ids, users, raw reply, latency |

---

## 3. Read the output

What to look at first in `eval.json`, and why:

1. **`test.retrieval_leaks`**: `doc` and `content` for every mode. These MUST be 0. `doc` counts
   hits the oracle says the user can't read. `content` counts the user's forbidden planted values
   found in hit text.
2. **`test.retrieval`**: recall@5 and MRR@10. On the committed run vector is 1.000 / 0.910 and
   hybrid 0.962 / 0.886. Compare with `dev.retrieval` before concluding anything
   ([docs/05](docs/05-evaluation.md)).
3. **`chosen_threshold`** (0.7) and **`test_answers`**: the extractive baseline at the threshold
   chosen on dev. `appropriate_abstention_rate` and `unsupported_answer_share` are separate
   numbers on purpose: one measures refusing when the user can't read the source, the other
   answering wrongly.

In `llm_answers_test_v2.json`, read **`paired.supported.distinct_inputs`**, not just `p_pairs`.
Per pair the LLM wins 14 vs 1 (p = 0.001). Per distinct input it is 6 vs 1 (p = 0.125), and the
distinct input is the honest unit.

In `revocation.json`, the fields that prove the guarantee are `doc_cited_after: false` and
`served_from_cache_after: false`. `delay_to_first_correct_response_ms` is just that request's
latency, and `requests_until_effective` is a constant ([docs/03](docs/03-freshness-and-revocation.md)).

---

## 4. Run the whole thing

Needs the Postgres daemon and the embedding model. In order, from [`RUNBOOK.md`](RUNBOOK.md):

| Step | RUNBOOK § | Needs |
|---|---|---|
| Unpack Postgres + pgvector | 1 | Network once (`apt-get download`) |
| Embedding model in the HF cache | 2 | Network once |
| Ingest, evaluate, revocation demo | 3 | Postgres daemon |
| Integration tests | 4 | Postgres daemon (its own instance) |
| API server and a request as two different users | 5 | Postgres daemon |
| LLM answers and the injection analysis | 6 | Project 01's gateway + llama.cpp |

---

## 5. Where to read next

- How permissions are decided, and why the tests use an oracle: [docs/02](docs/02-authorization.md).
- What "revocation on the next request" means precisely, and what it doesn't cover:
  [docs/03](docs/03-freshness-and-revocation.md).
- Every published number and its source file: [docs/05](docs/05-evaluation.md).
- What isn't done (role-dependent demo, reranker, M6): [docs/06](docs/06-roadmap.md).
- Where the served system differs from the evaluated one: [docs/09](docs/09-implementation-notes.md) § 2–3.

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `numpy.core.multiarray failed to import` | numpy 2.x in the venv against the host's numpy-1 torch/scipy | Keep the `numpy==1.26.4` pin; re-run `uv sync` (`env/ENVIRONMENT.md`) |
| `OSError` loading `BAAI/bge-base-en-v1.5` | `search/embed.py` loads with `local_files_only=True` | Download the model once ([`RUNBOOK.md`](RUNBOOK.md) § 2) |
| Integration tests skipped with "run scripts/setup_postgres.sh" | No `.tools/pg/usr/lib/postgresql/16/bin/postgres` | [`RUNBOOK.md`](RUNBOOK.md) § 1. On macOS the skip check looks only for the Linux path, so these tests skip even with Homebrew Postgres ([docs/09](docs/09-implementation-notes.md) § 12) |
| `psycopg.OperationalError` … port 20432 | The database isn't running (`eval.*` don't start it; `search.ingest` does) | `uv run python -m search.db up` |
| Port 20432 or 20433 already in use | Another instance | Set `SEARCH_PG_PORT` |
| `eval.run_llm` scores the extractive baseline at an unexpected threshold | It reads `chosen_threshold` from whatever `results/summary/eval.json` is on disk | Run `uv run python -m eval.run` first after re-ingesting |
| 401 "invalid token" from the API | Token signed with a different `token_secret` | Issue tokens with the same `SEARCH_RUN_DIR` the server uses ([`RUNBOOK.md`](RUNBOOK.md) § 5) |
| Scanned-page text looks different on macOS | Font fallback differs (`corpus/generate.py: FONT_CANDIDATES`) | Expected: published numbers come from the Linux host with DejaVuSans |
