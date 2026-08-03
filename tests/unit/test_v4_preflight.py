"""v4 §5.3 publish pre-flight (A) and slot validation (F).

Acceptance 11 (trailing moov parked), 12 (over-duration parked), 13 (caption length),
15 (slot rejected at write time + orphan sweep). SQLite substrate — these are pure
file/logic checks with no Postgres semantics.
"""

import os
import shutil
import subprocess
from datetime import date

import pytest
from PIL import Image

from app.config import OperatorError
from app.models import Post
from app.scheduler import sweep_orphaned_slots
from app.stages.preflight import (
    check_caption,
    ffprobe_available,
    preflight,
    preflight_image,
    preflight_video,
    preflight_wishlist,
)

HAS_FFMPEG = shutil.which("ffmpeg") is not None and ffprobe_available()
WEEK = date(2026, 8, 24)


def _image(tmp_path, size=(1080, 1080), name="i.jpg"):
    p = str(tmp_path / name)
    Image.new("RGB", size, "#0168B7").save(p, quality=95)
    return p


def _video(tmp_path, name="v.mp4", seconds=3, size="1080x1920", faststart=True,
           realistic=True):
    """`realistic=True` encodes HIGH-ENTROPY noise, which produces a bitrate in the same
    band as real `raw-video/` footage (~1 bit/pixel-second). A solid-colour clip is
    near-zero-entropy (~0.01) and is nothing like the thing the pre-flight guards — tuning
    the floor to accommodate one is how the fixture reshaped the spec. Solid colour is
    still fine for the moov/duration/aspect checks, which do not measure content.
    """
    out = str(tmp_path / name)
    # Measured bits/pixel-second on this encoder: testsrc2 ~2.9, real 1080x1920 social
    # H.264 ~1.45, solid colour ~0.01, pure lavfi noise ~105 (incompressible, and as
    # unrepresentative as solid colour in the opposite direction). testsrc2 is structured
    # moving content and is the closest available proxy for bank footage.
    source = (f"testsrc2=s={size}:d={seconds}:r=30" if realistic
              else f"color=c=blue:s={size}:d={seconds}:r=30")
    flags = ["-movflags", "+faststart"] if faststart else []
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", source,
         "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         *flags, "-y", out],
        check=True, capture_output=True, timeout=300,
    )
    return out


# ---- images ------------------------------------------------------------------------------

def test_image_passes_when_sound(tmp_path):
    result = preflight_image(_image(tmp_path), "square")
    assert result.ok, result.failures


def test_image_wrong_aspect_is_blocked(tmp_path):
    result = preflight_image(_image(tmp_path, size=(1920, 1080)), "square")
    assert not result.ok
    assert any("aspect" in f for f in result.failures)


def test_truncated_image_is_blocked(tmp_path):
    path = _image(tmp_path)
    with open(path, "r+b") as fh:          # lop off the tail — a real truncation
        fh.truncate(os.path.getsize(path) // 3)
    result = preflight_image(path, "square")
    assert not result.ok


def test_implausibly_small_image_is_blocked(tmp_path):
    path = str(tmp_path / "tiny.jpg")
    with open(path, "wb") as fh:
        fh.write(b"\xff\xd8\xff\xd9")
    result = preflight_image(path, "square")
    assert not result.ok


# ---- captions (acceptance 13) -------------------------------------------------------------

def test_caption_length_mismatch_fails_preflight():
    stored = "Build focus, one block at a time. Connecting sideways gives you options."
    truncated = stored[:40]                # the 'gives yo' class
    problem = check_caption(stored, truncated)
    assert problem and "truncation" in problem


def test_caption_intact_passes():
    stored = "Build focus, one block at a time."
    assert check_caption(stored, stored) is None
    assert check_caption(stored, None) is None      # not re-rendered = unchanged


# ---- video (acceptance 11, 12) — real ffprobe, never a mock --------------------------------

@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH here — this check "
                                           "runs in CI and is asserted by artec doctor in "
                                           "the deployed container")
def test_sound_video_passes(tmp_path):
    result = preflight_video(_video(tmp_path), aspect_ratio="9:16",
                             duration_bounds=(1.0, 600.0))
    assert result.ok, result.failures


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH here")
def test_trailing_moov_atom_is_parked_not_published(tmp_path):
    # acceptance 11 — post_1485's exact failure mode.
    result = preflight_video(_video(tmp_path, name="slow.mp4", faststart=False))
    assert not result.ok
    assert any("moov" in f for f in result.failures)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH here")
