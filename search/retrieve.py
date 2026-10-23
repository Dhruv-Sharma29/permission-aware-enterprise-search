"""Authorized retrieval. The only entry points take an authenticated user id; every SQL query
joins chunks to readable_docs(user) and to the document's current version.

  keyword  Postgres full-text search, OR over the question's terms, ranked by ts_rank_cd
  vector   pgvector cosine distance over BGE embeddings (exact scan of the authorized set:
           no approximate index whose filtering could silently drop results)
  hybrid   reciprocal-rank fusion of both (k = 60); archived documents downweighted
  vector+rerank, hybrid+rerank   the base mode's top 20, re-ordered by a cross-encoder (search/rerank.py)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from search.db import connect
from search.embed import embed_query, to_pgvector

RRF_K = 60
ARCHIVED_WEIGHT = 0.5
CANDIDATES = 50

AUTHORIZED = """
    FROM chunks c
    JOIN readable_docs(%(user)s) r ON r.doc_id = c.doc_id
    JOIN documents d ON d.doc_id = c.doc_id AND d.version = c.doc_version  -- stale chunks never match
"""


@dataclass
class Hit:
    chunk_id: int
    doc_id: str
    title: str
    text: str
    archived: bool
    score: float


def _terms(query: str) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 1]
    return " | ".join(dict.fromkeys(words)) or "zzzznomatch"


def keyword(user: str, query: str, k: int = CANDIDATES) -> list[Hit]:
    sql = f"""SELECT c.chunk_id, c.doc_id, d.title, c.text, d.archived, ts_rank_cd(c.tsv, q) AS score
              {AUTHORIZED}, to_tsquery('english', %(terms)s) q
              WHERE c.tsv @@ q ORDER BY score DESC, c.chunk_id LIMIT %(k)s"""
    with connect() as conn:
        rows = conn.execute(sql, {"user": user, "terms": _terms(query), "k": k}).fetchall()
    return [Hit(*r) for r in rows]


def vector(user: str, query: str, k: int = CANDIDATES, qvec=None) -> list[Hit]:
    v = to_pgvector(qvec if qvec is not None else embed_query(query))
    sql = f"""SELECT c.chunk_id, c.doc_id, d.title, c.text, d.archived, 1 - (c.embedding <=> %(v)s::vector) AS score
              {AUTHORIZED} ORDER BY c.embedding <=> %(v)s::vector, c.chunk_id LIMIT %(k)s"""
    with connect() as conn:
        rows = conn.execute(sql, {"user": user, "v": v, "k": k}).fetchall()
    return [Hit(*r) for r in rows]


def hybrid(user: str, query: str, k: int = 10, archived_weight: float = ARCHIVED_WEIGHT, qvec=None) -> list[Hit]:
    fused: dict[int, tuple[float, Hit]] = {}
    for hits in (keyword(user, query), vector(user, query, qvec=qvec)):
        for rank, h in enumerate(hits):
            prev = fused.get(h.chunk_id, (0.0, h))[0]
            fused[h.chunk_id] = (prev + 1.0 / (RRF_K + rank + 1), h)
    scored = []
    for score, h in fused.values():
        h.score = score * (archived_weight if h.archived else 1.0)
        scored.append(h)
    return sorted(scored, key=lambda h: (-h.score, h.chunk_id))[:k]


def search(user: str, query: str, mode: str = "hybrid", k: int = 10, qvec=None) -> list[Hit]:
    if mode in ("vector+rerank", "hybrid+rerank"):
        from search.rerank import DEPTH, rerank   # loads the cross-encoder only when asked for
        return rerank(query, search(user, query, mode.removesuffix("+rerank"), max(k, DEPTH), qvec=qvec))[:k]
    if mode == "keyword":
        return keyword(user, query, k)
    if mode == "vector":
        return vector(user, query, k, qvec=qvec)
    if mode == "hybrid-no-archive-penalty":
        return hybrid(user, query, k, archived_weight=1.0, qvec=qvec)
    return hybrid(user, query, k, qvec=qvec)


def can_read(user: str, doc_id: str) -> bool:
    with connect() as conn:
        return conn.execute("SELECT 1 FROM readable_docs(%s) WHERE doc_id = %s", (user, doc_id)).fetchone() is not None
