"""v3 EDIT pipeline — Python does pixels. Pillow frames stills; ffmpeg edits video.

Video posts are EDITED, NEVER GENERATED (v3 Rules 1–2): assembled from real `raw-video/`
footage with ffmpeg only — trim, crop to platform aspect, speed adjust, drawtext overlay,
concatenate, thumbnail extract. This is the first-class pipeline, not a fallback. The
generative endpoints that used to live here (kontext, generative video) are deleted, not
flagged.

Two ffmpeg specifics enforced here because they bite otherwise:
- drawtext captions go through `textfile=` with a temp file — colons, commas, apostrophes
  and percent signs break inline filter escaping, which is where mangled overlays start.
- every video output is written with `-movflags +faststart`; without a leading moov atom
  platforms reject the upload or the video will not play (post_1485's failure mode).
"""

from __future__ import annotations

import os
import tempfile

import ffmpeg
from PIL import Image


class EditError(RuntimeError):
    pass


# Kept name for callers that predate v3.
EditCombineError = EditError

FASTSTART = {"movflags": "+faststart"}

VIDEO_ASPECTS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}
IMAGE_ASPECTS = {"vertical": (1080, 1920), "square": (1080, 1080), "landscape": (1920, 1080)}


def _tmp(suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return path


# -- stills (Pillow) ---------------------------------------------------------------------

def fit_image_aspect(src: str, aspect: str) -> str:
    """Center-crop + resize a still to the channel's canvas."""
    if aspect not in IMAGE_ASPECTS:
        raise EditError(f"unknown aspect {aspect!r}")
    tw, th = IMAGE_ASPECTS[aspect]
    with Image.open(src) as im:
        im = im.convert("RGB")
        sw, sh = im.size
        scale = max(tw / sw, th / sh)
        im = im.resize((round(sw * scale), round(sh * scale)), Image.LANCZOS)
        left = (im.width - tw) // 2
        top = (im.height - th) // 2
        im = im.crop((left, top, left + tw, top + th))
        dst = _tmp(".jpg")
        im.save(dst, "JPEG", quality=92)
    return dst


# -- video (ffmpeg only) -----------------------------------------------------------------

def probe_duration_s(path: str) -> float | None:
    try:
        meta = ffmpeg.probe(path)
        return float(meta["format"]["duration"])
    except Exception:
        return None


def trim_clip(src: str, max_s: float) -> str:
    duration = probe_duration_s(src)
    if duration is not None and duration <= max_s:
        return src
    dst = _tmp(".mp4")
    ffmpeg.input(src).output(dst, t=max_s, c="copy", **FASTSTART).overwrite_output().run(quiet=True)
    return dst


def frame_video_aspect(src: str, aspect_ratio: str = "9:16") -> str:
    if aspect_ratio not in VIDEO_ASPECTS:
        raise EditError(f"unknown video aspect ratio {aspect_ratio!r}")
    tw, th = VIDEO_ASPECTS[aspect_ratio]
    dst = _tmp(".mp4")
    (
        ffmpeg.input(src)
        .filter("scale", tw, th, force_original_aspect_ratio="increase")
        .filter("crop", tw, th)
        .output(dst, **{"c:a": "copy"}, **FASTSTART)
        .overwrite_output()
        .run(quiet=True)
    )
    return dst


def speed_video(src: str, factor: float) -> str:
    """factor 2.0 = twice as fast. Audio dropped (short social clips ride platform audio)."""
    if not 0.25 <= factor <= 4.0:
        raise EditError(f"speed factor {factor} outside [0.25, 4.0]")
    dst = _tmp(".mp4")
    (
        ffmpeg.input(src)
        .filter("setpts", f"PTS/{factor}")
        .output(dst, an=None, **FASTSTART)
        .overwrite_output()
        .run(quiet=True)
    )
    return dst


def concat_videos(paths: list[str]) -> str:
    if len(paths) < 2:
        raise EditError("concat needs at least two clips")
    list_path = _tmp(".txt")
    with open(list_path, "w", encoding="utf-8") as fh:
        for p in paths:
            fh.write(f"file '{p}'\n")
    dst = _tmp(".mp4")
    (
        ffmpeg.input(list_path, format="concat", safe=0)
        .output(dst, c="copy", **FASTSTART)
        .overwrite_output()
        .run(quiet=True)
    )
    return dst


def drawtext_overlay(src: str, caption: str, font_path: str, fontsize: int = 56,
                     position: str = "bottom") -> str:
    """Caption on video via drawtext — ALWAYS textfile=, never inline text=.
    Colons, commas, apostrophes and percent signs break drawtext's inline filter syntax;
    a temp textfile sidesteps escaping entirely."""
    textfile = _tmp(".txt")
    with open(textfile, "w", encoding="utf-8") as fh:
        fh.write(caption)
    y = "h-th-120" if position == "bottom" else "120"
    dst = _tmp(".mp4")
    (
        ffmpeg.input(src)
        .filter(
            "drawtext",
            textfile=textfile,
            fontfile=font_path,
            fontsize=fontsize,
            fontcolor="white",
            borderw=3,
            bordercolor="black",
            x="(w-tw)/2",
            y=y,
        )
        .output(dst, **{"c:a": "copy"}, **FASTSTART)
        .overwrite_output()
        .run(quiet=True)
    )
    return dst


def extract_thumbnail(src: str, at_s: float = 1.0) -> str:
    dst = _tmp(".jpg")
    ffmpeg.input(src, ss=at_s).output(dst, vframes=1).overwrite_output().run(quiet=True)
    return dst


def moov_before_mdat(path: str) -> bool:
    """True when the moov atom precedes mdat (faststart) — playable-on-upload check."""
    with open(path, "rb") as fh:
        data = fh.read(4 * 1024 * 1024)
    moov = data.find(b"moov")
    mdat = data.find(b"mdat")
    if moov == -1:
        return False
    return mdat == -1 or moov < mdat


def edit_video_pipeline(src: str, *, duration_s: float, aspect_ratio: str,
                        caption: str | None = None, font_path: str | None = None) -> str:
    """The first-class v3 video path: real footage in, platform-ready mp4 out, zero model
    calls. trim → crop to aspect → optional drawtext caption → faststart output."""
    out = trim_clip(src, max_s=duration_s)
    out = frame_video_aspect(out, aspect_ratio=aspect_ratio)
    if caption:
        if not font_path:
            raise EditError("caption overlay requires a brand font path")
        out = drawtext_overlay(out, caption, font_path)
    return out
