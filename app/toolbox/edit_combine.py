"""§7.1 / §7.1b EDIT / COMBINE — images via FLUX Kontext, video via the configured family,
plus plain-Python framing (Pillow) and clipping (ffmpeg).

Image routing is strict on input count — `image_url` is SINGULAR on kontext and
`image_urls` is an ARRAY on kontext/multi; sending an array to the singular endpoint fails:
    0 images  → kontext/text-to-image   {prompt}
    1 image   → kontext                 {prompt, image_url}
    2+ images → kontext/multi           {prompt, image_urls}

Video routing mirrors it, endpoints and PROMPT REFERENCE SYNTAX both read from
config.video_family so they can never drift apart:
    0 videos   → text_to_video          {prompt, aspect_ratio, duration}
    1–3 videos → reference_to_video     {prompt, video_urls}
Source clips are ffmpeg-trimmed to the family's accepted window BEFORE submission, and
video inputs are always public (fal-storage) URLs — never Drive links.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

import ffmpeg
from PIL import Image


class EditCombineError(RuntimeError):
    pass


# -- image routing -----------------------------------------------------------------------

def route_kontext(image_count: int) -> tuple[str, str | None]:
    """(endpoint config key, payload key for the image input)."""
    if image_count < 0:
        raise EditCombineError("negative image count")
    if image_count == 0:
        return "kontext_t2i", None
    if image_count == 1:
        return "kontext_single", "image_url"
    return "kontext_multi", "image_urls"


def build_kontext_args(prompt: str, image_urls: list[str]) -> tuple[str, dict]:
    key, payload_key = route_kontext(len(image_urls))
    args: dict[str, Any] = {"prompt": prompt}
    if payload_key == "image_url":
        args["image_url"] = image_urls[0]
    elif payload_key == "image_urls":
        args["image_urls"] = list(image_urls)
    return key, args


def edit_images(fal, endpoints_cfg: dict, prompt: str, image_urls: list[str]) -> str:
    """Run the routed Kontext edit; returns a local path to the result."""
    key, args = build_kontext_args(prompt, image_urls)
    result = fal.run(endpoints_cfg[key], args)
    images = result.get("images") or ([result["image"]] if result.get("image") else [])
    if not images:
        raise EditCombineError("kontext returned no images")
    return fal.fetch(images[0]["url"], ".png")


# -- video routing -----------------------------------------------------------------------

def route_video(video_count: int, family: dict) -> str:
    if video_count == 0:
        return family["text_to_video"]
    if 1 <= video_count <= int(family.get("max_ref_videos", 3)):
        return family["reference_to_video"]
    raise EditCombineError(
        f"{video_count} reference videos exceeds the family limit of {family.get('max_ref_videos', 3)}"
    )


def reference_tokens(family: dict, n_videos: int, n_images: int = 0) -> list[str]:
    """Family-specific prompt reference syntax — bracket ([Video1]) vs at (@Video1)."""
    syntax = family.get("reference_syntax", "bracket")
    if syntax == "bracket":
        return [f"[Image{i + 1}]" for i in range(n_images)] + [f"[Video{i + 1}]" for i in range(n_videos)]
    if syntax == "at":
        return [f"@Image{i + 1}" for i in range(n_images)] + [f"@Video{i + 1}" for i in range(n_videos)]
    raise EditCombineError(f"unknown reference_syntax {syntax!r} in config.video_family")


def build_video_args(
    family: dict,
    prompt: str,
    video_urls: list[str],
    duration_s: int,
    aspect_ratio: str = "9:16",
    resolution: str = "720p",
) -> tuple[str, dict]:
    """Endpoint + arguments; duration and aspect come from config, never model defaults."""
    lo, hi = family.get("duration_range_s", [4, 15])
    duration_s = max(int(lo), min(int(hi), int(duration_s)))
    endpoint = route_video(len(video_urls), family)
    tokens = reference_tokens(family, n_videos=len(video_urls))
    for t in tokens:
        if t not in prompt:
            prompt = f"{prompt} {t}".strip()
    args: dict[str, Any] = {
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "duration": duration_s,
        "resolution": resolution,
    }
    if video_urls:
        args["video_urls"] = list(video_urls)
    return endpoint, args


def edit_video(fal, family: dict, prompt: str, video_urls: list[str], duration_s: int,
               aspect_ratio: str = "9:16", resolution: str = "720p") -> str:
    endpoint, args = build_video_args(family, prompt, video_urls, duration_s, aspect_ratio, resolution)
    result = fal.run(endpoint, args, timeout_s=900)
    video = result.get("video") or {}
    url = video.get("url") if isinstance(video, dict) else None
    if not url:
        raise EditCombineError("video endpoint returned no video url")
    return fal.fetch(url, ".mp4")


# -- plain-Python framing ----------------------------------------------------------------

def probe_duration_s(path: str) -> float | None:
    try:
        meta = ffmpeg.probe(path)
        return float(meta["format"]["duration"])
    except Exception:
        return None


def trim_clip(src: str, max_s: float) -> str:
    """Trim a clip to the accepted window before submission. A 40-second bank clip would be
    rejected or silently truncated by the endpoint — trim locally instead."""
    duration = probe_duration_s(src)
    if duration is not None and duration <= max_s:
        return src
    fd, dst = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    ffmpeg.input(src).output(dst, t=max_s, c="copy").overwrite_output().run(quiet=True)
    return dst


def fit_image_aspect(src: str, aspect: str) -> str:
    """Center-crop + resize a still to the channel's canvas."""
    sizes = {"vertical": (1080, 1920), "square": (1080, 1080), "landscape": (1920, 1080)}
    if aspect not in sizes:
        raise EditCombineError(f"unknown aspect {aspect!r}")
    tw, th = sizes[aspect]
    with Image.open(src) as im:
        im = im.convert("RGB")
        sw, sh = im.size
        scale = max(tw / sw, th / sh)
        im = im.resize((round(sw * scale), round(sh * scale)), Image.LANCZOS)
        left = (im.width - tw) // 2
        top = (im.height - th) // 2
        im = im.crop((left, top, left + tw, top + th))
        fd, dst = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        im.save(dst, "JPEG", quality=92)
    return dst


def frame_video_aspect(src: str, aspect_ratio: str = "9:16") -> str:
    """Crop-scale a video to the channel aspect with ffmpeg."""
    targets = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}
    if aspect_ratio not in targets:
        raise EditCombineError(f"unknown video aspect ratio {aspect_ratio!r}")
    tw, th = targets[aspect_ratio]
    fd, dst = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    (
        ffmpeg.input(src)
        .filter("scale", tw, th, force_original_aspect_ratio="increase")
        .filter("crop", tw, th)
        .output(dst, **{"c:a": "copy"})
        .overwrite_output()
        .run(quiet=True)
    )
    return dst
