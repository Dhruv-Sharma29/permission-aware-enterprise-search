"""Independent authorization oracle, computed from the fixture files in plain Python (no SQL).

Evaluation and tests check the system against this, so a bug in readable_docs() can't hide itself.
`overrides` lets revocation tests describe the expected state after a change.
"""

from __future__ import annotations

import json
from pathlib import Path


class Oracle:
    def __init__(self, corpus: Path):
        self.principals = json.loads((corpus / "principals.json").read_text())
        self.docs = {d["doc_id"]: d for d in json.loads((corpus / "documents.json").read_text())}
        truth = json.loads((corpus / "scan_truth.json").read_text())
        self.text = {i: (d["body"] if d["body"] is not None else truth[i]) for i, d in self.docs.items()}
        self.memberships = {u: set(g) for u, g in self.principals["users"].items()}
        self.deleted: set[str] = set()
        self.revoked_grants: set[tuple[str, str, str]] = set()

    def can_read(self, user: str, doc_id: str) -> bool:
        d = self.docs.get(doc_id)
        if d is None or doc_id in self.deleted:
            return False
        if user in d["users"] and (doc_id, "user", user) not in self.revoked_grants:
            return True
        return any(g in self.memberships.get(user, set()) and (doc_id, "group", g) not in self.revoked_grants
                   for g in d["groups"])

    def readable(self, user: str) -> set[str]:
        return {i for i in self.docs if self.can_read(user, i)}

    def forbidden_values(self, user: str) -> set[str]:
        """Fact values that appear only in documents this user can't read (any appearance = leak)."""
        readable = self.readable(user)
        values = {f["value"] for d in self.docs.values() for f in d["facts"]}
        return {v for v in values if not any(v in self.text[i] for i in readable)}
