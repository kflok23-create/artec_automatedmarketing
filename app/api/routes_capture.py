"""§10 CAPTURE — small FastAPI endpoints, straight into Postgres.

/webhooks/stripe   MONEY ONLY → orders (client_reference_id join; signature verified)
/webhooks/billplz  MONEY ONLY → orders (X-Signature verified; bill fetched for reference_1)
/event             BEHAVIOUR ONLY → events (public, CORS-restricted, rate limited, deduped)
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict, deque
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request

from app.db import session_scope
from app.integrations import billplz_webhook, stripe_webhook
from app.integrations.billplz_webhook import pending_order_key
from app.models import Event
from app.schemas import EventBeacon
from app.settings import get_settings

router = APIRouter()

# In-memory fixed window: 120 requests / 60 s per client IP (single service instance).
_WINDOW_S, _MAX_REQ = 60, 120
_hits: dict[str, deque] = defaultdict(deque)


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    q = _hits[ip]
    while q and now - q[0] > _WINDOW_S:
        q.popleft()
    if len(q) >= _MAX_REQ:
        return True
    q.append(now)
    return False


@router.post("/webhooks/stripe")
async def stripe_hook(request: Request):
    payload = await request.body()
    sig = request.headers.get("Stripe-Signature", "")
    try:
        stripe_webhook.verify_signature(payload, sig, get_settings().STRIPE_WEBHOOK_SECRET)
    except stripe_webhook.StripeSignatureError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON") from None
    with session_scope() as session:
        disposition = stripe_webhook.handle_event(session, event)
    return {"ok": True, "disposition": disposition}


@router.post("/webhooks/billplz")
async def billplz_hook(request: Request):
    """Forwarded verbatim from artec.my/billplz-callback.php as raw form-encoded POST.
    Must 200 inside 20 s (Billplz drops after 5 failed retries): no external HTTP happens
    here — signature HMAC + two indexed lookups, synchronous by design."""
    form = dict((await request.form()).items())
    try:
        billplz_webhook.verify_x_signature(form, get_settings().BILLPLZ_XSIGNATURE_KEY)
    except billplz_webhook.BillplzSignatureError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    with session_scope() as session:
        disposition = billplz_webhook.handle_callback(session, form)
    return {"ok": True, "disposition": disposition}


def event_dedupe_key(session_id: str, event_type: str, url: str, occurred_at: datetime) -> str:
    truncated = occurred_at.replace(microsecond=0).isoformat()
    return hashlib.sha256(f"{session_id}|{event_type}|{url}|{truncated}".encode()).hexdigest()


def _resolve_post_id(beacon: EventBeacon) -> str | None:
    for candidate in (beacon.post_id, beacon.utm_campaign):
        if candidate and candidate.startswith("post_"):
            return candidate
    return None


def ingest_event(session, beacon: EventBeacon) -> dict:
    """Store one beacon. BEHAVIOUR ONLY — even `order_created` is intent, not revenue;
    the orders row is written exclusively by the paid webhook.

    order_created (server-side, from checkout.php at bill creation) is keyed on bill_id so
    the Billplz paid callback can join against it; browser events dedupe on
    (session_id, type, url, second)."""
    from sqlalchemy import select

    occurred = beacon.occurred_at or beacon.ts or datetime.now(UTC)
    if beacon.event_type == "order_created":
        key = pending_order_key(beacon.bill_id, beacon.gateway or "billplz")
        payload = {
            "bill_id": beacon.bill_id,
            "value": beacon.value,
            "currency": beacon.currency,
            "pack": beacon.pack,
            "market": beacon.market,
            "gateway": beacon.gateway or "billplz",
        }
    else:
        key = event_dedupe_key(beacon.session_id, beacon.event_type, beacon.url, occurred)
        payload = None

    exists = session.execute(select(Event.event_id).where(Event.dedupe_key == key)).first()
    if exists:
        return {"ok": True, "deduped": True}
    utm = {k: v for k, v in (("utm_source", beacon.utm_source),
                             ("utm_medium", beacon.utm_medium),
                             ("utm_campaign", beacon.utm_campaign),
                             ("utm_content", beacon.utm_content)) if v}
    session.add(Event(
        dedupe_key=key,
        post_id=_resolve_post_id(beacon),
        session_id=beacon.session_id,
        event_type=beacon.event_type,
        url=beacon.url,
        code=beacon.code or None,
        utm=utm or None,
        occurred_at=occurred,
        payload=payload,
    ))
    session.flush()
    return {"ok": True, "deduped": False}


@router.post("/event")
async def event_beacon(request: Request, beacon: EventBeacon):
    ip = request.client.host if request.client else "unknown"
    if _rate_limited(ip):
        raise HTTPException(status_code=429, detail="rate limited")
    with session_scope() as session:
        return ingest_event(session, beacon)
