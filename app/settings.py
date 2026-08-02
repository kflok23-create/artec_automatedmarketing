"""Environment settings — fail fast at boot, naming the missing variable and nothing else.

Secret-hygiene contract (§16): no secret value ever appears in a log line, a `runs` row,
an exception message, or a checkpoint printout. `install_redaction` attaches a logging
filter that rewrites any secret value that slips into a message to `abc…wxyz` form.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class MissingEnvVarError(RuntimeError):
    """Carries variable NAMES only — never values, partial values, or shape hints."""

    def __init__(self, names: list[str]):
        self.names = sorted(str(n) for n in names)
        super().__init__("missing or invalid required environment variable(s): " + ", ".join(self.names))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Core service
    DATABASE_URL: str
    PORT: int = 8000
    HERMES_API_TOKEN: str
    PUBLIC_BASE_URL: str = ""  # does not exist until after first deploy (CHECKPOINT 2)
    ENVIRONMENT: str = "production"
    LOG_LEVEL: str = "INFO"

    # Anthropic
    ANTHROPIC_API_KEY: str
    ANTHROPIC_MODEL: str

    # fal.ai
    FAL_KEY: str

    # Google Drive (service account JSON is a raw JSON string, never a file path)
    GOOGLE_SERVICE_ACCOUNT_JSON: str
    GOOGLE_DRIVE_ROOT_FOLDER_ID: str
    GOOGLE_SHARED_DRIVE_ID: str

    # Upload-Post
    UPLOAD_POST_API_KEY: str
    UPLOAD_POST_USER: str

    # Brevo
    BREVO_API_KEY: str
    BREVO_LIST_ID: int
    BREVO_SENDER_EMAIL: str
    BREVO_TEMPLATE_ID: int

    # Telegram
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_CHAT_ID: str

    # Stripe (SG)
    STRIPE_SECRET_KEY: str
    STRIPE_WEBHOOK_SECRET: str

    # Billplz (MY)
    BILLPLZ_API_KEY: str
    BILLPLZ_COLLECTION_ID: str
    BILLPLZ_XSIGNATURE_KEY: str

    _SECRET_FIELDS = (
        "DATABASE_URL",
        "HERMES_API_TOKEN",
        "ANTHROPIC_API_KEY",
        "FAL_KEY",
        "GOOGLE_SERVICE_ACCOUNT_JSON",
        "UPLOAD_POST_API_KEY",
        "BREVO_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "BILLPLZ_API_KEY",
        "BILLPLZ_XSIGNATURE_KEY",
    )

    def secret_values(self) -> list[str]:
        vals = []
        for name in self._SECRET_FIELDS:
            v = getattr(self, name, "")
            if v:
                vals.append(str(v))
        return vals


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as e:
        # Name-only failure: never echo the offending value or its expected shape.
        names = [str(err["loc"][0]) if err.get("loc") else "<unknown>" for err in e.errors()]
        raise MissingEnvVarError(names) from None


def redact(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}…{value[-4:]}"


class SecretRedactionFilter(logging.Filter):
    def __init__(self, secrets: list[str]):
        super().__init__()
        # Longest first so substrings of longer secrets don't leave residue.
        self._secrets = sorted((s for s in secrets if s), key=len, reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        redacted = msg
        for s in self._secrets:
            if s in redacted:
                redacted = redacted.replace(s, redact(s))
        if redacted != msg:
            record.msg = redacted
            record.args = ()
        return True


def install_redaction(settings: Settings) -> SecretRedactionFilter:
    f = SecretRedactionFilter(settings.secret_values())
    root = logging.getLogger()
    root.addFilter(f)
    for h in root.handlers:
        h.addFilter(f)
    return f
