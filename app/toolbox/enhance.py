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
FAL_UPSCALER_MAX_INPUT_MP = 3.0


def upscale_skip_reason(width: int, height: int,
                        limit_mp: float = FAL_UPSCALER_MAX_INPUT_MP) -> str | None:
    """Why the upscale must be skipped for this input, or None.

    SKIP, NOT FAIL, and not downscale. The upscaler is a SHARPENING pass on a real product
    photo — `creativity: 0.1`, `resemblance: 0.9`, never invent block geometry. An image
    already above 3 MP does not need it; the original is the better artefact. Shrinking a
    good photo so it can be enlarged again would spend money to lose detail.

    Failing instead would PARK every photo post whose source came off a real camera, which
    on 2026-08-09 is the whole photo lane.
    """
    mp = (width * height) / 1_000_000
    if mp <= limit_mp:
        return None
    return (f"input is {mp:.2f} MP and fal's upscaler accepts at most {limit_mp} MP — "
            f"skipping ENHANCE and using the original, which at this resolution needs no "
            f"upscale. Not a failure: the source is already better than the pass would "
            f"return.")


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
                  local_path: str, medium: str, log=print) -> str:
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
    skip = upscale_skip_reason(in_w, in_h)
    if skip:
        log(f"ENHANCE upscale skipped: {skip}")
        return local_path
    scale = build_enhance_args("", "photo")["scale"]
    out_w, out_h = int(round(in_w * scale)), int(round(in_h * scale))

    public_url = fal.upload_public(local_path)
    args = build_enhance_args(public_url, medium)
    result = fal.run(model_endpoints["upscaler"], args, width=out_w, height=out_h)
    images = result.get("images") or ([result["image"]] if result.get("image") else [])
    if not images:
        raise EnhanceError("upscaler returned no image")
    return fal.fetch(images[0]["url"], ".png")
