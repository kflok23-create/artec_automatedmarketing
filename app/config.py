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
    # post_id_counter REMOVED in v4 Stage 2b — ids come from the post_id_seq sequence so
    # that no tool ever writes config. See app/post_ids.py and migration 0004.
    # Informational mirror only — GOOGLE_DRIVE_ROOT_FOLDER_ID (env) is authoritative.
    # Post-migration Shared Drive id (techup.my Workspace); the old personal-Drive id
    # 1XjChLEO2WBLKemZ_7E9srIFa8fuFM0eN is retired.
    "drive_root_folder_id": "17gYS0IbakBLNVDLfX8-wZIpL61gOoXSr",
    "drive_generated_folder": "_generated",
    "drive_root_marker": None,  # set by assets sync; a root change forces a full rescan
    # v4: model releases settled — the 202 person-bearing assets join the matching pool.
    "allow_person_assets": True,
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
    # v3 TOOLBOX — Python first; the ONLY surviving model endpoints. Generative video and
    # every prompt-to-pixels / multi-image-combine endpoint are REMOVED (not flagged):
    # models cannot render letterforms, generated video was expensive and off-brand, and
    # 479 real photographs exist. The agent interprets, SQL calculates, Python does pixels.
    "model_endpoints": {
        "upscaler": "fal-ai/clarity-upscaler",
        "lora_generate": "fal-ai/qwen-image-2512/lora",  # dormant — see generate_enabled
    },
    # GENERATE via the artec LoRAs is DISABLED (v3 Rule 3: do not generate what we can
    # photograph). The lora rows below are retained so the operator can overturn this with
    # one config flip — reversible, not archaeology.
    "generate_enabled": False,
    # ENHANCE whitelist — anything off this list is not callable. Image only.
    # upscale = fal clarity (low creativity); color_correct/autocontrast = Pillow, zero cost.
    "enhance_whitelist": ["upscale", "color_correct", "autocontrast"],
    # v4 Rule 4 — the budget. USD in integer cents (no floats near money, ever).
    # NOTE: endpoint_prices_cents was REMOVED from config in v4 and lives in the
    # `endpoint_prices` TABLE. Reconciliation must be able to write prices, and no tool may
    # write config — moving the table is how both invariants survive (§7·C5). Prices are
    # unit-aware: two fal endpoints bill per MEGAPIXEL, which a flat per-call figure cannot
    # represent.
    "render_run_cap_cents": 250,     # v4: USD 2.50 per render run (was 100)
    # Deliberately NOT raised with the run cap: the per-call ceiling exists to make a single
    # runaway call structurally impossible. At confirmed rates 50¢ is ~8× a 1080×1920
    # upscale and ~2× a 4K one. Raising both together would defeat having two limits.
    "per_call_ceiling_cents": 50,
    # Hard bound on requested output size — refused BEFORE the call, so a per-megapixel
    # endpoint can never be handed a resolution that prices past the ceiling.
    "max_output_megapixels": 4.0,
    # USD 15.00/week of agent LLM spend, metered from agent_runs. Raised from 500 in v4
    # Stage 2b: the brain's weekly load after full autonomy is one LEARN→IDEATE, one ~45
    # minute gate conversation and six digest sessions that stay open to relay replies —
    # materially more than the two Sunday jobs it does today, and 500 was a guess nobody
    # had data for. The degradation order on approach is drop scouting, then SHORTEN THE
    # GATE CONVERSATION, so a cap set too low does not fail loudly: it quietly shortens the
    # single most valuable human touch in the system, every week, while reporting green.
    # agent_runs now records cost, so after two real weeks this is set from evidence.
    "agent_weekly_cap_minor": 1500,

    # v4 review gates — the two surfaces that never auto-publish.
    "email_review_expiry_days": 3,   # stale email is worse than no email → PARK
    "video_review_expiry_days": 3,

    # v4 §E2 — a one-contact list cannot be scored. Below this, email is reported
    # "below measurement threshold" and EXCLUDED from lever kill decisions.
    "email_min_recipients": 25,

    # v4 §E1 — organic only, permanently. Paid spend is zero by decision, so CAC can never
    # fire an absolute kill line; levers are pruned RELATIVELY instead. See DECISIONS.md.
    "weekly_spend_minor": {},        # stays empty by policy, not by omission
    "min_lever_sample": 6,           # never kill on a single week
    "kill_margin": 0.30,             # bottom-quartile band width for the relative rule
    # Retained but INACTIVE so they exist if paid acquisition is ever introduced. They must
    # never silently evaluate to "pass" — learn() reports them as inactive with this reason.
    "kill_lines_active": False,
    "kill_lines_inactive_reason": "organic-only: zero paid spend makes an absolute CAC kill "
                                  "line structurally incapable of firing (v4 §E1)",
    # v3 §9 — slot becomes a real firing time (Asia/Singapore) AND stays a learned lever.
    "slot_times": {"morning": "09:00", "lunch": "12:30", "evening": "19:00", "weekend": "10:00"},
    "measure_reminder_time": "06:30",
    # v3 §11 — shadow-mode cutover. Starts and stays on 'shadow' until the operator flips
    # it after 2–3 Sundays of plan-diff. 'bespoke' is full rollback, no redeploy.
    "plan_source": "shadow",

    # v4 §9 — dated proofs for the nine capabilities that are built but never exercised.
    # doctor reports absent/>90d proofs YELLOW; the digest lists unproven ones weekly.
    "proofs": {},
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
    # NOTE: weekly_spend_minor is declared once, above, in the v4 §E1 block — it stays
    # permanently empty by policy (organic only), not by omission.
}


