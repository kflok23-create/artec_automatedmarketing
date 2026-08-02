"""Acceptance 2h (no UTM on the Stripe path), 2i/2j/2k (secret hygiene), 3 (no scheduler)."""

import logging
import re

import pytest

from app.settings import MissingEnvVarError, SecretRedactionFilter, get_settings


def test_stripe_webhook_module_never_touches_utm(repo_root):
    source = (repo_root / "app" / "integrations" / "stripe_webhook.py").read_text(encoding="utf-8")
    assert "utm" not in source.lower()  # 2h — reading campaign params off Stripe is a bug


def test_secret_redaction_filter(caplog):
    # 2i: a fixture secret passed through the logger comes out redacted.
    secret = "sk-fixture-super-secret-value-9999"
    logger = logging.getLogger("hermes.test")
    logger.addFilter(SecretRedactionFilter([secret]))
    with caplog.at_level(logging.INFO, logger="hermes.test"):
        logger.info("failed calling api with key %s", secret)
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert secret not in joined
    assert "sk-…9999" in joined


def test_gitignore_covers_secret_files(repo_root):
    gitignore = (repo_root / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".env", "*.pem", "*service-account*.json", ".env.*", "!.env.example"):
        assert pattern in gitignore, f".gitignore is missing {pattern}"


SECRET_PATTERNS = re.compile(
    r"(sk-ant-api[0-9A-Za-z_-]{10,}"
    r"|whsec_[0-9A-Za-z]{16,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN (RSA |EC )?PRIVATE KEY-----"
    r"|\d{8,10}:AA[0-9A-Za-z_-]{30,})"
)


def test_repo_scan_finds_no_committed_secret(repo_root):
    # 2j: scan tracked source files for secret-shaped strings.
    skip_dirs = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}
    offenders = []
    for path in repo_root.rglob("*"):
        if not path.is_file() or set(path.parts) & skip_dirs:
            continue
        if path.suffix in {".png", ".jpg", ".ttf", ".ico", ".pyc"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if SECRET_PATTERNS.search(text):
            offenders.append(str(path))
    assert not offenders, f"secret-shaped strings committed in: {offenders}"


def test_boot_failure_names_variable_without_value(monkeypatch):
    # 2k / 21: missing secret → the variable NAME only, no partial value, no shape hint.
    get_settings.cache_clear()
    monkeypatch.delenv("FAL_KEY", raising=False)
    with pytest.raises(MissingEnvVarError) as exc:
        get_settings()
    get_settings.cache_clear()
    msg = str(exc.value)
    assert "FAL_KEY" in msg
    assert "test-" not in msg  # no dummy values leak


SCHEDULER_IMPORT = re.compile(
    r"^\s*(import|from)\s+(cron\b|apscheduler|celery|rq\b|schedule\b|boto3)", re.MULTILINE | re.IGNORECASE
)


def test_no_scheduler_or_s3_import_anywhere(repo_root):
    # 3: repo-wide — no cron, APScheduler, celery, rq, schedule, or boto3 import exists.
    offenders = []
    for path in (repo_root / "app").rglob("*.py"):
        if SCHEDULER_IMPORT.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path))
    assert not offenders, f"scheduler/S3 imports found in: {offenders}"
