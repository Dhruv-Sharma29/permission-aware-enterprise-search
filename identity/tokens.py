"""Authentication: HS256-signed tokens carrying only the user id (`sub`) and expiry.

Tokens prove *who* is asking, nothing more. Groups and permissions are read from the database on
every request, so a token can't carry stale or forged roles. The signing secret lives in .run/ and
is generated on first use; it is never logged or committed.
"""

from __future__ import annotations

import os
import secrets
import time
from pathlib import Path

import jwt

SECRET_FILE = Path(os.environ.get("SEARCH_RUN_DIR", Path(__file__).resolve().parents[1] / ".run")) / "token_secret"
ALGORITHM = "HS256"


def _secret() -> str:
    if not SECRET_FILE.exists():
        SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        SECRET_FILE.write_text(secrets.token_hex(32))
        SECRET_FILE.chmod(0o600)
    return SECRET_FILE.read_text().strip()


def issue(user_id: str, ttl_s: int = 3600) -> str:
    now = int(time.time())
    return jwt.encode({"sub": user_id, "iat": now, "exp": now + ttl_s}, _secret(), algorithm=ALGORITHM)


class AuthError(Exception):
    pass


def verify(token: str) -> str:
    try:
        claims = jwt.decode(token, _secret(), algorithms=[ALGORITHM], options={"require": ["sub", "exp"]})
    except jwt.PyJWTError as e:
        raise AuthError(type(e).__name__) from None
    return claims["sub"]