# Runtime state keys are NEVER overwritten by seeding, not even with --force.
RUNTIME_KEYS = frozenset({
    "drive_page_token", "drive_root_marker",
    "confirm_first_publish", "text_card_pairing_idx",
})

# Keys DELETED from live config on seed — v3 removed these capabilities outright
# (all generative-video and prompt-to-pixels/combine endpoints). Removal is what makes the
# repo scan meaningful: those endpoints exist nowhere, not even in a dormant row.
# v4 adds: endpoint_prices_cents (moved to the endpoint_prices TABLE so reconciliation can
# write prices without any tool writing config) and video_pipeline_proven (superseded by the
# `proofs` manifest — a per-capability dated proof, not a boolean).
REMOVED_KEYS = ("image_endpoints", "video_family", "endpoint_prices_cents",
                "video_pipeline_proven", "render_budget_cents", "post_id_counter")

# Keys whose SHIPPED DEFAULT was superseded. A non-destructive seed keeps whatever is
# stored, which is right for operator choices and wrong for a corrected default: the old
# number was never chosen by anyone, so it would be inherited forever. If the stored value
# is still exactly a superseded default, nobody picked it — take the new one and report it.
# An operator-set value is never touched by this.
SUPERSEDED_DEFAULTS: dict[str, tuple[Any, ...]] = {
    # 500 (USD 5.00/week) was a guess made before the brain ran nightly digest sessions.
    "agent_weekly_cap_minor": (500,),
}


# ---------------------------------------------------------------------------------------
# v4 §7·C8 — config keys are load-bearing and were silently so. `slot_times` missing once
# disabled the ENTIRE scheduler with nothing but a per-tick log line. Every service now
# validates its manifest at boot and REFUSES TO START, naming the missing key.
# ---------------------------------------------------------------------------------------
REQUIRED_CONFIG_KEYS: dict[str, tuple[str, ...]] = {
    # keys every service needs to function at all
    "common": (
        "site_base_url", "social_code", "email_code", "timezone",
        "post_id_prefix",
        "drive_root_folder_id", "drive_generated_folder",
        "channel_cadence", "channel_media", "kpi_weights",
        "sg_price_minor", "my_price_minor", "sg_currency", "my_currency",
        "cm_per_unit_sgd_minor", "cm_per_unit_myr_minor",
        "discount_sgd_minor", "discount_myr_minor",
        "plan_source", "allow_person_assets",
    ),
    # artec-scheduler: without these the twelve jobs cannot fire correctly
    "scheduler": (
        "slot_times", "measure_reminder_time",
        "render_run_cap_cents", "per_call_ceiling_cents", "max_output_megapixels",
        "email_review_expiry_days", "video_review_expiry_days",
    ),
    # artec api: render + publish + report
    "api": (
        "model_endpoints", "enhance_whitelist", "fonts", "generate_enabled",
        "render_run_cap_cents", "per_call_ceiling_cents", "max_output_megapixels",
        "seo_seeds", "email_min_recipients", "min_lever_sample", "kill_margin",
    ),
}


class RequiredConfigMissing(RuntimeError):
    """Boot-blocking: a load-bearing config key is absent or empty. Names the key."""


