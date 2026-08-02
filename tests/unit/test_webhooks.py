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


def _order_created_beacon(bill_id="bill9", post_id="post_1483", code="SOCIAL50"):
    from app.schemas import EventBeacon

    return EventBeacon(
        event_type="order_created", bill_id=bill_id, post_id=post_id,
        url="https://artec.my/", code=code, value=449, currency="MYR", pack="single",
        market="MY", gateway="billplz", ts=datetime(2026, 8, 1, 9, 59, tzinfo=UTC),
    )


def test_order_created_stored_as_pending_keyed_on_bill_id(session):
    from app.api.routes_capture import ingest_event

    out = ingest_event(session, _order_created_beacon())
    assert out["deduped"] is False
    # checkout.php may retry — the pending row is idempotent on bill_id.
    assert ingest_event(session, _order_created_beacon())["deduped"] is True
    row = session.execute(select(Event)).scalar_one()
    assert row.dedupe_key == billplz_webhook.pending_order_key("bill9")
    assert row.post_id == "post_1483"
    assert row.code == "SOCIAL50"
    assert row.payload["pack"] == "single" and row.payload["bill_id"] == "bill9"


def test_order_created_requires_bill_id_and_browser_events_require_session():
    from pydantic import ValidationError

    from app.schemas import EventBeacon

    with pytest.raises(ValidationError):
        EventBeacon(event_type="order_created")
    with pytest.raises(ValidationError):
        EventBeacon(event_type="page_view")


def test_billplz_callback_joins_pending_row_and_is_idempotent(session):
    from app.api.routes_capture import ingest_event

    ingest_event(session, _order_created_beacon())
    form = {"id": "bill9", "paid": "true", "paid_amount": "44900", "email": "x@y.my",
            "paid_at": "2026-08-01 10:00:00 +0800"}
    assert billplz_webhook.handle_callback(session, form) == "attributed"
    assert billplz_webhook.handle_callback(session, form) == "duplicate"
    order = session.execute(select(Order)).scalar_one()
    assert order.post_id == "post_1483" and order.currency == "MYR"
    assert order.code == "SOCIAL50"
    assert order.amount_minor == 44900


def test_billplz_callback_without_pending_row_is_unattributed(session):
    # Direct Billplz link, or the pre-payment POST failed — never guessed.
    form = {"id": "bill_direct", "paid": "true", "paid_amount": "44900"}
    assert billplz_webhook.handle_callback(session, form) == "unattributed"
    order = session.execute(select(Order)).scalar_one()
    assert order.post_id is None and order.raw["pending_match"] is False


def test_billplz_webhook_module_does_no_external_http(repo_root):
    # 20-second / 5-retry Billplz contract: the handler must not call out.
    source = (repo_root / "app" / "integrations" / "billplz_webhook.py").read_text(encoding="utf-8")
    assert "httpx" not in source and "requests" not in source


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
