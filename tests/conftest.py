"""Shared fixtures. All §11 env vars get dummy values BEFORE app.settings is imported so
boot validation passes in tests; individual tests clear the cache to test failure modes.
Unit tests run on in-memory SQLite (DECISIONS.md #7)."""

from __future__ import annotations

import os

DUMMY_ENV = {
    "DATABASE_URL": "sqlite://",
    "HERMES_API_TOKEN": "test-token",
    "PUBLIC_BASE_URL": "",
    "ENVIRONMENT": "test",
    "LOG_LEVEL": "INFO",
    "ANTHROPIC_API_KEY": "test-anthropic-key-fixture-000",
    "ANTHROPIC_MODEL": "claude-test",
    "FAL_KEY": "test-fal-key",
    "GOOGLE_SERVICE_ACCOUNT_JSON": "{}",
    "GOOGLE_DRIVE_ROOT_FOLDER_ID": "root123",
    "GOOGLE_SHARED_DRIVE_ID": "drive123",
    "UPLOAD_POST_API_KEY": "test-upload-key",
    "UPLOAD_POST_USER": "ArtecMy",
    "BREVO_API_KEY": "test-brevo-key",
    "BREVO_LIST_ID": "3",
    "BREVO_SENDER_EMAIL": "hello@artec.my",
    "BREVO_TEMPLATE_ID": "3",
    "TELEGRAM_BOT_TOKEN": "1234:test-telegram-token",
    "TELEGRAM_CHAT_ID": "42",
    "STRIPE_SECRET_KEY": "test-stripe-secret-key-0000",
    "STRIPE_WEBHOOK_SECRET": "test-stripe-webhook-secret",
    "BILLPLZ_API_KEY": "test-billplz-key",
    "BILLPLZ_COLLECTION_ID": "coll1",
    "BILLPLZ_XSIGNATURE_KEY": "test-xsig-key",
}
os.environ.update(DUMMY_ENV)

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.config import seed_config  # noqa: E402
from app.models import V_BRIEF_SQL, Base  # noqa: E402


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    with eng.connect() as conn:
        conn.execute(text(V_BRIEF_SQL))
        conn.commit()
    return eng


@pytest.fixture
def session(engine):
    with Session(engine, expire_on_commit=False) as s:
        seed_config(s)
        s.commit()
        yield s


@pytest.fixture
def repo_root():
    from pathlib import Path

    return Path(__file__).resolve().parent.parent
