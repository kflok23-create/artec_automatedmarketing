"""v4 §5.3 — publish pre-flight. Mandatory on every asset, blocks on failure.

Everything except email and video auto-publishes, so the machine must look before it does.
A failure PARKs the post with a wishlist entry and reports it in the digest; it is never
shown to a human and never published.

This is also gate 1 for video (§5.1): a video that fails here is parked BEFORE anyone is
asked to review it, so the operator's time is only ever spent on files that are at least
mechanically sound.

Failure class: packaging/environment. `ffprobe` must exist in the DEPLOYED container —
nixpacks installs the `ffmpeg` package which bundles it, and `artec doctor` now asserts
ffprobe specifically rather than inferring it from ffmpeg.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field

from PIL import Image

from app.toolbox.edit_combine import moov_before_mdat

# Sane bands. A file outside these is not necessarily broken, but it is not something we
# publish unattended without a human having looked.
MIN_BYTES_IMAGE = 1_000
MAX_BYTES_IMAGE = 25_000_000
MAX_BYTES_VIDEO = 300_000_000

# An ABSOLUTE byte floor for video was withdrawn — see DECISIONS.md. It had been tuned down
# to 2 KB to accommodate a synthetic solid-colour fixture, which is near-zero-entropy and
# nothing like real footage; in doing so it stopped guarding what it exists to guard (a
# truncated render landing at 12 KB sailed through). The fixture had reshaped the spec.
#
# The replacement scales with what the clip actually is: bits per pixel-second,
#     (size_bytes * 8) / (width * height * duration_s)
# which is resolution- and duration-independent. Reference points measured on real encodes:
#     real 1080x1920 social H.264 @ ~3 Mbps ... ~1.45   bits/pixel-second
#     synthetic solid colour               ... ~0.010  bits/pixel-second
#     truncated / failed render            ... <0.005  bits/pixel-second
# 0.05 sits ~29x below real footage and ~5x above a degenerate encode. A synthetic
# solid-colour clip FAILS this deliberately: fixtures must look like the thing being
# guarded, so at least one video fixture is a realistic high-entropy encode.
MIN_BITS_PER_PIXEL_SECOND = 0.05

ASPECT_TOLERANCE = 0.04  # 4% — JPEG/H.264 dimension rounding, not a licence to drift

TARGET_ASPECT_RATIO = {"vertical": 1080 / 1920, "square": 1.0, "landscape": 1920 / 1080}


@dataclass
class PreflightResult:
    ok: bool
    checks: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def fail(self, message: str) -> None:
        self.ok = False
        self.failures.append(message)

    def passed(self, message: str) -> None:
        self.checks.append(message)

    @property
    def summary(self) -> str:
        return "; ".join(self.failures) if self.failures else "all pre-flight checks passed"


class PreflightError(RuntimeError):
    pass


def ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


def _probe(path: str) -> dict:
    """Raw ffprobe JSON. Uses the binary directly rather than ffmpeg-python so a missing
    ffprobe is a named error instead of an opaque one."""
    import json

    if not ffprobe_available():
        raise PreflightError(
            "ffprobe is not on PATH in this container — video cannot be verified before "
            "publishing; nixpacks must install ffmpeg (which bundles ffprobe)"
        )
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
         path],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if proc.returncode != 0:
        raise PreflightError(f"ffprobe could not read the file: {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout or "{}")


def check_caption(stored_caption: str | None, rendered_caption: str | None) -> str | None:
    """Closes the truncation class that produced 'gives yo'. The caption that goes out must
    be byte-identical in LENGTH to what was stored — a silent truncation anywhere between
    render and publish is caught here."""
    stored = stored_caption or ""
    rendered = rendered_caption if rendered_caption is not None else stored
    if len(rendered) != len(stored):
        return (f"caption length changed between render and publish: stored "
                f"{len(stored)} chars, about to publish {len(rendered)} — refusing "
                "(this is the truncation class that shipped 'gives yo')")
    return None


def preflight_image(path: str, aspect: str) -> PreflightResult:
    result = PreflightResult(ok=True)
    size = os.path.getsize(path)
    if size < MIN_BYTES_IMAGE:
        result.fail(f"image is only {size} bytes — implausibly small")
    elif size > MAX_BYTES_IMAGE:
        result.fail(f"image is {size} bytes — implausibly large")
    else:
        result.passed(f"size {size} bytes")

    try:
        with Image.open(path) as im:
            im.verify()          # header integrity only — verify() does NOT decode pixels
        with Image.open(path) as im:
            width, height = im.size
            # A truncated JPEG passes verify(); only a full decode catches it. PIL is
            # lenient by default, so force the load and let it raise.
            im.load()
    except Exception as e:
        result.fail(f"image does not decode: {type(e).__name__}: {e}")
        return result
    result.passed(f"decodes cleanly at {width}x{height}")

    target = TARGET_ASPECT_RATIO.get(aspect)
    if target is None:
        result.fail(f"unknown target aspect {aspect!r}")
    else:
        actual = width / height
        if abs(actual - target) / target > ASPECT_TOLERANCE:
            result.fail(f"aspect {actual:.3f} does not match target {aspect} ({target:.3f})")
        else:
            result.passed(f"aspect matches {aspect}")
    return result


def preflight_video(path: str, *, aspect_ratio: str = "9:16",
                    duration_bounds: tuple[float, float] = (1.0, 600.0)) -> PreflightResult:
    result = PreflightResult(ok=True)

    size = os.path.getsize(path)
    if size > MAX_BYTES_VIDEO:
        result.fail(f"video is {size} bytes — implausibly large")
    # The lower bound is a BITRATE check, applied below once ffprobe has given us the
    # dimensions and duration it needs. An absolute byte floor cannot tell a legitimate
    # short clip from a truncated long one.

    # The failure that shipped post_1485: a trailing moov atom means platforms reject the
    # upload or the video will not play.
    if not moov_before_mdat(path):
        result.fail("moov atom is not leading (no faststart) — platforms will reject this "
                    "or it will not play")
    else:
        result.passed("moov atom leads (faststart)")

    try:
        meta = _probe(path)
    except PreflightError as e:
        result.fail(str(e))
        return result
    result.passed("ffprobe reads the file")

    video_streams = [s for s in meta.get("streams", []) if s.get("codec_type") == "video"]
    if not video_streams:
        result.fail("no video stream present")
        return result
    result.passed(f"{len(video_streams)} video stream(s)")

    stream = video_streams[0]
    try:
        duration = float(meta.get("format", {}).get("duration")
                         or stream.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        result.fail("video stream has zero duration")
    else:
        lo, hi = duration_bounds
        if not (lo <= duration <= hi):
            result.fail(f"duration {duration:.1f}s outside the platform bound [{lo}, {hi}]s")
        else:
            result.passed(f"duration {duration:.1f}s within [{lo}, {hi}]s")

    width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    if width <= 0 or height <= 0:
        result.fail("video stream reports no dimensions")
    else:
        try:
            tw, th = (int(x) for x in aspect_ratio.split(":"))
            target = tw / th
        except Exception:
            result.fail(f"unknown target aspect ratio {aspect_ratio!r}")
            return result
        actual = width / height
        if abs(actual - target) / target > ASPECT_TOLERANCE:
            result.fail(f"aspect {width}x{height} ({actual:.3f}) does not match target "
                        f"{aspect_ratio} ({target:.3f})")
        else:
            result.passed(f"aspect {width}x{height} matches {aspect_ratio}")

        # Bitrate floor — scales with resolution and duration, so it catches a truncated
        # render at any length instead of only very small files.
        if duration > 0:
            bpps = (size * 8) / (width * height * duration)
            if bpps < MIN_BITS_PER_PIXEL_SECOND:
                result.fail(
                    f"bitrate {bpps:.4f} bits/pixel-second is below the floor "
                    f"({MIN_BITS_PER_PIXEL_SECOND}) — {size} bytes for {duration:.1f}s at "
                    f"{width}x{height} indicates a truncated or failed render, not footage"
                )
            else:
                result.passed(f"bitrate {bpps:.3f} bits/pixel-second")
    return result


def preflight(path: str, *, media_kind: str, aspect: str, aspect_ratio: str = "9:16",
              duration_bounds: tuple[float, float] = (1.0, 600.0),
              stored_caption: str | None = None,
              rendered_caption: str | None = None) -> PreflightResult:
    """The single entry point publish() calls. Blocks on any failure."""
    if media_kind == "video":
        result = preflight_video(path, aspect_ratio=aspect_ratio,
                                 duration_bounds=duration_bounds)
    else:
        result = preflight_image(path, aspect)

    caption_problem = check_caption(stored_caption, rendered_caption)
    if caption_problem:
        result.fail(caption_problem)
    else:
        result.passed("caption length intact")
    return result


def preflight_wishlist(media_kind: str, aspect: str, failures: list[str]) -> list[dict]:
    """A parked pre-flight failure still needs a wishlist entry in the bank's vocabulary."""
    folder = "raw-video/assembled" if media_kind == "video" else "raw-photo/assembled"
    return [{
        "target_folder": folder,
        "medium": media_kind,
        "aspect": aspect,
        "description": ("re-shoot or re-render: the rendered file failed publish pre-flight "
                        f"({'; '.join(failures)[:200]})"),
    }]
