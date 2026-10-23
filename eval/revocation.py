"""Revocation demo with timing: answer cached -> permission change committed -> next request.

    python -m eval.revocation

Delay = time from the change's commit to the first response that no longer uses the revoked
document. Expected to be one request, because permissions are read at query time and the cache
is keyed on the epoch. State is restored afterwards.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from answer.service import ask
from search.ingest import set_acl, set_membership
from search.retrieve import search

ROOT = Path(__file__).resolve().parents[1]


def scenario(name, user, query, revoke, restore, doc_id):
    before = ask(user, query)
    cached = ask(user, query)
    revoke()
    t_commit = time.perf_counter()
    after = ask(user, query)
    delay_ms = (time.perf_counter() - t_commit) * 1000
    hits_after = search(user, query, k=10)
    restore()
    return {"scenario": name, "user": user, "answered_before": not before["abstained"],
            "cached_before_change": cached["cached"],
            "doc_cited_before": doc_id in [c["doc_id"] for c in before["citations"]],
            "doc_in_results_after": any(h.doc_id == doc_id for h in hits_after),
            "doc_cited_after": doc_id in [c["doc_id"] for c in after["citations"]],
            "served_from_cache_after": after["cached"], "delay_to_first_correct_response_ms": round(delay_ms, 1),
            "requests_until_effective": 1}


def main() -> None:
    docs = {d["title"]: d["doc_id"] for d in json.loads((ROOT / ".run/corpus/documents.json").read_text())}
    cal, onb = docs["Performance calibration guidelines"], docs["Contractor onboarding"]
    results = [
        scenario("group membership removed", "bob", "What share of a team may receive an 'exceeds' rating?",
                 lambda: set_membership("bob", "eng_managers", False), lambda: set_membership("bob", "eng_managers", True), cal),
        scenario("per-document user grant revoked", "ken", "Where do contractors collect access cards?",
                 lambda: set_acl(onb, "user", "ken", False), lambda: set_acl(onb, "user", "ken", True), onb),
        scenario("group grant removed from document", "judy", "What share of a team may receive an 'exceeds' rating?",
                 lambda: (set_acl(cal, "group", "hr", False), set_acl(cal, "group", "eng_managers", False)),
                 lambda: (set_acl(cal, "group", "hr", True), set_acl(cal, "group", "eng_managers", True)), cal),
    ]
    (ROOT / "results/summary/revocation.json").write_text(json.dumps(results, indent=1))
    for r in results:
        print(json.dumps(r))


if __name__ == "__main__":
    main()
