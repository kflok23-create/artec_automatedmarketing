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
                  local_path: str, medium: str) -> str:
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
    scale = build_enhance_args("", "photo")["scale"]
    out_w, out_h = int(round(in_w * scale)), int(round(in_h * scale))

    public_url = fal.upload_public(local_path)
    args = build_enhance_args(public_url, medium)
    result = fal.run(model_endpoints["upscaler"], args, width=out_w, height=out_h)
    images = result.get("images") or ([result["image"]] if result.get("image") else [])
    if not images:
        raise EnhanceError("upscaler returned no image")
    return fal.fetch(images[0]["url"], ".png")
