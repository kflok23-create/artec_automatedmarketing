"""v3 RULE 4 — USD 1.00 per render run, enforced before the money leaves.

A config price table (integer cents) estimates every model call before it is made. Two
independent guards, both tested: a per-call ceiling (a single USD 8 call is structurally
impossible) and a per-run cap (over cap → refuse the call, PARK the remainder). Running
spend prints after every call. An endpoint absent from the price table is uncallable —
which is also how the v3 endpoint removals are made permanent.
"""

from __future__ import annotations

from app.toolbox.text_guard import assert_no_text_render


class BudgetError(RuntimeError):
    pass


class UnknownEndpointPrice(BudgetError):
    """Endpoint not in the price table — deliberately uncallable."""


class PerCallCeilingExceeded(BudgetError):
    pass


class RunBudgetExceeded(BudgetError):
    """The run cap would be crossed — the caller PARKs the remaining posts."""


class RenderBudget:
    def __init__(self, prices_cents: dict[str, int], run_cap_cents: int,
                 per_call_ceiling_cents: int, log=print):
        self.prices = {k: int(v) for k, v in prices_cents.items()}
        self.run_cap = int(run_cap_cents)
        self.ceiling = int(per_call_ceiling_cents)
        self.spent_cents = 0
        self._log = log

    def estimate(self, endpoint: str) -> int:
        price = self.prices.get(endpoint)
        if price is None:
            raise UnknownEndpointPrice(
                f"endpoint {endpoint!r} has no entry in config.endpoint_prices_cents — "
                "unpriced endpoints are uncallable by design"
            )
        return price

    def charge(self, endpoint: str) -> int:
        est = self.estimate(endpoint)
        if est > self.ceiling:
            raise PerCallCeilingExceeded(
                f"{endpoint} estimated at {est}¢ — above the per-call ceiling of "
                f"{self.ceiling}¢; the call was refused before it was made"
            )
        if self.spent_cents + est > self.run_cap:
            raise RunBudgetExceeded(
                f"{endpoint} at {est}¢ would take the run to {self.spent_cents + est}¢, "
                f"over the {self.run_cap}¢ cap — call refused; park the remainder"
            )
        self.spent_cents += est
        self._log(f"  spend: +{est}¢ ({endpoint}) — run total {self.spent_cents}¢ of {self.run_cap}¢")
        return est


class GuardedFal:
    """The only fal handle the render path receives: every call passes the text guard and
    the budget. upload/fetch are storage plumbing, free, and uncounted."""

    def __init__(self, fal, budget: RenderBudget):
        self._fal = fal
        self.budget = budget

    def run(self, endpoint: str, arguments: dict, timeout_s: int = 600) -> dict:
        assert_no_text_render(str(arguments.get("prompt", "")), endpoint)
        self.budget.charge(endpoint)
        return self._fal.run(endpoint, arguments, timeout_s=timeout_s)

    def upload_public(self, local_path: str) -> str:
        return self._fal.upload_public(local_path)

    def fetch(self, url: str, suffix: str) -> str:
        return self._fal.fetch(url, suffix)
