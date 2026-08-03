"""v4 §7·C5 — unit-aware fal pricing.

The v3 table stored a flat `cents_per_call`. That is **wrong for two of the four endpoints**:
clarity-upscaler and qwen-image-2512/lora bill **per megapixel**, so cost scales with the
requested output resolution. A flat figure under-charges a 4K upscale by 4× and would let the
run cap silently mean something different from what was intended.

Money is integer micro-dollars (1e-6 USD) end to end. 1 cent = 10_000 micros. Estimates are
computed BEFORE the call from the REQUESTED output dimensions.

Confirmed invoice rates (seeded, `source='invoice'`):

| endpoint | rate | unit | active in v4 |
|---|---|---|---|
| fal-ai/clarity-upscaler | $0.030 | per megapixel | YES — the only live endpoint |
| fal-ai/qwen-image-2512/lora | $0.035 | per megapixel | no — GENERATE dormant |
| fal-ai/flux-pro/kontext | $0.040 | per image | no — removed |
| fal-ai/flux-pro/kontext/multi | $0.040 | per image | no — removed |

Worked checks this module must reproduce (asserted in tests):
  1080×1920 upscale → 2.0736 MP × $0.030 = $0.0622  (62_208 micros)
  3840×2160 upscale → 8.2944 MP × $0.030 = $0.2488  (248_832 micros)
  2048×2048 upscale → 4.1943 MP × $0.030 = $0.1258  (125_829 micros)
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import EndpointPrice

MICROS_PER_CENT = 10_000
PIXELS_PER_MEGAPIXEL = 1_000_000

UNIT_PER_MEGAPIXEL = "per_megapixel"
UNIT_PER_IMAGE = "per_image"
VALID_UNITS = (UNIT_PER_MEGAPIXEL, UNIT_PER_IMAGE)

# Seed values — the confirmed invoice rates. `active` mirrors the v4 toolbox: only the
# upscaler is callable. Inactive endpoints stay priced so a future re-enable arrives priced.
SEED_PRICES: tuple[dict, ...] = (
    {"endpoint": "fal-ai/clarity-upscaler", "rate_micros": 30_000,
     "unit": UNIT_PER_MEGAPIXEL, "active": True},
    {"endpoint": "fal-ai/qwen-image-2512/lora", "rate_micros": 35_000,
     "unit": UNIT_PER_MEGAPIXEL, "active": False},
    {"endpoint": "fal-ai/flux-pro/kontext", "rate_micros": 40_000,
     "unit": UNIT_PER_IMAGE, "active": False},
    {"endpoint": "fal-ai/flux-pro/kontext/multi", "rate_micros": 40_000,
     "unit": UNIT_PER_IMAGE, "active": False},
)


class PricingError(RuntimeError):
    pass


class UnknownEndpointPrice(PricingError):
    """Endpoint absent from the price table — deliberately uncallable."""


class EndpointInactive(PricingError):
    """Priced but not active in v4 — uncallable until deliberately re-enabled."""


class OutputTooLarge(PricingError):
    """Requested output exceeds max_output_megapixels — refused BEFORE the call, so a
    per-megapixel endpoint can never be handed a resolution that prices past the ceiling."""


def megapixels(width: int, height: int) -> float:
    if width <= 0 or height <= 0:
        raise PricingError(f"invalid output dimensions: {width}x{height}")
    return (width * height) / PIXELS_PER_MEGAPIXEL


def seed_prices(session: Session) -> int:
    """Idempotent seed of the confirmed invoice rates. Never overwrites a reconciled row
    (one whose source is already 'fal_api') — reconciliation outranks the seed."""
    written = 0
    now = datetime.now(UTC)
    for row in SEED_PRICES:
        existing = session.get(EndpointPrice, row["endpoint"])
        if existing is None:
            session.add(EndpointPrice(source="invoice", as_of=now, **row))
            written += 1
        elif existing.source == "seed":
            existing.rate_micros = row["rate_micros"]
            existing.unit = row["unit"]
            existing.source = "invoice"
            existing.as_of = now
            written += 1
    session.flush()
    return written


def estimate_micros(session: Session, endpoint: str, *, width: int | None = None,
                    height: int | None = None, images: int = 1,
                    max_megapixels: float | None = None) -> int:
    """Pre-call cost estimate in micro-dollars, branching on the endpoint's billing unit.

    per_megapixel → rate × megapixels(width, height) × images  (dimensions REQUIRED)
    per_image     → rate × images                              (dimensions ignored)
    """
    price = session.get(EndpointPrice, endpoint)
    if price is None:
        raise UnknownEndpointPrice(
            f"endpoint {endpoint!r} has no row in endpoint_prices — unpriced endpoints are "
            "uncallable by design"
        )
    if not price.active:
        raise EndpointInactive(
            f"endpoint {endpoint!r} is priced but inactive in v4 — it is uncallable until "
            "deliberately re-enabled"
        )
    if price.unit == UNIT_PER_IMAGE:
        return int(price.rate_micros) * max(1, int(images))
    if price.unit != UNIT_PER_MEGAPIXEL:
        raise PricingError(f"unknown billing unit {price.unit!r} for {endpoint}")

    if width is None or height is None:
        raise PricingError(
            f"{endpoint} bills {UNIT_PER_MEGAPIXEL}; output dimensions are required to "
            "estimate cost before the call"
        )
    mp = megapixels(width, height)
    if max_megapixels is not None and mp > max_megapixels:
        raise OutputTooLarge(
            f"requested output {width}x{height} = {mp:.4f} MP exceeds max_output_megapixels "
            f"({max_megapixels}) — refused before the call"
        )
    # Round to the nearest micro; micros are fine-grained enough that this never distorts a
    # cap comparison (1 micro = $0.000001).
    return round(int(price.rate_micros) * mp) * max(1, int(images))


def micros_to_cents(micros: int) -> float:
    """Display helper only. Budget comparisons stay in micros to avoid double-rounding."""
    return micros / MICROS_PER_CENT


def cents_to_micros(cents: int | float) -> int:
    return int(round(float(cents) * MICROS_PER_CENT))


def price_table(session: Session) -> list[dict]:
    """The table as the digest and CHECKPOINT reporting render it."""
    rows = session.query(EndpointPrice).order_by(EndpointPrice.endpoint).all()
    return [
        {
            "endpoint": r.endpoint,
            "rate_usd": r.rate_micros / 1_000_000,
            "unit": r.unit,
            "active": r.active,
            "source": r.source,
            "as_of": r.as_of.isoformat() if r.as_of else None,
            "acknowledged_at": r.acknowledged_at.isoformat() if r.acknowledged_at else None,
        }
        for r in rows
    ]


def stale_prices(session: Session, max_age_days: int = 30) -> list[str]:
    """Endpoints whose price is older than max_age_days — warned in the digest."""
    now = datetime.now(UTC)
    stale = []
    for r in session.query(EndpointPrice).all():
        if r.as_of is None:
            stale.append(r.endpoint)
            continue
        as_of = r.as_of if r.as_of.tzinfo else r.as_of.replace(tzinfo=UTC)
        if (now - as_of).days > max_age_days:
            stale.append(r.endpoint)
    return sorted(stale)
