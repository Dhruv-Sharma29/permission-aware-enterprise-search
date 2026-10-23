"""Integration tests use their own Postgres (port 20433) and run directory, ingested once per
session from a freshly generated corpus, so they can revoke and delete freely."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("SEARCH_RUN_DIR", str(ROOT / ".run-test"))
os.environ.setdefault("SEARCH_PG_PORT", "20433")
