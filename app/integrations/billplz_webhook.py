"""Billplz webhook — MONEY ONLY → orders (§10).

X-Signature (verified against Billplz API docs): HMAC-SHA256 over the callback's key+value
pairs (excluding x_signature), sorted case-insensitively by key, each pair concatenated as
`key` + `value`, pairs joined with '|', keyed with BILLPLZ_XSIGNATURE_KEY.

Attribution: the callback body does NOT carry reference_1 — it lives on the Bill object.
After verification we fetch GET /api/v3/bills/{id} and read reference_1 as the post_id
(artec.my sets it, mirroring Stripe's client_reference_id). Missing / non-post_ values →
post_id NULL (UNATTRIBUTED); never guessed. See DECISIONS.md #3.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Order
from app.settings import Settings

BILLS_BASE = "https://www.billplz.com/api/v3"


class BillplzSignatureError(RuntimeError):
    pass


def verify_x_signature(form: dict[str, str], xsignature_key: str) -> None:
    provided = form.get("x_signature", "")
    if not provided:
        raise BillplzSignatureError("missing x_signature")
    keys = sorted((k for k in form if k != "x_signature"), key=str.lower)
    source = "|".join(f"{k}{form[k]}" for k in keys)
    expected = hmac.new(xsignature_key.encode(), source.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided):
        raise BillplzSignatureError("x_signature verification failed")


def fetch_bill_reference(settings: Settings, bill_id: str) -> str | None:
    try:
        with httpx.Client(timeout=30, auth=(settings.BILLPLZ_API_KEY, "")) as client:
            resp = client.get(f"{BILLS_BASE}/bills/{bill_id}")
        if resp.status_code >= 400:
            return None
        return resp.json().get("reference_1")
    except httpx.HTTPError:
        return None


def handle_callback(session: Session, settings: Settings, form: dict[str, str]) -> str:
    """Process one verified Billplz callback. Returns a short disposition string."""
    bill_id = form.get("id")
    if not bill_id:
        return "ignored"
    if str(form.get("paid", "")).lower() != "true":
        return "unpaid"

    existing = session.execute(
        select(Order).where(Order.source == "billplz", Order.external_id == bill_id)
    ).scalar_one_or_none()
    if existing is not None:
        return "duplicate"

    ref = fetch_bill_reference(settings, bill_id)
    post_id = ref if isinstance(ref, str) and ref.startswith("post_") else None
    paid_amount = form.get("paid_amount") or form.get("amount")
    email = (form.get("email") or "").strip().lower()
    paid_at_raw = form.get("paid_at")
    occurred = None
    if paid_at_raw:
        try:
            occurred = datetime.fromisoformat(paid_at_raw.replace(" +0800", "+08:00"))
        except ValueError:
            occurred = None
    session.add(
        Order(
            source="billplz",
            external_id=bill_id,
            post_id=post_id,
            code=None,
            amount_minor=int(paid_amount) if paid_amount and str(paid_amount).isdigit() else None,
            currency="MYR",
            customer_email_hash=hashlib.sha256(email.encode()).hexdigest() if email else None,
            occurred_at=occurred or datetime.now(UTC),
            raw={"id": bill_id, "collection_id": form.get("collection_id"), "state": form.get("state")},
        )
    )
    session.flush()
    return "attributed" if post_id else "unattributed"
