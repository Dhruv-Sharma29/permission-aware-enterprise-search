"""PostgreSQL (with pgvector) as both index and source of truth for permissions.

Authorization lives in one SQL function, `readable_docs(user)`: direct user grants plus group
grants via current memberships, excluding deleted documents. Every retrieval query joins against
it, so ranking happens only over documents the user may read (no post-filtering of a shared top-k).
ACLs are never copied into the index: a permission change is visible to the very next query.

    python -m search.db up | down
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
RUN = Path(os.environ.get("SEARCH_RUN_DIR", ROOT / ".run"))
def _pg_bin() -> Path:
    """$PG_BIN, else the Linux user-space copy (scripts/setup_postgres.sh), else `pg_config` (Homebrew on macOS)."""
    if os.environ.get("PG_BIN"):
        return Path(os.environ["PG_BIN"])
    local = ROOT / ".tools/pg/usr/lib/postgresql/16/bin"
    if (local / "postgres").exists():
        return local
    pg_config = shutil.which("pg_config")
    if pg_config:
        return Path(subprocess.run([pg_config, "--bindir"], capture_output=True, text=True).stdout.strip())
    return local


PG_BIN, PG_LIB = _pg_bin(), ROOT / ".tools/pg/usr/lib/aarch64-linux-gnu"  # PG_LIB: Linux copy only
PG_LOCALE = "C" if sys.platform == "darwin" else "C.UTF-8"  # macOS has no C.UTF-8 locale
PORT = int(os.environ.get("SEARCH_PG_PORT", "20432"))
DSN = f"host=127.0.0.1 port={PORT} dbname=search user=search connect_timeout=3"
EMBED_DIM = 768

SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS users (user_id text PRIMARY KEY);
CREATE TABLE IF NOT EXISTS groups (group_id text PRIMARY KEY);
CREATE TABLE IF NOT EXISTS memberships (
    user_id text REFERENCES users ON DELETE CASCADE, group_id text REFERENCES groups ON DELETE CASCADE,
    PRIMARY KEY (user_id, group_id));
CREATE TABLE IF NOT EXISTS documents (
    doc_id text PRIMARY KEY, title text NOT NULL, kind text NOT NULL, version int NOT NULL DEFAULT 1,
    archived boolean NOT NULL DEFAULT false, deleted boolean NOT NULL DEFAULT false,
    source text NOT NULL DEFAULT 'text', updated_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TABLE IF NOT EXISTS doc_acl (
    doc_id text REFERENCES documents ON DELETE CASCADE,
    principal_kind text NOT NULL CHECK (principal_kind IN ('group', 'user')), principal_id text NOT NULL,
    PRIMARY KEY (doc_id, principal_kind, principal_id));
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id bigserial PRIMARY KEY, doc_id text REFERENCES documents ON DELETE CASCADE,
    doc_version int NOT NULL, ordinal int NOT NULL, text text NOT NULL,
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    embedding vector({EMBED_DIM}));
CREATE INDEX IF NOT EXISTS chunks_tsv ON chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks (doc_id);
-- bumped by every permission or content change; part of every answer-cache key
CREATE TABLE IF NOT EXISTS epoch (id int PRIMARY KEY CHECK (id = 1), value bigint NOT NULL);
INSERT INTO epoch VALUES (1, 0) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS answer_cache (
    user_id text NOT NULL, query_sha text NOT NULL, epoch bigint NOT NULL, response jsonb NOT NULL,
    doc_ids text[] NOT NULL, PRIMARY KEY (user_id, query_sha));
-- metadata only: no query text, no content
CREATE TABLE IF NOT EXISTS query_log (
    id bigserial PRIMARY KEY, at timestamptz NOT NULL DEFAULT clock_timestamp(), user_id text NOT NULL,
    endpoint text NOT NULL, query_sha text NOT NULL, doc_ids text[] NOT NULL, latency_ms real NOT NULL);

CREATE OR REPLACE FUNCTION readable_docs(p_user text) RETURNS TABLE (doc_id text) LANGUAGE sql STABLE AS $$
    SELECT d.doc_id FROM documents d
    WHERE NOT d.deleted AND EXISTS (
        SELECT 1 FROM doc_acl a
        WHERE a.doc_id = d.doc_id AND (
            (a.principal_kind = 'user' AND a.principal_id = p_user) OR
            (a.principal_kind = 'group' AND a.principal_id IN
                (SELECT m.group_id FROM memberships m WHERE m.user_id = p_user))))
$$;
"""


def _pg_prefix() -> list[str]:
    if os.geteuid() != 0:
        return []
    pg_dir = RUN / "pg"
    groups = {p.stat().st_gid for p in [pg_dir, *pg_dir.parents] if p.exists() and not p.stat().st_mode & stat.S_IXOTH}
    return ["setpriv", "--reuid=65534", "--regid=65534", f"--groups={','.join(map(str, sorted(groups))) or '65534'}"]


def _pg(tool: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([*_pg_prefix(), str(PG_BIN / tool), *args], env={**os.environ, "LD_LIBRARY_PATH": str(PG_LIB)},
                          capture_output=True, text=True, check=check)


def up() -> None:
    pg_dir, data = RUN / "pg", RUN / "pg/data"
    if not data.exists():
        pg_dir.mkdir(parents=True, exist_ok=True)
        if os.geteuid() == 0:
            os.chown(pg_dir, 65534, 65534)
        _pg("initdb", "-D", str(data), "-U", "postgres", "--auth=trust", "-E", "UTF8", f"--locale={PG_LOCALE}")
    if _pg("pg_isready", "-h", "127.0.0.1", "-p", str(PORT), check=False).returncode != 0:
        _pg("pg_ctl", "-D", str(data), "-l", str(pg_dir / "postgres.log"), "-w", "start",
            "-o", f"-p {PORT} -k {pg_dir} -c listen_addresses=127.0.0.1")
    with psycopg.connect(f"host=127.0.0.1 port={PORT} dbname=postgres user=postgres", autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='search'").fetchone():
            conn.execute("CREATE ROLE search LOGIN")
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname='search'").fetchone():
            conn.execute("CREATE DATABASE search OWNER search")
    # pgvector's CREATE EXTENSION needs a superuser on first install
    with psycopg.connect(f"host=127.0.0.1 port={PORT} dbname=search user=postgres", autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    with connect() as conn:
        conn.execute(SCHEMA)


def down() -> None:
    if (RUN / "pg/data").exists():
        _pg("pg_ctl", "-D", str(RUN / "pg/data"), "-m", "fast", "-w", "stop", check=False)


def connect() -> psycopg.Connection:
    return psycopg.connect(DSN, autocommit=True)


def bump_epoch(conn) -> int:
    """Advance the epoch and purge cached answers from earlier epochs in the same transaction. They
    could never be served again (the cache is read only at the current epoch), but left in place they
    kept answer text at rest for users who may have lost access."""
    epoch = conn.execute("UPDATE epoch SET value = value + 1 WHERE id = 1 RETURNING value").fetchone()[0]
    conn.execute("DELETE FROM answer_cache WHERE epoch < %s", (epoch,))
    return epoch


if __name__ == "__main__":
    {"up": up, "down": down}[sys.argv[1]]()
    print("ok")
