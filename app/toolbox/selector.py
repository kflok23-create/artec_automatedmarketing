"""§7 tool selection — one documented, independently unit-testable function returning an
ordered tool list plus chosen asset ids.

The model proposes; `validate_plan` (pure) enforces the hard rules:
- BANK-FIRST: a plan may only reach GENERATE when the bank offered no usable candidate.
- Only known tools, in a sensible order; chosen asset ids must come from the candidates.
- ENHANCE never terminates a video chain (it is image-only; the executor re-checks).
If the model's plan is invalid, the deterministic fallback replaces it — a defect in the
model's routing must never turn into a defect in the render.
"""

from __future__ import annotations

from app.models import Asset
from app.schemas import ToolPlan

VALID_TOOLS = ("asset", "edit_combine", "generate", "enhance", "text_card")
GENERATABLE_SUBJECTS = ("loose_blocks", "assembled_blocks")


class PlanError(RuntimeError):
    pass


def validate_plan(plan: dict, candidates: list[Asset], allow_person: bool) -> ToolPlan:
    p = ToolPlan.model_validate(plan)
    unknown = [t for t in p.tools if t not in VALID_TOOLS]
    if unknown:
        raise PlanError(f"unknown tool(s) {unknown}; valid: {VALID_TOOLS}")

    candidate_ids = {c.drive_file_id for c in candidates}
    bad_ids = [a for a in p.asset_ids if a not in candidate_ids]
    if bad_ids:
        raise PlanError(f"plan references asset ids not in the offered candidates: {bad_ids}")

    if "asset" in p.tools and not p.asset_ids:
        raise PlanError("plan uses 'asset' but chose no asset ids")

    # BANK-FIRST HARD RULE: generating while usable bank candidates exist and none are
    # consumed wastes both the curation and the spend.
    if "generate" in p.tools and candidates and not p.asset_ids:
        raise PlanError("bank-first violation: candidates exist but the plan generates from scratch")

    if "generate" in p.tools and p.subject not in GENERATABLE_SUBJECTS:
        raise PlanError(f"GENERATE cannot produce subject {p.subject!r} (LoRAs cover {GENERATABLE_SUBJECTS})")

    if not allow_person:
        flagged = [c.drive_file_id for c in candidates
                   if c.drive_file_id in p.asset_ids and c.has_person is True]
        if flagged:
            raise PlanError(f"person assets are gated off (allow_person_assets=false): {flagged}")

    return p


def fallback_plan(subject: str, candidates: list[Asset], media_kind: str) -> ToolPlan | None:
    """Deterministic plan when the model's routing is invalid. None → park."""
    if candidates:
        chosen = candidates[0]
        tools = ["asset"]
        if media_kind == "photo" and chosen.medium == "photo":
            tools.append("enhance")
        return ToolPlan(subject=subject, tools=tools, asset_ids=[chosen.drive_file_id], prompt="")
    if media_kind == "photo" and subject in GENERATABLE_SUBJECTS:
        return ToolPlan(subject=subject, tools=["generate"], asset_ids=[],
                        prompt="clean studio product photograph, soft daylight")
    if media_kind == "photo":
        return ToolPlan(subject=subject, tools=["text_card"], asset_ids=[], prompt="")
    return None  # video with an empty bank → park (no video generation without references)


def select_tools(llm, post_genome: dict, candidates: list[Asset], media_kind: str,
                 allow_person: bool) -> ToolPlan | None:
    """Model-driven selection with pure validation and deterministic fallback."""
    candidate_briefs = [
        {
            "drive_file_id": c.drive_file_id,
            "subject": c.subject,
            "medium": c.medium,
            "aspect": c.aspect,
            "has_person": c.has_person,
            "description": c.description or "",
            "times_used": c.times_used,
        }
        for c in candidates
    ]
    raw = llm.complete_json(
        "toolbox_route_v1.md",
        {"genome": post_genome, "candidates": candidate_briefs, "media_kind": media_kind},
    )
    try:
        return validate_plan(raw, candidates, allow_person)
    except (PlanError, ValueError):
        subject = raw.get("subject") if isinstance(raw, dict) else None
        subject = subject or (candidates[0].subject if candidates else "assembled_blocks")
        return fallback_plan(subject, candidates, media_kind)
