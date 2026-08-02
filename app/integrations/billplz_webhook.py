"""Billplz webhook — MONEY ONLY → orders (§10).

X-Signature (verified against Billplz API docs): HMAC-SHA256 over the callback's key+value
pairs (excluding x_signature), sorted case-insensitively by key, each pair concatenated as
`key` + `value`, pairs joined with '|', keyed with BILLPLZ_XSIGNATURE_KEY.

Attribution — bill_id join (DECISIONS.md #3): artec.my's live checkout already uses BOTH
Billplz reference slots (reference_1 = discount code, reference_2 = pack), so the bill's
reference fields can never carry a post_id. Instead, checkout.php POSTs an `order_created`
event to /event the moment the bill is created (before payment), keyed on bill_id. The paid
callback joins against that pending row to resolve post_id. No matching pending row — a
direct Billplz link, or the pre-payment POST failed — means UNATTRIBUTED; never guessed.

Latency contract: Billplz drops a callback after 5 failed retries and every failure
degrades the account's webhook rank, so this handler must 200 well inside 20 seconds. It
performs no external HTTP — one signature HMAC and two indexed DB lookups — so it responds
synchronously in milliseconds.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event, Order


class BillplzSignatureError(RuntimeError):
    pass


def pending_order_key(bill_id: str, gateway: str = "billplz") -> str:
    """Dedupe key for the pre-payment `order_created` event — the bill_id join key."""
    return f"order_created|{gateway}|{bill_id}"


def verify_x_signature(form: dict[str, str], xsignature_key: str) -> None:
    provided = form.get("x_signature", "")
    if not provided:
        raise BillplzSignatureError("missing x_signature")
    keys = sorted((k for k in form if k != "x_signature"), key=str.lower)
    source = "|".join(f"{k}{form[k]}" for k in keys)
    expected = hmac.new(xsignature_key.encode(), source.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided):
        raise BillplzSignatureError("x_signature verification failed")


def find_pending_order(session: Session, bill_id: str) -> Event | None:
    return session.execute(
        select(Event).where(Event.dedupe_key == pending_order_key(bill_id))
    ).scalar_one_or_none()


def handle_callback(session: Session, form: dict[str, str]) -> str:
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

    pending = find_pending_order(session, bill_id)
    post_id = pending.post_id if pending is not None and pending.post_id else None
    code = pending.code if pending is not None else None

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
            code=code,
            amount_minor=int(paid_amount) if paid_amount and str(paid_amount).isdigit() else None,
            currency="MYR",
            customer_email_hash=hashlib.sha256(email.encode()).hexdigest() if email else None,
            occurred_at=occurred or datetime.now(UTC),
            raw={
                "id": bill_id,
                "collection_id": form.get("collection_id"),
                "state": form.get("state"),
                "pending_match": pending is not None,
            },
        )
    )
    session.flush()
    return "attributed" if post_id else "unattributed"
