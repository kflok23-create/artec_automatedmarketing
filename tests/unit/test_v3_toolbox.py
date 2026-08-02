"""v3 acceptance 4 (text guard), 5 (endpoint repo scan), 8/9 (budget), 11 (bank-only)."""

import json

import pytest

from app.config import OPERATOR_CONSTANTS
from app.integrations.fakes import FakeDrive, FakeFal, FakeLLM
from app.models import Asset, Post
from app.stages.render import render
from app.toolbox.budget import (
    GuardedFal,
    PerCallCeilingExceeded,
    RenderBudget,
    RunBudgetExceeded,
    UnknownEndpointPrice,
)
from app.toolbox.text_guard import TextRenderForbidden, assert_no_text_render

# ---- Rule 0: no model renders text -----------------------------------------------------

BAD_PROMPTS = [
    'product shot with "Robotics class 1 term $15" on a banner',   # quoted string
    "blocks arranged next to $15 signage",                          # price
    "photo showing RM449 on a card",                                # price (MY)
    "a poster that is 50% off",                                     # percentage
    "blocks with the caption underneath",                           # banned word
    "write the class schedule on the board",                        # banned word
    "a title card for the course",                                  # banned word
    "box that says hello",                                          # banned word
]


@pytest.mark.parametrize("prompt", BAD_PROMPTS)
def test_text_guard_raises_on_fixture(prompt):
    with pytest.raises(TextRenderForbidden):
        assert_no_text_render(prompt, "any-endpoint")


def test_text_guard_passes_clean_product_prompts():
    assert_no_text_render("assembled artec blocks crane, studio light, white background")
    assert_no_text_render("")  # promptless calls (upscaler) pass


def test_guard_is_wired_into_the_budgeted_path():
    budget = RenderBudget({"fal-ai/clarity-upscaler": 4}, 100, 50, log=lambda *_: None)
    gfal = GuardedFal(FakeFal(), budget)
    with pytest.raises(TextRenderForbidden):
        gfal.run("fal-ai/clarity-upscaler", {"prompt": 'add "SALE" to the image'})
    assert budget.spent_cents == 0  # refused before any charge


# ---- Rule 1/2 + repo scan (acceptance 5) -----------------------------------------------

# Assembled from parts so this test file cannot trip its own scan.
BANNED_SUBSTRINGS = (
    "flux-pro/kon" + "text",
    "text-to-" + "image",
    "text-to-" + "video",
    "reference-to-" + "video",
    "seed" + "ance",
)


def test_no_generative_endpoint_in_code_or_config(repo_root):
    offenders = []
    for path in (repo_root / "app").rglob("*.py"):
        content = path.read_text(encoding="utf-8", errors="ignore")
        for banned in BANNED_SUBSTRINGS:
            if banned in content:
                offenders.append(f"{path}: {banned}")
    config_dump = json.dumps(OPERATOR_CONSTANTS, default=str)
    for banned in BANNED_SUBSTRINGS:
        if banned in config_dump:
            offenders.append(f"OPERATOR_CONSTANTS: {banned}")
    price_dump = json.dumps(OPERATOR_CONSTANTS["endpoint_prices_cents"])
    for banned in BANNED_SUBSTRINGS:
        if banned in price_dump:
            offenders.append(f"price table: {banned}")
    assert not offenders, f"removed endpoints resurfaced: {offenders}"


# ---- Rule 4: the budget (acceptance 8, 9) ----------------------------------------------

def test_per_call_ceiling_refuses_before_the_call_is_made():
    # A single USD 8.00 call must be structurally impossible.
    fal = FakeFal()
    budget = RenderBudget({"expensive/video": 800}, run_cap_cents=1000,
                          per_call_ceiling_cents=50, log=lambda *_: None)
    gfal = GuardedFal(fal, budget)
    with pytest.raises(PerCallCeilingExceeded):
        gfal.run("expensive/video", {"prompt": ""})
    assert fal.calls == [] and budget.spent_cents == 0


def test_run_cap_refuses_and_prints_running_spend():
    lines: list[str] = []
    budget = RenderBudget({"fal-ai/clarity-upscaler": 40}, run_cap_cents=100,
                          per_call_ceiling_cents=50, log=lines.append)
    gfal = GuardedFal(FakeFal(), budget)
    gfal.run("fal-ai/clarity-upscaler", {})
    gfal.run("fal-ai/clarity-upscaler", {})
    assert budget.spent_cents == 80
    assert len(lines) == 2 and all("run total" in line for line in lines)  # spend printed per call
    with pytest.raises(RunBudgetExceeded):
        gfal.run("fal-ai/clarity-upscaler", {})  # 120 > 100 — refused
    assert budget.spent_cents == 80


def test_unpriced_endpoint_is_uncallable():
    budget = RenderBudget({"fal-ai/clarity-upscaler": 4}, 100, 50, log=lambda *_: None)
    with pytest.raises(UnknownEndpointPrice):
        GuardedFal(FakeFal(), budget).run("some/new-model", {})


def _approved(session, pid, channel="instagram"):
    p = Post(post_id=pid, week_start=__import__("datetime").date(2026, 8, 3),
             channel=channel, status="APPROVED", angle="a", hook="Build focus",
             cta_type="discount", cta_placement="caption_end",
             tracked_url=f"https://artec.my/?code=SOCIAL50&utm_source={channel}&utm_medium=organic&utm_campaign={pid}")
    session.add(p)
    session.flush()
    return p


def test_run_over_cap_parks_the_remainder_with_wishlist(session):
    # acceptance 8, end to end: two photo posts, budget only covers the first upscale.
    from app.config import set_config

    session.add(Asset(drive_file_id="ph1", drive_path="raw-photo/assembled/a.jpg",
                      medium="photo", subject="assembled_blocks", has_person=False,
                      aspect="square", status="active"))
    p1 = _approved(session, "post_9001")
    p2 = _approved(session, "post_9002")
    set_config(session, "endpoint_prices_cents", {"fal-ai/clarity-upscaler": 60,
                                                  "fal-ai/qwen-image-2512/lora": 3})
    set_config(session, "render_budget_cents", 100)
    set_config(session, "per_call_ceiling_cents", 90)
    lines: list[str] = []
    out = render(session, FakeLLM(), FakeDrive(), FakeFal(), all_approved=True,
                 log=lines.append)
    assert out["rendered"] == 1 and out["parked"] == 1
    assert out["spent_cents"] == 60
    assert p1.status == "RENDERED"
    assert p2.status == "PARKED"
    assert p2.asset_wishlist and p2.asset_wishlist[0]["target_folder"].startswith("raw-photo")
    assert any("run total" in line for line in lines)


def test_product_idea_with_no_bank_asset_parks_never_generates(session):
    # acceptance 11 — empty bank + product idea → PARK; the lora endpoint is never called.
    fal = FakeFal()
    post = _approved(session, "post_9003")
    out = render(session, FakeLLM(), FakeDrive(), fal, all_approved=True, log=lambda *_: None)
    assert out["parked"] == 1 and post.status == "PARKED"
    lora = OPERATOR_CONSTANTS["model_endpoints"]["lora_generate"]
    assert not any(endpoint == lora for endpoint, _ in fal.calls)
    assert fal.calls == []  # in fact: zero model calls at all
