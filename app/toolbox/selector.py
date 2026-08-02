"""v3 tool selection — BANK-ONLY for anything depicting the product.

The model proposes; `validate_plan` (pure) enforces the hard rules; the deterministic
fallback replaces invalid plans. There is NO generate tool in the routing vocabulary: a
product-depicting idea with no matching bank asset PARKs — it never reaches generation
(v3 Rule 3; `generate.py` is dormant behind `generate_enabled=false`).

Tools:
  asset      — a real bank photograph / clip is the base (bank-only for the product)
  enhance    — whitelisted quality pass on a real photo (image only)
  overlay    — Pillow caption/price on a still (Python renders letterforms, Rule 0)
  video_edit — the ffmpeg pipeline over raw-video/ footage (edited, never generated)
  text_card  — brand-background Pillow card; the only asset-free option, product-free
"""

from __future__ import annotations

from app.models import Asset
from app.schemas import ToolPlan

VALID_TOOLS = ("asset", "enhance", "overlay", "video_edit", "text_card")

# Every bank subject depicts the product or real people with it — all are BANK-ONLY.
PRODUCT_SUBJECTS = ("loose_blocks", "assembled_blocks", "parent_child", "child_face",
                    "classroom", "lesson_book", "lesson_pdf", "ugc")


class PlanError(RuntimeError):
    pass


def validate_plan(plan: dict, candidates: list[Asset], allow_person: bool) -> ToolPlan:
    p = ToolPlan.model_validate(plan)
    unknown = [t for t in p.tools if t not in VALID_TOOLS]
    if unknown:
        # 'generate' lands here by construction — it is not in the vocabulary.
        raise PlanError(f"unknown tool(s) {unknown}; valid: {VALID_TOOLS}")

    candidate_ids = {c.drive_file_id for c in candidates}
    bad_ids = [a for a in p.asset_ids if a not in candidate_ids]
    if bad_ids:
        raise PlanError(f"plan references asset ids not in the offered candidates: {bad_ids}")

    if ("asset" in p.tools or "video_edit" in p.tools) and not p.asset_ids:
        raise PlanError("asset/video_edit plans must consume a bank asset")

    if p.subject in PRODUCT_SUBJECTS and "text_card" not in p.tools and not p.asset_ids:
        raise PlanError(
            f"BANK-ONLY: subject {p.subject!r} depicts the product and the plan consumes "
            "no bank asset — park, never generate"
        )

    if "enhance" in p.tools and "video_edit" in p.tools:
        raise PlanError("enhance is image-only; it cannot ride a video chain")

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
        if media_kind == "video":
            return ToolPlan(subject=subject, tools=["asset", "video_edit"],
                            asset_ids=[chosen.drive_file_id], prompt="")
        return ToolPlan(subject=subject, tools=["asset", "enhance"],
                        asset_ids=[chosen.drive_file_id], prompt="")
    if media_kind == "photo" and subject not in PRODUCT_SUBJECTS:
        # Product-free idea (pure message) — the zero-asset brand card.
        return ToolPlan(subject=subject, tools=["text_card"], asset_ids=[], prompt="")
    return None  # product-depicting with an empty bank, or video with no footage → PARK


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
