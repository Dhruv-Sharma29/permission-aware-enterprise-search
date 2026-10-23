#!/usr/bin/env bash
# PostgreSQL 16 + pgvector 0.6.0 from the Ubuntu arm64 archive, unpacked into .tools/pg (user space
# only; `rm -rf .tools` undoes it). Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "$(uname -s)" = "Darwin" ]; then
  # macOS: Homebrew's PostgreSQL + pgvector; search/db.py finds them through pg_config (or set PG_BIN).
  command -v pg_config >/dev/null || { echo "run: brew install postgresql@17 pgvector, then put its bin dir on PATH or set PG_BIN"; exit 1; }
  ls "$(pg_config --sharedir)/extension" | grep -q '^vector\.control$' \
    || { echo "pgvector isn't installed for $(pg_config --version); see README (macOS)"; exit 1; }
  echo "using $(pg_config --version) at $(pg_config --bindir) with pgvector"; exit 0
fi
PG_VERSION=16.15-0ubuntu0.24.04.1
BIN=.tools/pg/usr/lib/postgresql/16
if [ -x "$BIN/bin/postgres" ] && [ -f "$BIN/lib/vector.so" ]; then echo "already unpacked"; exit 0; fi
mkdir -p .tools/debs && cd .tools/debs
apt-get download "postgresql-16=$PG_VERSION" "postgresql-client-16=$PG_VERSION" "libpq5=$PG_VERSION" postgresql-16-pgvector 2>/dev/null
cd ..
for deb in debs/*.deb; do dpkg-deb -x "$deb" pg; done
ls pg/usr/lib/postgresql/16/lib/vector.so && pg/usr/lib/postgresql/16/bin/postgres --version
