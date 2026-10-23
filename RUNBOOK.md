# Runbook: the steps that need a daemon, a download or a model server

[`start.md`](start.md) covers everything that runs with no database and no model. The steps below
start a PostgreSQL server, download binaries or model weights, or call project 01's LLM gateway.
The committed `results/` come from §§ 3 and 6 on the GX10 host recorded in
`env/ENVIRONMENT.md`. Re-running them **overwrites** files in `results/summary/` (and adds files to
`results/raw/`), so check `git diff results/` afterwards.

Ports and paths used:

| What | Default | Override |
|---|---|---|
| Main database | `127.0.0.1:20432`, data in `.run/pg/` | `SEARCH_PG_PORT`, `SEARCH_RUN_DIR` |
| Test database | `127.0.0.1:20433`, data in `.run-test/pg/` | set by `tests/conftest.py` |
| Postgres binaries | `.tools/pg/usr/lib/postgresql/16/bin` | `PG_BIN` |
| Token secret | `$SEARCH_RUN_DIR/token_secret` | — |
| API | `127.0.0.1:20080` (by convention in README) | uvicorn flags |
| LLM gateway (project 01) | `http://127.0.0.1:21091` | `AGENT_LLM_URL`, `AGENT_LLM_MODEL` |
| llama.cpp props (weights file name only) | `http://127.0.0.1:21000/props` | hard-coded in `eval/run_llm.py` |

---

## 1. PostgreSQL 16 + pgvector, in user space

```bash
scripts/setup_postgres.sh
```

On Linux this downloads `postgresql-16=16.15-0ubuntu0.24.04.1`, its client, `libpq5` and
`postgresql-16-pgvector` with `apt-get download` (network, no root), and unpacks them into
`.tools/pg`. It is idempotent, and `rm -rf .tools` undoes it. On macOS it only checks for
Homebrew's `postgresql@17` and pgvector and tells you what to install.

Start and stop the server:

```bash
uv run python -m search.db up      # initdb on first run, start, create role/db, CREATE EXTENSION vector, schema
uv run python -m search.db down
```

When run as root, `search/db.py` drops the Postgres processes to `nobody` (uid 65534) with
`setpriv`, because Postgres refuses to run as root.

---

## 2. Embedding model

`search/embed.py` loads `BAAI/bge-base-en-v1.5` with `local_files_only=True`, so it never
downloads at run time. Fetch it once with network access (README § macOS gives this command; the
`hf` CLI comes from `huggingface_hub`, which is not a declared dependency in `pyproject.toml`. On
the GX10 it is on the host's PATH):

```bash
uv run hf download BAAI/bge-base-en-v1.5
```

---

## 3. Ingest, evaluate, revocation demo

```bash
uv run python -m corpus.generate --out .run/corpus
uv run python -m search.ingest .run/corpus      # starts Postgres if needed; OCR, chunk, embed, load
uv run python -m eval.run                       # writes results/summary/eval.json
uv run python -m eval.revocation                # writes results/summary/revocation.json
```

- `search.ingest` truncates all tables, including `answer_cache` and `query_log`, and prints
  `{"documents", "chunks", "ocr_documents"}`. README gives 733 chunks and about 45 s on CPU, but no
  file records either **[verify]**.
- `eval.run` and `eval.revocation` read the corpus from `.run/corpus` (hard-coded) and need the
  database already running. `search.ingest` leaves it running; after a reboot run
  `search.db up`.
- `eval.revocation` revokes and then restores real grants and memberships. Don't run it against a
  database you are demonstrating from at the same time.

Expected on the committed run: [docs/05](docs/05-evaluation.md) for `eval.json`, and
[docs/03](docs/03-freshness-and-revocation.md) for `revocation.json`.

---

## 4. Integration tests

```bash
uv run pytest
```

Runs 8 integration tests in `tests/test_security.py` plus the 7 unit tests. The session fixture
generates a fresh corpus into `.run-test/corpus`, ingests it into a **separate** Postgres on port
20433, and stops that server at the end, so the tests can revoke and delete freely without touching
`.run/`. Needs § 1 and § 2.

---

## 5. API server, and the same question as two users

```bash
uv run uvicorn search.api:app --host 127.0.0.1 --port 20080
```

There is no token-issuing CLI or endpoint. Tokens come from `identity.tokens.issue()`, which is what
the tests and the evaluation call. The one-liner below calls that function directly. It is not a
repo script, and it must run with the same `SEARCH_RUN_DIR` as the server so it signs with the
same secret:

```bash
TOKEN_CAROL=$(uv run python -c "from identity.tokens import issue; print(issue('carol'))")
TOKEN_ALICE=$(uv run python -c "from identity.tokens import issue; print(issue('alice'))")
for T in "$TOKEN_CAROL" "$TOKEN_ALICE"; do
  curl -s -X POST http://127.0.0.1:20080/search -H "Authorization: Bearer $T" \
       -H 'Content-Type: application/json' -d '{"query": "What is the salary band for L5 engineers?"}'
  echo
done
```

carol (in `hr`) should get "Engineering salary bands" among her results, and alice should not.
That is what `test_two_users_same_question_get_only_their_evidence` asserts. Then try
`GET /citations/{chunk_id}` and `GET /docs/{doc_id}/preview` for that document with alice's token:
both MUST be 404.

This is the manual version of the open role-dependent demo ([docs/06](docs/06-roadmap.md) item 1).

`/answer` uses the extractive answerer by default, at the evaluated threshold (0.7, read from
`results/summary/eval.json`). Start the server with `ANSWER_GENERATOR=llm` to
use the LLM (needs § 6's gateway).

---

## 6. LLM answers and the injection analysis

Needs project 01's gateway on `AGENT_LLM_URL` (default `http://127.0.0.1:21091`) serving
`qwen2.5-7b-instruct`. The committed runs used
`models/qwen2.5-7b-instruct-a09a354-Q4_K_M.gguf` (`meta.model_file` in the summaries).

```bash
AGENT_LLM_URL=http://127.0.0.1:21091 uv run python -m eval.run_llm --split dev
AGENT_LLM_URL=http://127.0.0.1:21091 uv run python -m eval.run_llm --split test
uv run python -m eval.llm_injection results/raw/<dev run>.jsonl results/raw/<test run>.jsonl
```

- **Dev for any prompt work, test once.** That is how prompt v2 was chosen
  (`answer/llm.py: PROMPT_VERSION`; PLAN.md M4 decision). Running test again after changing the
  prompt turns the test split into a tuning set.
- `--workers` (default 4) should match `llama-server --parallel`.
- Each run writes every call, failures included, to `results/raw/<UTC stamp>-llm-<split>.jsonl`
  and overwrites `results/summary/llm_answers_<split>_<prompt version>.json`.
- `eval.run_llm` reads `chosen_threshold` from `results/summary/eval.json`, so run § 3 first.
- `eval.llm_injection` needs no model. It replays the raw files, but it reads `chunks` from the
  database to find the planted chunk's id, so it needs the same ingestion as the runs it replays.
  The committed runs are `results/raw/20261008T111634Z-llm-dev.jsonl` and
  `results/raw/20261008T112349Z-llm-test.jsonl`.
- Committed test-run cost for reference: 349 calls, 370.2 s wall, p50 3.97 s / p95 6.26 s per call
  (`llm_answers_test_v2.json` § `meta`, `calls`), on a GPU README describes as shared.

---

## 7. Clean up

```bash
uv run python -m search.db down     # stop the main database
rm -rf .run .run-test               # generated corpora, databases, token secret (all git-ignored)
rm -rf .tools                       # unpacked Postgres
```
