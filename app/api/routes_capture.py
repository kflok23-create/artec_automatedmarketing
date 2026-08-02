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
    form = dict((await request.form()).items())
    settings = get_settings()
    try:
        billplz_webhook.verify_x_signature(form, settings.BILLPLZ_XSIGNATURE_KEY)
    except billplz_webhook.BillplzSignatureError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    with session_scope() as session:
        disposition = billplz_webhook.handle_callback(session, settings, form)
    return {"ok": True, "disposition": disposition}


def event_dedupe_key(session_id: str, event_type: str, url: str, occurred_at: datetime) -> str:
    truncated = occurred_at.replace(microsecond=0).isoformat()
    return hashlib.sha256(f"{session_id}|{event_type}|{url}|{truncated}".encode()).hexdigest()


@router.post("/event")
async def event_beacon(request: Request, beacon: EventBeacon):
    ip = request.client.host if request.client else "unknown"
    if _rate_limited(ip):
        raise HTTPException(status_code=429, detail="rate limited")
    occurred = beacon.occurred_at or datetime.now(UTC)
    key = event_dedupe_key(beacon.session_id, beacon.event_type, beacon.url, occurred)
    with session_scope() as session:
        from sqlalchemy import select

        exists = session.execute(select(Event.event_id).where(Event.dedupe_key == key)).first()
        if exists:
            return {"ok": True, "deduped": True}
        utm = {k: v for k, v in (("utm_source", beacon.utm_source),
                                 ("utm_medium", beacon.utm_medium),
                                 ("utm_campaign", beacon.utm_campaign)) if v}
        campaign = beacon.utm_campaign
        session.add(Event(
            dedupe_key=key,
            post_id=campaign if campaign and campaign.startswith("post_") else None,
            session_id=beacon.session_id,
            event_type=beacon.event_type,
            url=beacon.url,
            code=beacon.code,
            utm=utm or None,
            occurred_at=occurred,
            payload=None,
        ))
    return {"ok": True, "deduped": False}
