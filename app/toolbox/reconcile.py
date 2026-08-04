"""Price reconciliation — job 1's FIRST action, before the weekly snapshot.

Order matters: the weekly report, the Sunday gate and every subsequent render must see
reconciled prices, not last week's. Reconciling after the snapshot would produce a report
costed against rates that were already known to be wrong.

Writes `endpoint_prices`. **Never `config`.** The no-config-writes rule is not weakened to
make this convenient — that is precisely how a security property erodes, and the seam grep
must still return zero.

WHAT IS AND IS NOT VERIFIED: fal publishes no pricing API this project has confirmed. So
`pull_rates` PROBES a candidate endpoint and reports exactly what came back — status, shape,
and whether anything usable was parsed. It never fabricates a rate, and a failed pull is not
a failure of job 1: the table is flagged stale, the digest says so, and the week continues.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import EndpointPrice
from app.toolbox.pricing import UNIT_PER_MEGAPIXEL

FAL_PRICING_URL = "https://fal.ai/api/pricing"

# The reference render the cap is reasoned about in: one 1080×1920 upscale.
REFERENCE_MEGAPIXELS = (1080 * 1920) / 1_000_000


@dataclass
class Reconciliation:
    pulled: bool
    source: str
    applied: list[dict] = field(default_factory=list)
    needs_acknowledgement: list[dict] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    reason: str = ""


def calls_at_cap(rate_micros: int, unit: str, cap_cents: int,
                 megapixels: float = REFERENCE_MEGAPIXELS) -> int:
    """How many reference renders the run cap affords at this rate.

    This is the number the cap actually MEANS. A rate change that leaves it unchanged is
    housekeeping; one that changes it means USD 2.50 now buys a different amount of work
    than the person who set it intended — which is exactly the silent redefinition the
    per-megapixel correction fixed once already.
    """
    per_call = rate_micros * megapixels if unit == UNIT_PER_MEGAPIXEL else rate_micros
    if per_call <= 0:
        return 0
    return int((cap_cents * 10_000) // per_call)


def is_material(old_micros: int, new_micros: int, unit: str, cap_cents: int) -> bool:
    return calls_at_cap(old_micros, unit, cap_cents) != calls_at_cap(new_micros, unit,
                                                                     cap_cents)


def pull_rates(client=None, url: str = FAL_PRICING_URL) -> tuple[dict, str]:
    """Probe fal for live rates. Returns ({endpoint: micros}, human-readable source note).

    Reports what actually happened rather than assuming a contract: an empty dict with the
    reason is a valid, honest outcome, and the caller treats it as "stale", never as "no
    change".
    """
    import httpx

    getter = client.get if client is not None else httpx.get
    try:
        response = getter(url, timeout=30)
    except Exception as e:
        return {}, f"fal pricing unreachable: {type(e).__name__}: {e}"
    if getattr(response, "status_code", 0) != 200:
        return {}, f"fal pricing returned HTTP {response.status_code}"
    try:
        body = response.json()
    except Exception:
        return {}, "fal pricing returned a non-JSON body"

    rates: dict[str, int] = {}
    entries = body.get("endpoints") if isinstance(body, dict) else body
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("endpoint") or entry.get("id") or entry.get("name")
        price = entry.get("price_usd", entry.get("price", entry.get("rate_usd")))
        if name and isinstance(price, int | float):
            rates[str(name)] = int(round(float(price) * 1_000_000))
    if not rates:
        return {}, ("fal pricing parsed but carried no recognisable endpoint rates — the "
                    "response shape is not the one this parser expects")
    return rates, f"fal pricing: {len(rates)} endpoint rate(s)"


def reconcile_prices(session: Session, cap_cents: int, client=None,
                     now: datetime | None = None, log=print) -> Reconciliation:
    """Job 1's first action. Never raises; a failed pull flags stale and continues."""
    now = now or datetime.now(UTC)
    rates, source = pull_rates(client=client)
    rows = session.query(EndpointPrice).order_by(EndpointPrice.endpoint).all()

    if not rates:
        stale = [r.endpoint for r in rows]
        log(f"reconcile: NOT RECONCILED — {source}. The table is flagged stale and the "
            "week continues; the digest reports it.")
        return Reconciliation(pulled=False, source=source, stale=stale, reason=source)

    result = Reconciliation(pulled=True, source=source)
    for row in rows:
        new = rates.get(row.endpoint)
        if new is None:
            # An endpoint fal did not price is not an endpoint priced at zero.
            result.stale.append(row.endpoint)
            continue
        old = int(row.rate_micros)
        if new == old:
            row.as_of = now
            row.source = "fal_api"
            result.unchanged.append(row.endpoint)
            continue
        delta = {"endpoint": row.endpoint, "old_micros": old, "new_micros": new,
                 "unit": row.unit,
                 "calls_at_cap_before": calls_at_cap(old, row.unit, cap_cents),
                 "calls_at_cap_after": calls_at_cap(new, row.unit, cap_cents),
                 "active": bool(row.active)}
        if is_material(old, new, row.unit, cap_cents):
            # DO NOT apply. The cap would silently come to mean something else.
            result.needs_acknowledgement.append(delta)
            log(f"reconcile: {row.endpoint} {old}→{new} micros is MATERIAL — the run cap "
                f"would afford {delta['calls_at_cap_after']} renders instead of "
                f"{delta['calls_at_cap_before']}. Held for acknowledge_price_table.")
        else:
            row.rate_micros = new
            row.as_of = now
            row.source = "fal_api"
            result.applied.append(delta)
            log(f"reconcile: {row.endpoint} {old}→{new} micros applied "
                f"(cap still affords {delta['calls_at_cap_after']} renders)")
    session.flush()
    return result
