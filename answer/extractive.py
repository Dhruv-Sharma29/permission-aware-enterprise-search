"""Extractive answering baseline (no language model): quote the passage sentence or table row that
best covers the question's terms, cite it, or abstain.

Same contract a model-backed generator will have: input = only the authorized passages retrieved
for this user; output = answer text, citations (doc + chunk), abstained flag. Swapping in an LLM
(project 01's endpoint) changes how the answer is worded, not what it can see.
"""

from __future__ import annotations

import re

STOP = set("""a an and are as at be by can could did do does for from has have how i in is it its many may much of
on or should the their there this to under was were what when where which who whom why will with within
current currently long""".split())  # "long" as in "how long": question phrasing, not content (found on dev)


# Text that addresses the system rather than stating facts. Never quoted (counted instead): the
# real protection is that unauthorized text never reaches this function, but an answer shouldn't
# echo an attacker's instructions either.
INJECTION = re.compile(r"ignore (all )?(previous|prior) instructions|you are now|admin mode|system prompt|reveal (the|every|all)", re.I)


def stem(w: str) -> str:
    for suffix in ("ing", "ers", "ed", "er", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 4:
            return w[: -len(suffix)]
    return w


def terms(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9$][a-z0-9$,.\-']*[a-z0-9]|[a-z0-9]", text.lower())
    return {stem(w) for w in words if w not in STOP}


def units(chunk_text: str) -> list[tuple[str, str]]:
    """(text to match, text to quote) for each sentence and table row. Table rows are matched
    together with the table's header row but quoted alone; header and separator rows are not quotable."""
    out, header = [], None
    for line in chunk_text.split("\n"):
        row = line.strip()
        if row.startswith("|"):
            if re.fullmatch(r"\|[\s\-|]+\|?", row):
                continue
            if header is None:
                header = row
                continue
            out.append((f"{header} {row}", row))
        else:
            header = None
            out += [(s.strip(), s.strip()) for s in re.split(r"(?<=[.!?])\s+(?=[A-Z|])", line) if s.strip()]
    return out


def answer(question: str, hits: list, threshold: float = 0.5, top: int = 5) -> dict:
    q = terms(question)
    best, flagged = None, 0
    for rank, h in enumerate(hits[:top]):
        title_terms = terms(h.title)
        for match_text, quote in units(h.text):
            if quote.rstrip(".") == h.title.rstrip("."):
                continue
            if INJECTION.search(quote):
                flagged += 1
                continue
            covered = len(q & (terms(match_text) | title_terms)) / max(1, len(q))
            score = covered + 0.02 * (top - rank)
            if best is None or score > best[0]:
                best = (score, covered, quote, h)
    if best is None or best[1] < threshold:
        return {"abstained": True, "answer": None, "citations": [], "injection_flagged": flagged,
                "reason": "no authorized evidence covers the question well enough"}
    _, covered, text, h = best
    return {"abstained": False, "answer": text, "coverage": round(covered, 2), "injection_flagged": flagged,
            "citations": [{"doc_id": h.doc_id, "chunk_id": h.chunk_id, "title": h.title}]}
