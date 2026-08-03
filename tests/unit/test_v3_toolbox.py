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
    # v4 amends this scan in ONE controlled way: app/toolbox/pricing.py is the price
    # registry and is REQUIRED to name the removed image endpoints so a future re-enable
    # arrives already priced (§7·C5). Everywhere else the ban stands, and the compensating
    # guarantee — that those endpoints are uncallable — is asserted below.
    PRICE_REGISTRY = "pricing.py"
    offenders = []
    for path in (repo_root / "app").rglob("*.py"):
        if path.name == PRICE_REGISTRY:
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        for banned in BANNED_SUBSTRINGS:
            if banned in content:
                offenders.append(f"{path}: {banned}")
    config_dump = json.dumps(OPERATOR_CONSTANTS, default=str)
    for banned in BANNED_SUBSTRINGS:
        if banned in config_dump:
            offenders.append(f"OPERATOR_CONSTANTS: {banned}")
    assert not offenders, f"removed endpoints resurfaced: {offenders}"


def test_generative_video_appears_nowhere_at_all(repo_root):
    # Generative VIDEO has no pricing exemption — it must not exist anywhere, including the
    # price registry. Only the removed IMAGE endpoints are priced-but-inactive.
    video_banned = ("text-to-" + "video", "reference-to-" + "video", "seed" + "ance")
    offenders = []
    for path in (repo_root / "app").rglob("*.py"):
        content = path.read_text(encoding="utf-8", errors="ignore")
        for banned in video_banned:
            if banned in content:
                offenders.append(f"{path}: {banned}")
    assert not offenders, f"generative video resurfaced: {offenders}"


def test_priced_but_removed_endpoints_are_inactive():
    # The security property is "uncallable", not "unpriced". Only the upscaler is active.
    from app.toolbox.pricing import SEED_PRICES

    active = {p["endpoint"] for p in SEED_PRICES if p["active"]}
    assert active == {"fal-ai/clarity-upscaler"}
    for p in SEED_PRICES:
        if p["endpoint"] != "fal-ai/clarity-upscaler":
            assert p["active"] is False, f"{p['endpoint']} must stay uncallable"


# ---- Rule 4: the budget (acceptance 8, 9) ----------------------------------------------

UPSCALER = "fal-ai/clarity-upscaler"


def _budget(session, run_cap=250, ceiling=50, max_mp=4.0, log=None):
    from app.toolbox.pricing import seed_prices

    seed_prices(session)
    return RenderBudget(session, run_cap, ceiling, max_output_megapixels=max_mp,
                        log=log or (lambda *_: None))


def test_cost_estimation_branches_on_billing_unit(session):
    # v4 acceptance 28 — the worked checks from the price contract. A flat per-call figure
    # cannot express these: cost scales with requested output resolution.
    from app.models import EndpointPrice
    from app.toolbox.pricing import estimate_micros, micros_to_cents, seed_prices

    seed_prices(session)
    # per_megapixel: 2048x2048 = 4.194 MP x $0.030 = $0.1258 ≈ 12.6¢
    est = estimate_micros(session, UPSCALER, width=2048, height=2048)
    assert round(micros_to_cents(est), 1) == 12.6
    # 1080x1920 = 2.0736 MP x $0.030 = $0.0622
    est = estimate_micros(session, UPSCALER, width=1080, height=1920)
    assert est == 62_208 and round(micros_to_cents(est), 2) == 6.22
    # 4K: 3840x2160 = 8.2944 MP x $0.030 = $0.2488
    est = estimate_micros(session, UPSCALER, width=3840, height=2160)
    assert round(micros_to_cents(est), 2) == 24.88

    # per_image ignores dimensions entirely.
    session.get(EndpointPrice, "fal-ai/flux-pro/kontext").active = True
    session.flush()
    flat = estimate_micros(session, "fal-ai/flux-pro/kontext", width=4096, height=4096)
    assert flat == 40_000 == estimate_micros(session, "fal-ai/flux-pro/kontext",
                                             width=64, height=64)


def test_output_larger_than_max_megapixels_refused_before_the_call(session):
    # v4 acceptance 29 — a per-megapixel endpoint can never be handed a resolution that
    # prices past the ceiling, because the size guard fires first.
    from app.toolbox.pricing import OutputTooLarge

    budget = _budget(session, max_mp=4.0)
    fal = FakeFal()
    with pytest.raises(OutputTooLarge):
        GuardedFal(fal, budget).run(UPSCALER, {}, width=3840, height=2160)  # 8.29 MP
    assert fal.calls == [] and budget.spent_micros == 0


def test_per_call_ceiling_still_refuses_at_50_cents(session):
    # v4 acceptance 30 — the ceiling was deliberately NOT raised with the run cap.
    from app.models import EndpointPrice

    budget = _budget(session, run_cap=250, ceiling=50)
    row = session.get(EndpointPrice, UPSCALER)
    row.unit = "per_image"
    row.rate_micros = 800_000  # $0.80 — a single runaway call
    session.flush()
    fal = FakeFal()
    with pytest.raises(PerCallCeilingExceeded):
        GuardedFal(fal, budget).run(UPSCALER, {"prompt": ""})
    assert fal.calls == [] and budget.spent_micros == 0


def test_run_cap_refuses_at_250_and_prints_running_spend(session):
    from app.models import EndpointPrice

    lines: list[str] = []
    budget = _budget(session, run_cap=250, ceiling=50, log=lines.append)
    row = session.get(EndpointPrice, UPSCALER)
    row.unit = "per_image"
    row.rate_micros = 400_000  # 40¢ per call
    session.flush()
    gfal = GuardedFal(FakeFal(), budget)
    for _ in range(6):
        gfal.run(UPSCALER, {})                      # 240¢ total, still under 250¢
    assert round(budget.spent_cents) == 240
    assert len(lines) == 6 and all("run total" in line for line in lines)
    with pytest.raises(RunBudgetExceeded):
        gfal.run(UPSCALER, {})                      # 280¢ > 250¢ — refused
    assert round(budget.spent_cents) == 240


def test_unpriced_endpoint_is_uncallable(session):
    budget = _budget(session)
    with pytest.raises(UnknownEndpointPrice):
        GuardedFal(FakeFal(), budget).run("some/new-model", {})


def test_inactive_endpoint_is_uncallable(session):
    from app.toolbox.pricing import EndpointInactive

    budget = _budget(session)
    with pytest.raises(EndpointInactive):
        GuardedFal(FakeFal(), budget).run("fal-ai/qwen-image-2512/lora", {}, width=64, height=64)


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
    from app.models import EndpointPrice
    from app.toolbox.pricing import seed_prices

    seed_prices(session)
    row = session.get(EndpointPrice, "fal-ai/clarity-upscaler")
    row.unit = "per_image"      # flat 60¢ so the cap arithmetic is legible in the test
    row.rate_micros = 600_000
    set_config(session, "render_run_cap_cents", 100)
    set_config(session, "per_call_ceiling_cents", 90)
    set_config(session, "max_output_megapixels", 4.0)
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