def validate_required_config(session: Session, role: str) -> list[str]:
    """Validate the manifest for one service role. Returns the checked key list; raises
    RequiredConfigMissing naming every absent/empty key. Called at boot — refuse to start
    rather than discover it from a per-tick error log three hours later."""
    keys = list(REQUIRED_CONFIG_KEYS["common"]) + list(REQUIRED_CONFIG_KEYS.get(role, ()))
    missing: list[str] = []
    for key in keys:
        row = session.get(Config, key)
        if row is None:
            missing.append(f"{key} (absent)")
            continue
        value = row.value
        # False and 0 are legitimate values; only None and empty containers are "empty".
        if value is None or (isinstance(value, str | list | dict) and len(value) == 0):
            # seo_seeds empty is a *warning* elsewhere (ideate refuses); everything else is fatal
            if key != "seo_seeds":
                missing.append(f"{key} (empty)")
    if missing:
        raise RequiredConfigMissing(
            f"required config missing for role '{role}': {', '.join(sorted(missing))} — "
            "run `artec config seed`"
        )
    return keys


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
    removed: list[str] = []
    upgraded: list[str] = []
    needs_decision: list[str] = []
    for key in REMOVED_KEYS:
        row = session.get(Config, key)
        if row is not None:
            session.delete(row)
            removed.append(key)
    for key, value in data.items():
        row = session.get(Config, key)
        if row is None:
            session.add(Config(key=key, value=value, updated_at=datetime.now(UTC),
                               set_by="seed"))
            added.append(key)
            continue
        if row.value == value or key in RUNTIME_KEYS:
            continue
        if row.value in SUPERSEDED_DEFAULTS.get(key, ()):
            # Only a value this seeder wrote may be corrected. An operator's choice is
            # never overwritten, and an UNKNOWN provenance is not treated as permission —
            # it is reported, and the operator decides.
            if row.set_by == "seed":
                row.value = value
                row.updated_at = datetime.now(UTC)
                upgraded.append(key)
                continue
            if row.set_by is None:
                needs_decision.append(
                    f"{key}: stored {row.value!r} is a superseded default and the shipped "
                    f"value is now {value!r}, but this row predates provenance tracking so "
                    f"it cannot be told from a deliberate choice — take it with "
                    f"`artec config set {key} {value!r}` or leave it")
                kept.append(key)
                continue
        if force or key in forced_keys:
            row.value = value
            row.updated_at = datetime.now(UTC)
            row.set_by = "operator" if key in forced_keys else "seed"
            overwritten.append(key)
        else:
            kept.append(key)
    # v4: the fal price table lives in its own table so reconciliation never writes config.
    from app.toolbox.pricing import seed_prices

    priced = seed_prices(session)

    session.flush()
    return {"added": sorted(added), "kept": sorted(kept),
            "overwritten": sorted(overwritten), "removed": sorted(removed),
            "upgraded": sorted(upgraded), "needs_decision": sorted(needs_decision),
            "prices_seeded": priced}


def get_config(session: Session, key: str, default: Any = _RAISE) -> Any:
    row = session.get(Config, key)
    if row is None:
        if default is _RAISE:
            raise OperatorError(f"config key '{key}' missing — run `artec config seed`")
        return default
    return row.value


def set_config(session: Session, key: str, value: Any, set_by: str | None = None) -> None:
    """`set_by='operator'` marks a deliberate choice, which supersession may never
    overwrite. Runtime state (cursors, gates, last_doctor) leaves provenance alone."""
    row = session.get(Config, key)
    if row is None:
        session.add(Config(key=key, value=value, updated_at=datetime.now(UTC),
                           set_by=set_by))
    else:
        row.value = value
        row.updated_at = datetime.now(UTC)
        if set_by:
            row.set_by = set_by
    session.flush()


def all_config(session: Session) -> dict[str, Any]:
    return {row.key: row.value for row in session.execute(select(Config)).scalars()}


def next_post_id(session: Session) -> str:
    """Monotonic post ids: post_1482, post_1483, …

    Allocated from the `post_id_seq` SEQUENCE, never from `config` — allocation is state,
    not policy, and keeping it in config made every injection a config write (§2 breach).
    """
    from app.post_ids import allocate_post_id

    prefix = get_config(session, "post_id_prefix", "post_")
    post_id = allocate_post_id(session.connection(), prefix)
    session.flush()
    return post_id


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
