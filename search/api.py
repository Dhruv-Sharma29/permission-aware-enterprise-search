"""HTTP API. Identity comes only from the bearer token; any user/role field in a request body is
ignored. Citation links and previews re-check authorization and answer 404 (not 403) for documents
the caller can't read, so a link doesn't even confirm the document exists. Logs: metadata only.

    uvicorn search.api:app --host 127.0.0.1 --port 20080
"""

from __future__ import annotations

import time

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from answer.service import ask, query_sha
from identity.tokens import AuthError, verify
from search.db import connect
from search.retrieve import can_read, search

app = FastAPI(title="permission-aware-search")


class Query(BaseModel):
    query: str
    mode: str = "hybrid"
    k: int = 10


def caller(authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "missing bearer token")
    try:
        user = verify(token)
    except AuthError:
        raise HTTPException(401, "invalid token") from None
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM users WHERE user_id = %s", (user,)).fetchone():
            raise HTTPException(401, "unknown user")
    return user


def log(user: str, endpoint: str, query: str, doc_ids: list[str], t0: float) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO query_log (user_id, endpoint, query_sha, doc_ids, latency_ms) VALUES (%s, %s, %s, %s, %s)",
                     (user, endpoint, query_sha(query), doc_ids, (time.perf_counter() - t0) * 1000))


@app.post("/search")
def search_endpoint(q: Query, authorization: str | None = Header(default=None)) -> dict:
    t0 = time.perf_counter()
    user = caller(authorization)
    hits = search(user, q.query, q.mode if q.mode in ("keyword", "vector", "hybrid", "vector+rerank", "hybrid+rerank") else "hybrid", min(q.k, 20))
    log(user, "search", q.query, [h.doc_id for h in hits], t0)
    return {"results": [{"doc_id": h.doc_id, "chunk_id": h.chunk_id, "title": h.title, "snippet": h.text[:300],
                         "score": round(h.score, 5)} for h in hits]}


@app.post("/answer")
def answer_endpoint(q: Query, authorization: str | None = Header(default=None)) -> dict:
    t0 = time.perf_counter()
    user = caller(authorization)
    result = ask(user, q.query)
    log(user, "answer", q.query, [c["doc_id"] for c in result["citations"]], t0)
    return result


@app.get("/citations/{chunk_id}")
def citation(chunk_id: int, authorization: str | None = Header(default=None)) -> dict:
    user = caller(authorization)
    with connect() as conn:
        row = conn.execute("SELECT c.doc_id, d.title, c.text FROM chunks c JOIN documents d ON d.doc_id = c.doc_id "
                           "AND d.version = c.doc_version WHERE c.chunk_id = %s", (chunk_id,)).fetchone()
    if not row or not can_read(user, row[0]):
        raise HTTPException(404, "not found")
    return {"doc_id": row[0], "title": row[1], "text": row[2]}


@app.get("/docs/{doc_id}/preview")
def preview(doc_id: str, authorization: str | None = Header(default=None)) -> dict:
    user = caller(authorization)
    if not can_read(user, doc_id):
        raise HTTPException(404, "not found")
    with connect() as conn:
        row = conn.execute("SELECT d.title, c.text FROM documents d JOIN chunks c ON c.doc_id = d.doc_id "
                           "AND c.doc_version = d.version WHERE d.doc_id = %s ORDER BY c.ordinal LIMIT 1", (doc_id,)).fetchone()
    if not row:
        raise HTTPException(404, "not found")
    return {"doc_id": doc_id, "title": row[0], "preview": row[1][:300]}