def test_over_duration_video_is_parked(tmp_path):
    # acceptance 12 — outside the platform bound.
    result = preflight_video(_video(tmp_path, name="long.mp4", seconds=5),
                             duration_bounds=(1.0, 3.0))
    assert not result.ok
    assert any("duration" in f for f in result.failures)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH here")
def test_wrong_aspect_video_is_parked(tmp_path):
    result = preflight_video(_video(tmp_path, name="wide.mp4", size="1920x1080"),
                             aspect_ratio="9:16")
    assert not result.ok
    assert any("aspect" in f for f in result.failures)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH here")
def test_low_bitrate_render_is_parked_even_when_structurally_perfect(tmp_path):
    """The guard the absolute byte floor stopped providing. A solid-colour clip has a
    leading moov, the right aspect and the right duration — and still carries the bitrate
    signature of a truncated or failed render."""
    result = preflight_video(_video(tmp_path, name="flat.mp4", realistic=False))
    assert not result.ok
    assert any("bits/pixel-second" in f for f in result.failures)
    # …and it failed ONLY on bitrate: everything structural about it was sound.
    assert not any(("moov" in f or "aspect" in f or "duration" in f)
                   for f in result.failures)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH here")
def test_bitrate_floor_is_resolution_and_duration_independent(tmp_path):
    """An absolute byte floor cannot tell a legitimate short clip from a truncated long
    one. Realistic content passes at very different sizes."""
    short = preflight_video(_video(tmp_path, name="s.mp4", seconds=2))
    longer = preflight_video(_video(tmp_path, name="l.mp4", seconds=6))
    assert short.ok, short.failures
    assert longer.ok, longer.failures


def test_unreadable_video_is_blocked_with_a_named_error(tmp_path):
    junk = str(tmp_path / "junk.mp4")
    with open(junk, "wb") as fh:
        fh.write(b"\x00" * 40_000)
    result = preflight_video(junk)
    assert not result.ok


def test_parked_preflight_failure_carries_a_wishlist_entry():
    entries = preflight_wishlist("video", "vertical", ["moov atom is not leading"])
    assert entries[0]["target_folder"] == "raw-video/assembled"
    assert entries[0]["medium"] == "video"


def test_preflight_entry_point_combines_media_and_caption(tmp_path):
    result = preflight(_image(tmp_path), media_kind="photo", aspect="square",
                       stored_caption="hello", rendered_caption="hel")
    assert not result.ok
    assert any("caption" in f for f in result.failures)


# ---- F: slot validation at write time + the orphan sweep -----------------------------------

def test_bespoke_ideate_rejects_an_off_vocabulary_slot(session):
    from app.config import set_config
    from app.integrations.fakes import FakeLLM
    from app.stages.ideate import ideate

    set_config(session, "seo_seeds", ["a", "b", "c", "d", "e"])

    class _BadSlotLLM(FakeLLM):
        def complete_json(self, prompt_file, variables, max_tokens=4000):
            if prompt_file == "ideate_v1.md":
                return [{"channel": "instagram", "angle": "a", "hook": "h",
                         "cta_type": "discount", "cta_placement": "caption_end",
                         "keywords": [], "slot": "afternoon"}]   # not in slot_times
            return super().complete_json(prompt_file, variables, max_tokens)

    with pytest.raises(OperatorError, match="afternoon"):
        ideate(session, _BadSlotLLM(), week_start=WEEK, log=lambda *_: None)


def test_orphan_sweep_finds_rendered_posts_no_slot_pass_will_ever_select(session):
    session.add(Post(post_id="post_7700", week_start=WEEK, channel="instagram",
                     status="RENDERED", slot="evening"))          # valid
    session.add(Post(post_id="post_7701", week_start=WEEK, channel="instagram",
                     status="RENDERED", slot="afternoon"))        # orphan
    session.add(Post(post_id="post_7702", week_start=WEEK, channel="instagram",
                     status="RENDERED", slot="afternoon",
                     external_post_id="up_1"))                    # already published
    session.flush()
    orphans = sweep_orphaned_slots(session)
    assert [o["post_id"] for o in orphans] == ["post_7701"]
    assert "afternoon" in orphans[0]["reason"]
