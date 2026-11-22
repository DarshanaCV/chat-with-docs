"""Anonymous browser isolation for the public demo."""

import hashlib
import os
import re
import secrets

from fastapi import HTTPException, Request

DEMO_MODE = os.getenv("APP_MODE") == "demo"
COOKIE_NAME = "__Host-docs-visitor"
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
SESSION_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}")


def visitor_namespace(token: str | None) -> str | None:
    if token is None or not TOKEN_PATTERN.fullmatch(token):
        return None
    return hashlib.sha256(token.encode("ascii")).hexdigest() + "-"


def visitor_prefix(request: Request) -> str:
    if not DEMO_MODE:
        return ""
    prefix = visitor_namespace(request.cookies.get(COOKIE_NAME))
    if prefix is None:
        raise HTTPException(status_code=401, detail="Start a visitor session first")
    return prefix


def scoped_session_id(prefix: str, session_id: str) -> str:
    if prefix and not SESSION_PATTERN.fullmatch(session_id):
        raise HTTPException(status_code=400, detail="Invalid conversation ID")
    return prefix + session_id


def new_visitor_token() -> str:
    return secrets.token_urlsafe(32)
