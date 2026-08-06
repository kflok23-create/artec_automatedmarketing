"""Every square photo post parked, every time — two constants never compared.

    post_1491 PARKED  OutputTooLarge: requested output 2400x2400 = 5.7600 MP exceeds
                      max_output_megapixels (4.0) — refused before the call
    post_1492 PARKED  ↑ identical
    post_1497 PARKED  ↑ identical

Not data-dependent. The pipeline always requests source × 1.5; a 1600×1600 bank photo is
always 2400×2400; that is always 5.76 MP; the ceiling is always 4.0.

THE THREE NUMBERS, reconciled rather than tuned:

  requested output        source × 1.5 — a consequence, not a setting
  max_output_megapixels   4.0, OUR OWN. §7·C5 uses it inside `estimate_micros` to PRICE a
                          call. It is a COST bound, and applying it as a hard capability
                          refusal is a category error.
  fal's real limit        32, on the RESULT, in 1024² units — AUTHORITATIVE

fal's own arithmetic, from post_1482's fuller park reason:

    "… upscale factor is 2. Which results in 77.25 (4500 * 4500 * 2 / 1024^2) MP which is
     greater than the max allowed value of 32."

(4500×2)² ÷ 1024² = 77.25. So `FAL_UPSCALER_MAX_INPUT_MP = 3.0` was wrong by an order of
magnitude — it came from the earlier message truncated mid-number, "the maximum megapixels
that can be processed is 3…", and that digit was the first of 32.

THE ACTUAL DEFECT IS THE ORDERING. `enhance` runs INSIDE the tool chain on the SOURCE asset;
`fit_image_aspect` crops to the platform canvas AFTERWARDS (render.py:119). Square is
1080×1080 = 1.17 MP. So the 5.76 MP intermediate was produced only to be thrown away —
$0.173 an image at $0.030/MP, for pixels nobody sees, tripping our own ceiling to do it.
"""

from __future__ import annotations

import pytest

from app.toolbox.edit_combine import IMAGE_ASPECTS
from app.toolbox.enhance import (
    FAL_UPSCALER_MAX_RESULT_MP,
    upscale_result_mp,
    upscale_skip_reason,
)

SCALE = 1.5


# --- THE GUARD §4 asks for: two constants, now compared by the suite -------------------

@pytest.mark.parametrize("aspect,size", sorted(IMAGE_ASPECTS.items()))
def test_every_canvas_is_within_our_own_cost_ceiling(aspect, size, session):
    """The comparison that was never made. All three canvases pass comfortably — which is
    itself what proves the 5.76 MP figure was never a deliverable.

    WHAT SUPPLIES EACH SIDE: the size table is `IMAGE_ASPECTS`; the ceiling is read from
    config. Neither is derived from the other.
    """
    from app.config import get_config

    ceiling = float(get_config(session, "max_output_megapixels", 4.0))
    w, h = size
    mp = (w * h) / 1_000_000
    assert mp <= ceiling, (
        f"{aspect} canvas {w}x{h} = {mp:.3f} MP exceeds max_output_megapixels {ceiling}")


@pytest.mark.parametrize("aspect,size", sorted(IMAGE_ASPECTS.items()))
def test_every_canvas_upscaled_is_within_FALS_limit(aspect, size):
    """The other side of the same comparison, against the authoritative number."""
    w, h = size
    assert upscale_result_mp(w, h, SCALE) <= FAL_UPSCALER_MAX_RESULT_MP


# --- fal's constraint, re-established --------------------------------------------------

def test_fals_own_arithmetic_reproduces_its_own_error():
    """If our formula does not reproduce 77.25 from fal's own numbers, we have not
    understood the constraint and any bound derived from it is a guess."""
    assert round(upscale_result_mp(4500, 4500, 2), 2) == 77.25
    assert FAL_UPSCALER_MAX_RESULT_MP == 32.0


def test_the_old_3_0_bound_would_have_blocked_legitimate_work():
    """Why the correction matters beyond tidiness. A 3000×3000 bank photo upscales to 19.31
    — well inside fal's 32 — and the old input bound of 3.0 MP would have refused it."""
    assert upscale_result_mp(3000, 3000, SCALE) < FAL_UPSCALER_MAX_RESULT_MP
    assert (3000 * 3000) / 1_000_000 > 3.0        # what the old bound measured, and rejected


def test_post_1482_is_still_refused_and_for_the_right_reason():
    """The genuine oversized source, caught BEFORE the call rather than after paying."""
    assert upscale_skip_reason(4500, 4500, target=IMAGE_ASPECTS["square"],
                               scale=SCALE) is not None


# --- the actual fix: nothing to gain ---------------------------------------------------

def test_a_source_at_or_above_the_canvas_skips_the_upscale():
    """THE THREE PARKED POSTS. 1600×1600 source, 1080×1080 canvas — the upscale output is
    cropped away immediately, so producing it is pure spend."""
    reason = upscale_skip_reason(1600, 1600, target=IMAGE_ASPECTS["square"], scale=SCALE)
    assert reason is not None
    assert "cropped away" in reason
    assert "Nothing to gain" in reason


def test_a_source_SMALLER_than_the_canvas_still_upscales():
    """The exclusion must not swallow the case ENHANCE exists for: a low-res bank photo the
    crop would otherwise stretch."""
    assert upscale_skip_reason(600, 600, target=IMAGE_ASPECTS["square"], scale=SCALE) is None


def test_a_source_short_on_ONE_axis_still_upscales():
    """`>=` on both axes, not on area — a 2000×600 source is wider than the square canvas
    and shorter than it, and the short axis is the one the crop would stretch."""
    assert upscale_skip_reason(2000, 600, target=IMAGE_ASPECTS["square"], scale=SCALE) is None


def test_with_no_target_only_fals_limit_applies():
    """Callers that do not know the canvas still get the provider bound — the two conditions
    are independent facts about different parties."""
    assert upscale_skip_reason(1600, 1600, target=None, scale=SCALE) is None
    assert upscale_skip_reason(5000, 5000, target=None, scale=SCALE) is not None
