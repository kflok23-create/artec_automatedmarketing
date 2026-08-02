"""Auth dependency — non-webhook, non-public routes require the static bearer token.
Webhook routes verify provider signatures instead and reject anything unsigned."""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException

from app.settings import get_settings


def require_token(authorization: str = Header(default="")) -> None:
    expected = get_settings().HERMES_API_TOKEN
    provided = authorization.removeprefix("Bearer ").strip()
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")
