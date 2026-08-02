"""§7.3 ENHANCE — fal-ai/clarity-upscaler, scale 1.5, creativity LOW.

This is a real product: hallucinated block geometry is a defect, not a style choice, so
creativity stays low and resemblance high. IMAGE ONLY — the guard raises on any other
medium (Clarity is SD1.5-based and cannot process video).
"""

from __future__ import annotations


class EnhanceError(RuntimeError):
    pass


class EnhanceMediumError(EnhanceError):
    """Raised when ENHANCE is handed a non-image asset."""


def build_enhance_args(image_url: str, medium: str) -> dict:
    if medium != "photo":
        raise EnhanceMediumError(
            f"ENHANCE is image-only; got medium={medium!r}. Video quality passes are out of "
            "the locked toolset."
        )
    return {
        "image_url": image_url,
        "scale": 1.5,
        "creativity": 0.1,   # sharpen, don't invent block studs
        "resemblance": 0.9,
    }


def enhance_image(fal, endpoints_cfg: dict, image_url: str, medium: str) -> str:
    args = build_enhance_args(image_url, medium)
    result = fal.run(endpoints_cfg["upscaler"], args)
    images = result.get("images") or ([result["image"]] if result.get("image") else [])
    if not images:
        raise EnhanceError("upscaler returned no image")
    return fal.fetch(images[0]["url"], ".png")
