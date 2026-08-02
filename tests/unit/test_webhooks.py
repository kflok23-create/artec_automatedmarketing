"""Acceptance 16 (stripe idempotent), 17 (event dedupe), signature verification both ways."""

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.api.routes_capture import event_dedupe_key
from app.integrations import billplz_webhook, stripe_webhook
from app.models import Event, Order


def _stripe_event(session_id="cs_test_1", ref="post_1482"):
    return {
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": session_id,
            "client_reference_id": ref,
            "amount_total": 13900,
            "currency": "sgd",
            "created": 1750000000,
            "customer_details": {"email": "buyer@example.com"},
        }},
    }


def test_stripe_signature_roundtrip():
    secret = "whsec_test_secret_000000000"
    payload = json.dumps(_stripe_event()).encode()
    t = int(time.time())
    sig = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    stripe_webhook.verify_signature(payload, f"t={t},v1={sig}", secret)  # no raise
    with pytest.raises(stripe_webhook.StripeSignatureError):
        stripe_webhook.verify_signature(payload, f"t={t},v1={'0' * 64}", secret)
    with pytest.raises(stripe_webhook.StripeSignatureError):
        stripe_webhook.verify_signature(payload, f"t={t - 9999},v1={sig}", secret)


def test_same_stripe_webhook_twice_one_order_row(session):
    event = _stripe_event()
    assert stripe_webhook.handle_event(session, event) == "attributed"
    assert stripe_webhook.handle_event(session, event) == "duplicate"
    n = session.execute(select(func.count()).select_from(Order)).scalar()
    assert n == 1                                     # 16
    order = session.execute(select(Order)).scalar_one()
    assert order.post_id == "post_1482"
    assert order.currency == "SGD"
    assert order.customer_email_hash != "buyer@example.com"  # hashed, never raw


def test_stripe_without_reference_is_unattributed_never_guessed(session):
    assert stripe_webhook.handle_event(session, _stripe_event("cs_2", ref=None)) == "unattributed"
    assert session.execute(select(Order)).scalar_one().post_id is None


def test_billplz_signature_roundtrip():
    key = "xsig-key"
    form = {"id": "bill1", "paid": "true", "amount": "44900", "collection_id": "c1"}
    src = "|".join(f"{k}{form[k]}" for k in sorted(form, key=str.lower))
    form["x_signature"] = hmac.new(key.encode(), src.encode(), hashlib.sha256).hexdigest()
    billplz_webhook.verify_x_signature(form, key)  # no raise
    form["amount"] = "1"
    with pytest.raises(billplz_webhook.BillplzSignatureError):
        billplz_webhook.verify_x_signature(form, key)


def test_billplz_callback_idempotent_and_fetches_reference(session, monkeypatch):
    monkeypatch.setattr(billplz_webhook, "fetch_bill_reference", lambda s, b: "post_1483")
    from app.settings import get_settings

    form = {"id": "bill9", "paid": "true", "paid_amount": "44900", "email": "x@y.my",
            "paid_at": "2026-08-01 10:00:00 +0800"}
    assert billplz_webhook.handle_callback(session, get_settings(), form) == "attributed"
    assert billplz_webhook.handle_callback(session, get_settings(), form) == "duplicate"
    order = session.execute(select(Order)).scalar_one()
    assert order.post_id == "post_1483" and order.currency == "MYR"


def test_event_beacon_dedupe_key_truncates_to_second(session):
    t1 = datetime(2026, 8, 1, 10, 0, 0, 123456, tzinfo=UTC)
    t2 = datetime(2026, 8, 1, 10, 0, 0, 999999, tzinfo=UTC)
    k1 = event_dedupe_key("s1", "page_view", "https://artec.my/", t1)
    k2 = event_dedupe_key("s1", "page_view", "https://artec.my/", t2)
    assert k1 == k2                                    # 17: same second → same key

    session.add(Event(dedupe_key=k1, session_id="s1", event_type="page_view",
                      url="https://artec.my/", occurred_at=t1))
    session.flush()
    exists = session.execute(select(Event).where(Event.dedupe_key == k2)).first()
    assert exists is not None                          # second beacon would be dropped
    assert session.execute(select(func.count()).select_from(Event)).scalar() == 1
