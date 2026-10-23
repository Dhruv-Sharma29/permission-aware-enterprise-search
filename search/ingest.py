"""Ingestion: principals, documents, ACLs, OCR for scanned pages, chunking, embeddings.

    python -m search.ingest .run/corpus

Also the write path for freshness and revocation (used by the admin CLI and the tests):
update_document (new version replaces old chunks atomically), delete_document, grant/revoke,
add/remove membership. Every change bumps the epoch, which invalidates cached answers.
"""

from __future__ import annotations

import argparse
import json
import re
from functools import lru_cache
from pathlib import Path

from search.db import bump_epoch, connect, up
from search.embed import embed, to_pgvector


@lru_cache(maxsize=1)
def _ocr():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def ocr_image(path: Path) -> str:
    """OCR a scanned page into paragraphs (blank-line separated, like the text documents)."""
    result, _ = _ocr()(str(path))
    lines = sorted(result or [], key=lambda r: (r[0][0][1], r[0][0][0]))  # top-to-bottom, left-to-right
    paragraphs, current, last_y = [], [], None
    for box, text, _conf in lines:
        y = box[0][1]
        if last_y is not None and y - last_y > 45 and current:  # vertical gap = new paragraph
            paragraphs.append(" ".join(current))
            current = []
        current.append(text)
        last_y = y
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs[1:]) if len(paragraphs) > 1 else "\n\n".join(paragraphs)  # drop title line


def chunk(title: str, body: str) -> list[str]:
    """One chunk per paragraph, prefixed with the document title for context."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    return [f"{title}. {p}" for p in paras]


def _write_chunks(conn, doc_id: str, version: int, title: str, body: str) -> int:
    texts = chunk(title, body)
    vectors = embed(texts)
    conn.execute("DELETE FROM chunks WHERE doc_id = %s", (doc_id,))
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO chunks (doc_id, doc_version, ordinal, text, embedding) VALUES (%s, %s, %s, %s, %s::vector)",
                        [(doc_id, version, i, t, to_pgvector(v)) for i, (t, v) in enumerate(zip(texts, vectors))])
    return len(texts)


def ingest(corpus: Path) -> dict:
    up()
    principals = json.loads((corpus / "principals.json").read_text())
    docs = json.loads((corpus / "documents.json").read_text())
    stats = {"documents": 0, "chunks": 0, "ocr_documents": 0}
    with connect() as conn:
        conn.execute("TRUNCATE users, groups, memberships, documents, doc_acl, chunks, answer_cache, query_log CASCADE")
        for g in principals["groups"]:
            conn.execute("INSERT INTO groups VALUES (%s)", (g,))
        for u, groups in principals["users"].items():
            conn.execute("INSERT INTO users VALUES (%s)", (u,))
            for g in groups:
                conn.execute("INSERT INTO memberships VALUES (%s, %s)", (u, g))
        for d in docs:
            body, source = d["body"], "text"
            if d.get("scan"):
                body, source = ocr_image(corpus / d["scan"]), "ocr"
                stats["ocr_documents"] += 1
            conn.execute("INSERT INTO documents (doc_id, title, kind, archived, source) VALUES (%s, %s, %s, %s, %s)",
                         (d["doc_id"], d["title"], d["kind"], d["archived"], source))
            for g in d["groups"]:
                conn.execute("INSERT INTO doc_acl VALUES (%s, 'group', %s)", (d["doc_id"], g))
            for u in d["users"]:
                conn.execute("INSERT INTO doc_acl VALUES (%s, 'user', %s)", (d["doc_id"], u))
            stats["chunks"] += _write_chunks(conn, d["doc_id"], 1, d["title"], body)
            stats["documents"] += 1
        bump_epoch(conn)
    return stats


# --- change operations (freshness and revocation) ---------------------------------------------------

def update_document(doc_id: str, body: str) -> int:
    """New version: chunks replaced in one transaction, so readers see old or new, never a mix."""
    with connect() as conn, conn.transaction():
        version, title = conn.execute("UPDATE documents SET version = version + 1, updated_at = clock_timestamp() "
                                      "WHERE doc_id = %s RETURNING version, title", (doc_id,)).fetchone()
        _write_chunks(conn, doc_id, version, title, body)
        bump_epoch(conn)
    return version


def delete_document(doc_id: str) -> None:
    with connect() as conn, conn.transaction():
        conn.execute("UPDATE documents SET deleted = true, updated_at = clock_timestamp() WHERE doc_id = %s", (doc_id,))
        conn.execute("DELETE FROM chunks WHERE doc_id = %s", (doc_id,))
        bump_epoch(conn)


def set_acl(doc_id: str, kind: str, principal: str, grant: bool) -> None:
    with connect() as conn, conn.transaction():
        if grant:
            conn.execute("INSERT INTO doc_acl VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (doc_id, kind, principal))
        else:
            conn.execute("DELETE FROM doc_acl WHERE doc_id = %s AND principal_kind = %s AND principal_id = %s",
                         (doc_id, kind, principal))
        bump_epoch(conn)


def set_membership(user: str, group: str, member: bool) -> None:
    with connect() as conn, conn.transaction():
        if member:
            conn.execute("INSERT INTO memberships VALUES (%s, %s) ON CONFLICT DO NOTHING", (user, group))
        else:
            conn.execute("DELETE FROM memberships WHERE user_id = %s AND group_id = %s", (user, group))
        bump_epoch(conn)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("corpus", type=Path)
    print(ingest(p.parse_args().corpus))


if __name__ == "__main__":
    main()
