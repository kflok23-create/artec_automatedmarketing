"""fal refuses an upscale input over 3 MP, and our only ceiling measured the wrong thing.

From production, verbatim:

    FalClientHTTPError: [{'loc': ['body', 'image_url'], 'msg': 'The image is too large to
    upscale. Please try a smaller image or a smaller upscale factor. The maximum megapixels
    that can be processed is 3…'}]

`max_output_megapixels: 4.0` did not catch it and could not have:

  1. IT MEASURES THE WRONG QUANTITY. It bounds the requested OUTPUT, inside
     `estimate_micros`, for costing. fal refuses on the INPUT.
  2. IT IS LOOSER THAN THE REAL LIMIT. 4.0 > 3, so even applied to the right quantity it
     would pass images fal rejects. A ceiling above the provider's ceiling cannot protect.

A modern phone photo is ~12 MP. This is not an edge case — it is every real bank photo, and
on 2026-08-09 the photo lane is five of the nine drafts.

WHAT SUPPLIES EACH SIDE: the input dimensions come from Pillow reading the actual file
(they were already being read to compute output size for the budget, and were compared
against nothing); the limit is fal's own documented figure. Neither is supplied by the
caller.
"""

from __future__ import annotations

import pytest

from app.toolbox.enhance import FAL_UPSCALER_MAX_INPUT_MP, upscale_skip_reason


def test_the_bound_is_the_providers_not_ours():
    """3, from fal's refusal — not 4.0, which is our costing ceiling for a different
    quantity. A guard calibrated above the thing it guards against is decorative."""
    assert FAL_UPSCALER_MAX_INPUT_MP == 3.0


@pytest.mark.parametrize("w,h,mp", [
    (4032, 3024, 12.19),      # an ordinary phone photo
    (3000, 2000, 6.0),
    (2000, 1600, 3.2),        # only just over — the band our 4.0 ceiling would have passed
])
def test_an_input_over_the_limit_is_skipped(w, h, mp):
    reason = upscale_skip_reason(w, h)
    assert reason is not None
    assert f"{mp:.2f} MP" in reason
    assert "at most 3.0 MP" in reason


@pytest.mark.parametrize("w,h", [
    (1080, 1920),             # 2.07 MP — the spine's vertical format
    (1000, 1000),
    (1732, 1732),             # 3.00 MP exactly — the boundary is inclusive
])
def test_an_input_within_the_limit_still_upscales(w, h):
    assert upscale_skip_reason(w, h) is None


def test_the_3_to_4_MP_band_is_exactly_what_the_old_ceiling_missed():
    """The precise gap. 3.2 MP input passes `max_output_megapixels: 4.0` and fal rejects it.
    Asserting the band explicitly so nobody 'simplifies' the two numbers into one."""
    assert upscale_skip_reason(2000, 1600) is not None       # 3.2 MP — fal refuses
    assert 3.2 < 4.0                                          # ...and our ceiling allowed it


def test_it_skips_rather_than_failing():
    """SKIP, not park. The upscaler is a SHARPENING pass (creativity 0.1, resemblance 0.9);
    an image already above 3 MP does not need it and the original is the better artefact.
    Failing would PARK every photo post whose source came off a real camera."""
    reason = upscale_skip_reason(4032, 3024)
    assert "using the original" in reason
    assert "Not a failure" in reason


def test_it_does_not_downscale_to_squeeze_under_the_limit():
    """Shrinking a good photo so it can be enlarged again would spend money to lose detail.
    The function returns a REASON, never dimensions — there is no resize path to take."""
    assert isinstance(upscale_skip_reason(4032, 3024), str)
    assert upscale_skip_reason(1080, 1920) is None
