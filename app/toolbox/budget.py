"""v4 Rule 4 — USD 2.50 per render run, enforced before the money leaves.

Two independent guards, both tested:
  • per-call ceiling, held at 50¢ and deliberately NOT raised with the run cap. Its job is
    to make a single runaway call structurally impossible; at confirmed rates 50¢ is ~8× a
    1080×1920 upscale and ~2× a 4K one. Raising both together would defeat having two limits.
  • per-run cap, raised 100¢ → 250¢ in v4.

Costs come from the unit-aware `endpoint_prices` table (app/toolbox/pricing.py), computed
from the REQUESTED output dimensions before the call — two fal endpoints bill per megapixel
and a flat per-call figure cannot represent them. Arithmetic is in micro-dollars end to end;
config caps are in cents and converted once, so no double-rounding can push a run past its
cap. Running spend prints after every call. An endpoint absent from the table, or present but
inactive, is uncallable — which is also how endpoint removal is made permanent.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.toolbox.pricing import (
    EndpointInactive,
    OutputTooLarge,
    UnknownEndpointPrice,
    cents_to_micros,
    estimate_micros,
    micros_to_cents,
)
from app.toolbox.text_guard import assert_no_text_render

# Re-exported so existing call sites and tests keep importing from one place.
__all__ = [
    "BudgetError", "RunBudgetExceeded", "PerCallCeilingExceeded", "UnknownEndpointPrice",
    "EndpointInactive", "OutputTooLarge", "RenderBudget", "GuardedFal",
]


class BudgetError(RuntimeError):
    pass


class PerCallCeilingExceeded(BudgetError):
    pass


class RunBudgetExceeded(BudgetError):
    """The run cap would be crossed — the caller PARKs the remaining posts."""


class RenderBudget:
    def __init__(self, session: Session, run_cap_cents: int, per_call_ceiling_cents: int,
                 max_output_megapixels: float | None = None, log=print):
        self._session = session
        self.run_cap_micros = cents_to_micros(run_cap_cents)
        self.ceiling_micros = cents_to_micros(per_call_ceiling_cents)
        self.max_megapixels = max_output_megapixels
        self.spent_micros = 0
        self._log = log

    # -- display -------------------------------------------------------------------------
    @property
    def spent_cents(self) -> float:
        return micros_to_cents(self.spent_micros)

    @property
    def run_cap_cents(self) -> float:
        return micros_to_cents(self.run_cap_micros)

    def estimate(self, endpoint: str, *, width: int | None = None, height: int | None = None,
                 images: int = 1) -> int:
        """Pre-call estimate in micros. Raises OutputTooLarge before any call is made when
        the requested output exceeds max_output_megapixels."""
        return estimate_micros(self._session, endpoint, width=width, height=height,
                               images=images, max_megapixels=self.max_megapixels)

    def charge(self, endpoint: str, *, width: int | None = None, height: int | None = None,
               images: int = 1) -> int:
        est = self.estimate(endpoint, width=width, height=height, images=images)
        if est > self.ceiling_micros:
            raise PerCallCeilingExceeded(
                f"{endpoint} estimated at {micros_to_cents(est):.2f}¢ — above the per-call "
                f"ceiling of {micros_to_cents(self.ceiling_micros):.0f}¢; refused before the "
                "call was made"
            )
        if self.spent_micros + est > self.run_cap_micros:
            raise RunBudgetExceeded(
                f"{endpoint} at {micros_to_cents(est):.2f}¢ would take the run to "
                f"{micros_to_cents(self.spent_micros + est):.2f}¢, over the "
                f"{micros_to_cents(self.run_cap_micros):.0f}¢ cap — call refused; park the "
                "remainder"
            )
        self.spent_micros += est
        dims = f" @{width}x{height}" if width and height else ""
        self._log(f"  spend: +{micros_to_cents(est):.2f}¢ ({endpoint}{dims}) — run total "
                  f"{self.spent_cents:.2f}¢ of {self.run_cap_cents:.0f}¢")
        return est


class GuardedFal:
    """The only fal handle the render path receives: every call passes the text guard and
    the budget. upload/fetch are storage plumbing, free, and uncounted."""

    def __init__(self, fal, budget: RenderBudget):
        self._fal = fal
        self.budget = budget

    def run(self, endpoint: str, arguments: dict, timeout_s: int = 600, *,
            width: int | None = None, height: int | None = None, images: int = 1) -> dict:
        assert_no_text_render(str(arguments.get("prompt", "")), endpoint)
        self.budget.charge(endpoint, width=width, height=height, images=images)
        return self._fal.run(endpoint, arguments, timeout_s=timeout_s)

    def upload_public(self, local_path: str) -> str:
        return self._fal.upload_public(local_path)

    def fetch(self, url: str, suffix: str) -> str:
        return self._fal.fetch(url, suffix)
