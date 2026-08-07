"""v3 ENHANCE — the last model in the toolbox, on a hard whitelist.

Permitted only on real photographs of the real product, image only, no text involved.
Whitelisted operations (config `enhance_whitelist`) and NOTHING else is callable:
  upscale       → fal clarity-upscaler ×1.5, creativity LOW (the one surviving model call)
  color_correct → Pillow, zero model calls
  autocontrast  → Pillow, zero model calls
"""

from __future__ import annotations

import os
import tempfile

from PIL import Image, ImageEnhance, ImageOps


class EnhanceError(RuntimeError):
    pass


class EnhanceMediumError(EnhanceError):
    """Raised when ENHANCE is handed a non-image asset."""


class EnhanceNotWhitelisted(EnhanceError):
    """The operation is off the whitelist — deliberately not callable."""


KNOWN_OPS = ("upscale", "color_correct", "autocontrast")

# ---------------------------------------------------------------------------------------
# THE PROVIDER'S OWN LIMIT, ON THE INPUT. Read from fal's refusal, verbatim:
#
#   FalClientHTTPError: [{'loc': ['body', 'image_url'], 'msg': 'The image is too large to
#   upscale. Please try a smaller image or a smaller upscale factor. The maximum megapixels
#   that can be processed is 3…'}]
#
# `max_output_megapixels: 4.0` did not and could not catch this. Two reasons, and both
# matter:
#   1. IT MEASURES THE WRONG QUANTITY. It bounds the requested OUTPUT, inside
#      `estimate_micros`, for costing. fal is refusing on the INPUT.
#   2. IT IS LOOSER THAN THE REAL LIMIT. 4.0 > 3, so even applied to the right quantity it
#      would pass images fal rejects. A ceiling above the provider's ceiling cannot protect.
#
# A modern phone photo is ~12 MP, so this is not an edge case: it is every real bank photo.
# The input dimensions were ALREADY being read two lines before the call (to compute output
# size for the budget) and simply never compared against anything.
#
# Named as a constant, and READ — the max_title lesson: a limit that is declared and never
# consulted is not a rule, it is a comment that looks like enforcement.
# fal's ACTUAL constraint, re-established from its own arithmetic. post_1482's park reason
# carried the fuller sentence the earlier one truncated:
#
#   "… upscale factor is 2. Which results in 77.25 (4500 * 4500 * 2 / 1024^2) MP which is
#    greater than the max allowed value of 32."
#
# 4500 × 4500 with factor 2 gives 9000 × 9000 = 81,000,000 px; ÷ 1024² = 77.25. So fal
# measures the RESULT, in 1024²-pixel units, and its ceiling is 32.
#
# `FAL_UPSCALER_MAX_INPUT_MP = 3.0` WAS WRONG BY AN ORDER OF MAGNITUDE. It came from a
# message truncated mid-number — "the maximum megapixels that can be processed is 3…" — and
# the digit was the first of 32. An input bound set from a truncated sentence is a guess
# wearing a constant's clothes, and this one would have refused a 3000×3000 bank photo whose
# ×1.5 result is 19.3, comfortably inside fal's real limit.
FAL_UPSCALER_MAX_RESULT_MP = 32.0
FAL_MP_DIVISOR = 1024 * 1024        # fal counts in 1024² units, not 10⁶


def upscale_result_mp(width: int, height: int, scale: float) -> float:
    """The figure fal itself computes and compares against 32."""
    return (width * scale) * (height * scale) / FAL_MP_DIVISOR


