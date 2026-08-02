"""Stripe webhook — MONEY ONLY → orders (§10).

The join key is client_reference_id, appended by artec.my to the hosted Payment Link.
This module deliberately contains no reference to campaign query parameters anywhere:
they never reach the Checkout Session object on Payment Links, so reading them here would
be a bug. An order with no client_reference_id is stored with post_id NULL (UNATTRIBUTED)
— never guessed from timing, amount, or recency.

Signature verification is hand-rolled (Stripe scheme v1): HMAC-SHA256 over
"{t}.{raw_payload}" with the endpoint's own signing secret. This service's endpoint is a
SECOND Stripe endpoint with its own whsec_ — never the one on artec.my/stripe-webhook.php.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Order


class StripeSignatureError(RuntimeError):
    pass


def verify_signature(payload: bytes, sig_header: str, secret: str, tolerance_s: int = 300) -> None:
    """Raise StripeSignatureError unless the v1 signature is valid and fresh."""
    if not sig_header:
        raise StripeSignatureError("missing Stripe-Signature header")
    parts: dict[str, list[str]] = {}
    for item in sig_header.split(","):
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        parts.setdefault(k.strip(), []).append(v.strip())
    try:
        timestamp = int(parts["t"][0])
    except (KeyError, ValueError):
        raise StripeSignatureError("malformed Stripe-Signature header") from None
    if abs(time.time() - timestamp) > tolerance_s:
        raise StripeSignatureError("Stripe-Signature timestamp outside tolerance")
    signed = f"{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    for candidate in parts.get("v1", []):
        if hmac.compare_digest(expected, candidate):
            return
    raise StripeSignatureError("Stripe-Signature verification failed")


def handle_event(session: Session, event: dict) -> str:
    """Process one verified Stripe event. Returns a short disposition string."""
    if event.get("type") != "checkout.session.completed":
        return "ignored"
    obj = (event.get("data") or {}).get("object") or {}
    external_id = obj.get("id")
    if not external_id:
        return "ignored"

    existing = session.execute(
        select(Order).where(Order.source == "stripe", Order.external_id == external_id)
    ).scalar_one_or_none()
    if existing is not None:
        return "duplicate"  # idempotent: same webhook delivered twice → one row

    ref = obj.get("client_reference_id")
    post_id = ref if isinstance(ref, str) and ref.startswith("post_") else None
    email = ((obj.get("customer_details") or {}).get("email") or "").strip().lower()
    created = obj.get("created")
    session.add(
        Order(
            source="stripe",
            external_id=external_id,
            post_id=post_id,
            code=(obj.get("discounts") or [{}])[0].get("promotion_code") if obj.get("discounts") else None,
            amount_minor=obj.get("amount_total"),
            currency=(obj.get("currency") or "").upper() or None,
            customer_email_hash=hashlib.sha256(email.encode()).hexdigest() if email else None,
            occurred_at=datetime.fromtimestamp(created, tz=UTC) if created else datetime.now(UTC),
            raw={"id": external_id, "client_reference_id": ref},
        )
    )
    session.flush()
    return "attributed" if post_id else "unattributed"
