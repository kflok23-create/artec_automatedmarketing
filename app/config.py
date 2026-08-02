"""Operator constants (§0), the `config` table accessors, the post-id counter, and the
money math. ALL money is integer minor units; the loop scores NET contribution margin only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Config


class OperatorError(RuntimeError):
    """An error the operator must fix (config, credentials, groundwork) — not a code bug."""


_RAISE = object()

# ---------------------------------------------------------------------------------------
# §0 OPERATOR CONSTANTS — seeded into the config table by `artec config seed`.
# Plain config only; secrets never appear here.
# ---------------------------------------------------------------------------------------
OPERATOR_CONSTANTS: dict[str, Any] = {
    "site_base_url": "https://artec.my",
    "social_code": "SOCIAL50",
    "email_code": "EMAIL50",
    "timezone": "Asia/Singapore",
    "post_id_prefix": "post_",
    "post_id_start": 1482,
    "post_id_counter": 1482,  # next id to hand out; monotonic
    # Informational mirror only — GOOGLE_DRIVE_ROOT_FOLDER_ID (env) is authoritative.
    # Post-migration Shared Drive id (techup.my Workspace); the old personal-Drive id
    # 1XjChLEO2WBLKemZ_7E9srIFa8fuFM0eN is retired.
    "drive_root_folder_id": "17gYS0IbakBLNVDLfX8-wZIpL61gOoXSr",
    "drive_generated_folder": "_generated",
    "drive_root_marker": None,  # set by assets sync; a root change forces a full rescan
    "allow_person_assets": False,
    # Pricing — integer minor units, no floats anywhere.
    "sg_price_minor": 14900,
    "sg_currency": "SGD",
    "my_price_minor": 44900,
    "my_currency": "MYR",
    "discount_sgd_minor": 1000,
    "discount_myr_minor": 4000,
    "cm_per_unit_sgd_minor": 8400,   # GROSS — never scored directly
    "cm_per_unit_myr_minor": 25200,  # GROSS — never scored directly
    "kill_line_cac_sgd_minor": 2000,
    "kill_line_cac_myr_minor": 6000,
    "channel_cadence": {
        "instagram": 3, "tiktok": 2, "linkedin": 1, "facebook": 1, "youtube": 1, "email": 1,
    },
    "kpi_weights": {"engagement": 0.3, "traffic": 0.3, "sales": 0.4},
    # STILL BLANK by operator instruction — ideate refuses to run until 5–15 seeds are set.
    "seo_seeds": [],
    # Brand tokens
    "brand_light": {
        "bg": "#F4F8FC", "grid": "#E2ECF6", "text": "#12212F", "accent": "#0168B7",
        "accent_deep": "#014E8B", "accent_tint": "#E9F3FB", "muted": "#4C5A67",
    },
    "brand_dark": {"bg": "#12141A", "text": "#F5F3EE", "accent": "#E8A840", "muted": "#9698A0"},
    # TEXT CARD: three approved pairings ONLY, text colour locked per background.
    "text_card_pairings": [
        {"bg": "#0168B7", "text": "#F5F3EE"},
        {"bg": "#014E8B", "text": "#F5F3EE"},
        {"bg": "#E8A840", "text": "#12212F"},  # never light text on amber
    ],
    "text_card_pairing_idx": 0,
    "fonts": {
        "display": "BricolageGrotesque-Bold.ttf",
        "body": "HankenGrotesk-Regular.ttf",
        "body_semibold": "HankenGrotesk-SemiBold.ttf",
        "label": "SpaceMono-Regular.ttf",
    },
    # Per-channel media targets — duration/aspect come from config, never model defaults.
    "channel_media": {
        "tiktok":    {"media": "video", "aspect": "vertical",  "aspect_ratio": "9:16", "duration_s": 12, "resolution": "720p", "max_caption": 2200},
        "instagram": {"media": "photo", "aspect": "square",    "max_caption": 2200},
        "facebook":  {"media": "photo", "aspect": "square",    "max_caption": 5000},
        "youtube":   {"media": "video", "aspect": "vertical",  "aspect_ratio": "9:16", "duration_s": 15, "resolution": "720p", "max_caption": 5000, "max_title": 100},
        "linkedin":  {"media": "photo", "aspect": "landscape", "max_caption": 3000},
        "email":     {"media": "photo", "aspect": "landscape"},
    },
    # Image endpoints — locked set.
    "image_endpoints": {
        "lora": "fal-ai/qwen-image-2512/lora",
        "kontext_t2i": "fal-ai/flux-pro/kontext/text-to-image",
        "kontext_single": "fal-ai/flux-pro/kontext",
        "kontext_multi": "fal-ai/flux-pro/kontext/multi",
        "upscaler": "fal-ai/clarity-upscaler",
    },
    # Video family — endpoint ids and prompt-reference syntax live TOGETHER so they can
    # never drift apart. reference_syntax: "bracket" → [Image1] [Video1]; "at" → @Video1.
    "video_family": {
        "name": "seedance-2.0",
        "text_to_video": "bytedance/seedance-2.0/text-to-video",
        "reference_to_video": "bytedance/seedance-2.0/reference-to-video",
        "reference_syntax": "bracket",
        "max_ref_videos": 3,
        "duration_range_s": [4, 15],
        "resolutions": ["480p", "720p"],
        "verified": False,  # doctor flips after first successful call
    },
    # LoRAs — registered in config, never hardcoded. One LoRA, one trigger, one request.
    "loras": {
        "assembled": {
            "path": "https://v3b.fal.media/files/b/0aa44eca/STKjW7AbkplQT9mJBedDz_pytorch_lora_weights.safetensors",
            "trigger": "artec blocks assembled",
            "scale": 1.0,
            "config_file": "https://v3b.fal.media/files/b/0aa44ecb/LC_1mMQE7F1WRBbTVT-tq_config_039afd2e-2105-487f-af44-890d467c1837.json",
        },
        "unassembled": {
            "path": "https://v3b.fal.media/files/b/0aa43a2f/Ikmk2fkxUUzmH1YMTW8kN_pytorch_lora_weights.safetensors",
            "trigger": "artec block",
            "scale": 1.0,
            "config_file": "https://v3b.fal.media/files/b/0aa43a30/rJ-hB10fHi1nANkd0nUET_config_9ceef13e-c98f-4857-bb82-b03e2e5bbe25.json",
        },
    },
    # First-publish safety gate (CHECKPOINT 4) — fires once per install, then persists off.
    "confirm_first_publish": True,
    # Drive changes-API cursor (set by assets sync)
    "drive_page_token": None,
    # Optional: per-channel weekly spend for CAC — {"tiktok": {"currency": "SGD", "amount_minor": 0}, ...}
    "weekly_spend_minor": {},
}


# Runtime state keys are NEVER overwritten by seeding, not even with --force.
RUNTIME_KEYS = frozenset({
    "post_id_counter", "drive_page_token", "drive_root_marker",
    "confirm_first_publish", "text_card_pairing_idx",
})


def seed_config(
    session: Session,
    overrides: dict[str, Any] | None = None,
    force: bool = False,
) -> dict[str, list[str]]:
    """NON-DESTRUCTIVE seed of §0 constants: adds missing keys only.

    Existing keys whose stored value differs from the shipped default are KEPT and
    reported — re-seeding must never silently clobber operator-set values (seo_seeds once
    died this way). Overwriting requires `--force`, or passing the key explicitly via
    `--file` overrides (an explicit override is operator intent). Runtime state keys
    (counters, cursors, gates) are never touched either way.
    """
    forced_keys = set(overrides or {})
    data = dict(OPERATOR_CONSTANTS)
    if overrides:
        data.update(overrides)
    added: list[str] = []
    kept: list[str] = []
    overwritten: list[str] = []
    for key, value in data.items():
        row = session.get(Config, key)
        if row is None:
            session.add(Config(key=key, value=value, updated_at=datetime.now(UTC)))
            added.append(key)
            continue
        if row.value == value or key in RUNTIME_KEYS:
            continue
        if force or key in forced_keys:
            row.value = value
            row.updated_at = datetime.now(UTC)
            overwritten.append(key)
        else:
            kept.append(key)
    session.flush()
    return {"added": sorted(added), "kept": sorted(kept), "overwritten": sorted(overwritten)}


def get_config(session: Session, key: str, default: Any = _RAISE) -> Any:
    row = session.get(Config, key)
    if row is None:
        if default is _RAISE:
            raise OperatorError(f"config key '{key}' missing — run `artec config seed`")
        return default
    return row.value


def set_config(session: Session, key: str, value: Any) -> None:
    row = session.get(Config, key)
    if row is None:
        session.add(Config(key=key, value=value, updated_at=datetime.now(UTC)))
    else:
        row.value = value
        row.updated_at = datetime.now(UTC)
    session.flush()


def all_config(session: Session) -> dict[str, Any]:
    return {row.key: row.value for row in session.execute(select(Config)).scalars()}


def next_post_id(session: Session) -> str:
    """Monotonic post ids: post_1482, post_1483, … Counter lives in config."""
    row = session.get(Config, "post_id_counter")
    if row is None:
        raise OperatorError("config key 'post_id_counter' missing — run `artec config seed`")
    n = int(row.value)
    row.value = n + 1
    row.updated_at = datetime.now(UTC)
    session.flush()
    prefix = get_config(session, "post_id_prefix", "post_")
    return f"{prefix}{n}"


# ---------------------------------------------------------------------------------------
# Money — NET CM is the only CM the loop may use.
# ---------------------------------------------------------------------------------------

def net_cm_minor(currency: str, cfg: dict[str, Any] | None = None) -> int:
    """Net contribution margin per unit, in minor units. Every code discounts, so every
    attributed sale is discounted: net = gross CM − discount, per currency. Never convert
    between currencies."""
    c = cfg if cfg is not None else OPERATOR_CONSTANTS
    if currency == "SGD":
        return int(c["cm_per_unit_sgd_minor"]) - int(c["discount_sgd_minor"])
    if currency == "MYR":
        return int(c["cm_per_unit_myr_minor"]) - int(c["discount_myr_minor"])
    raise ValueError(f"unknown currency for net CM: {currency}")


def kill_line_minor(currency: str, cfg: dict[str, Any] | None = None) -> int:
    c = cfg if cfg is not None else OPERATOR_CONSTANTS
    if currency == "SGD":
        return int(c["kill_line_cac_sgd_minor"])
    if currency == "MYR":
        return int(c["kill_line_cac_myr_minor"])
    raise ValueError(f"unknown currency for kill line: {currency}")