def upscale_skip_reason(width: int, height: int, target: tuple[int, int] | None = None,
                        scale: float = 1.5,
                        limit_mp: float = FAL_UPSCALER_MAX_RESULT_MP) -> str | None:
    """Why the upscale must be skipped for this input, or None.

    TWO REASONS, and the first is the one that parked every square photo post.

    1. NOTHING TO GAIN. ENHANCE runs INSIDE the tool chain, on the SOURCE asset, and
       `fit_image_aspect` crops to the platform canvas AFTERWARDS (render.py:119). Square is
       1080×1080 = 1.17 MP. A 1600×1600 source upscaled ×1.5 is 2400×2400 = 5.76 MP — which
       is then thrown away by the crop. We were paying to enlarge pixels nobody would ever
       see, and tripping our own ceiling to do it.

       `max_output_megapixels: 4.0` is a COST bound: §7·C5 uses it inside `estimate_micros`
       to price a call before making it. Applying a costing number as a hard capability
       refusal is a category error, and it is why three posts parked with a message about
       megapixels when the real answer is that the upscale was pointless.

       The upscaler is a SHARPENING pass — creativity 0.1, resemblance 0.9, never invent
       block geometry. Its value is on a source SMALLER than the canvas, where the crop
       would otherwise stretch real detail. On a source already at or above the canvas there
       is no detail to add, so skipping is not a workaround: it is the correct behaviour, and
       it happens to cost nothing instead of $0.173 an image.

    2. fal WOULD REFUSE THE RESULT. Kept as a second, independent condition because it is a
       different fact about a different party, and one day a genuinely small source will meet
       a genuinely large scale.
    """
    if target:
        tw, th = target
        if width >= tw and height >= th:
            return (f"source is {width}x{height} and the {tw}x{th} canvas is smaller — the "
                    f"upscale would be cropped away immediately. Nothing to gain, so nothing "
                    f"spent: ENHANCE skipped and the original used.")
    result = upscale_result_mp(width, height, scale)
    if result > limit_mp:
        return (f"{width}x{height} at scale {scale} results in {result:.2f} MP and fal's "
                f"maximum is {limit_mp} (it measures the RESULT, in 1024² units). Skipping "
                f"ENHANCE and using the original rather than paying for a refusal.")
    return None


def build_enhance_args(image_url: str, medium: str) -> dict:
    if medium != "photo":
        raise EnhanceMediumError(
            f"ENHANCE is image-only; got medium={medium!r}. There is no video quality pass."
        )
    return {
        "image_url": image_url,
        "scale": 1.5,
        "creativity": 0.1,   # sharpen a real product photo, never invent block geometry
        "resemblance": 0.9,
    }


def _pillow_out(im: Image.Image) -> str:
    fd, dst = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    im.convert("RGB").save(dst, "JPEG", quality=94)
    return dst


def enhance_image(fal, model_endpoints: dict, whitelist: list[str], op: str,
                  local_path: str, medium: str, log=print,
                  target: tuple[int, int] | None = None) -> str:
    """Run one whitelisted enhancement on a bank photograph; returns a local path."""
    if op not in KNOWN_OPS or op not in whitelist:
        raise EnhanceNotWhitelisted(
            f"enhancement {op!r} is not on the whitelist {sorted(whitelist)} — off-whitelist "
            "operations are not callable"
        )
    if medium != "photo":
        raise EnhanceMediumError(f"ENHANCE is image-only; got medium={medium!r}")

    if op == "color_correct":
        with Image.open(local_path) as im:
            im = ImageEnhance.Color(im.convert("RGB")).enhance(1.08)
            im = ImageEnhance.Contrast(im).enhance(1.05)
            return _pillow_out(im)
    if op == "autocontrast":
        with Image.open(local_path) as im:
            return _pillow_out(ImageOps.autocontrast(im.convert("RGB"), cutoff=1))

    # upscale — the one surviving model call, priced and budgeted by GuardedFal.
    # The upscaler bills PER MEGAPIXEL, so the budget needs the OUTPUT dimensions before the
    # call: clarity scales by `scale` (1.5), so output = input × 1.5 on each axis.
    with Image.open(local_path) as probe:
        in_w, in_h = probe.size
    # The dimensions were already here. Nothing compared them to the provider's limit.
    scale = build_enhance_args("", "photo")["scale"]
    skip = upscale_skip_reason(in_w, in_h, target=target, scale=scale)
    if skip:
        log(f"ENHANCE upscale skipped: {skip}")
        return local_path
    out_w, out_h = int(round(in_w * scale)), int(round(in_h * scale))

    public_url = fal.upload_public(local_path)
    args = build_enhance_args(public_url, medium)
    result = fal.run(model_endpoints["upscaler"], args, width=out_w, height=out_h)
    images = result.get("images") or ([result["image"]] if result.get("image") else [])
    if not images:
        raise EnhanceError("upscaler returned no image")
    return fal.fetch(images[0]["url"], ".png")
