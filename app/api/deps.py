"""Auth dependency — non-webhook, non-public routes require the static bearer token.
Webhook routes verify provider signatures instead and reject anything unsigned.

A 401 used to say nothing. When one appeared in production against a token that had been
verified byte-identical in the shell, in `/proc/1/environ` and in a fresh interpreter, there
was no way to tell from the outside whether the header arrived, whether the prefix parsed,
or whether the RUNNING process held a different value from the one on disk — so the next
step was guesswork. It now logs enough to answer that in one request, and nothing that
could leak the token:

  * lengths, never values
  * sha256[:8] of each side — 8 hex characters of a digest is not reversible, and it is the
    same comparison the operator made by hand across layers
  * server-side log only. The RESPONSE stays generic: an unauthenticated caller learns
    nothing about why it failed.
"""

from __future__ import annotations

import hashlib
import logging
import secrets

from fastapi import Header, HTTPException

from app.settings import get_settings

log = logging.getLogger("artec.auth")


def _fingerprint(value: str) -> str:
    """8 hex chars of sha256 — an identity check that cannot be turned back into a token."""
    if not value:
        return "<empty>"
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:8]


def require_token(authorization: str = Header(default="")) -> None:
    expected = get_settings().HERMES_API_TOKEN
    had_header = bool(authorization)
    had_prefix = authorization.startswith("Bearer ")
    provided = authorization.removeprefix("Bearer ").strip()

    ok = False
    reason = ""
    if not provided:
        reason = "no header" if not had_header else "header present but empty after 'Bearer '"
    else:
        try:
            ok = secrets.compare_digest(provided, expected)
        except TypeError:
            # compare_digest requires ASCII str; a mangled or wide-character header lands here.
            reason = "provided token is not ASCII — check for smart quotes or copy damage"
    if ok:
        return

    if not reason:
        reason = ("value mismatch — the running process holds a different token from the "
                  "one presented")
    log.warning(
        "401 on an authenticated route: %s | header=%s prefix=%s provided_len=%d "
        "expected_len=%d provided_sha8=%s expected_sha8=%s",
        reason, had_header, had_prefix, len(provided), len(expected),
        _fingerprint(provided), _fingerprint(expected),
    )
    raise HTTPException(status_code=401, detail="invalid or missing bearer token")
