"""Acceptance 2a (kontext routing), 2b (video routing), 2c (trim), 2e (enhance guard)."""

import pytest

from app.config import OPERATOR_CONSTANTS
from app.toolbox import edit_combine
from app.toolbox.edit_combine import build_kontext_args, build_video_args, route_video
from app.toolbox.enhance import EnhanceMediumError, build_enhance_args

FAMILY = OPERATOR_CONSTANTS["video_family"]


def test_kontext_zero_images_text_to_image():
    key, args = build_kontext_args("p", [])
    assert key == "kontext_t2i"
    assert "image_url" not in args and "image_urls" not in args


def test_kontext_one_image_singular_key():
    key, args = build_kontext_args("p", ["u1"])
    assert key == "kontext_single"
    assert args["image_url"] == "u1"          # SINGULAR — exact key name
    assert "image_urls" not in args


def test_kontext_multi_array_key():
    key, args = build_kontext_args("p", ["u1", "u2"])
    assert key == "kontext_multi"
    assert args["image_urls"] == ["u1", "u2"]  # ARRAY — exact key name
    assert "image_url" not in args


def test_video_routing_counts():
    assert route_video(0, FAMILY) == FAMILY["text_to_video"]
    assert route_video(1, FAMILY) == FAMILY["reference_to_video"]
    assert route_video(3, FAMILY) == FAMILY["reference_to_video"]
    from app.toolbox.edit_combine import EditCombineError

    with pytest.raises(EditCombineError):
        route_video(4, FAMILY)


def test_video_args_single_element_array_and_bracket_syntax():
    endpoint, args = build_video_args(FAMILY, "child builds a crane", ["v1"], duration_s=12)
    assert endpoint == FAMILY["reference_to_video"]
    assert args["video_urls"] == ["v1"]
    assert "[Video1]" in args["prompt"]  # Seedance bracket syntax, from config


def test_video_args_at_syntax_when_family_switched():
    wan = dict(FAMILY, reference_syntax="at", reference_to_video="wan/v2.6/reference-to-video")
    _, args = build_video_args(wan, "p", ["v1", "v2"], duration_s=10)
    assert "@Video1" in args["prompt"] and "@Video2" in args["prompt"]
    assert "[Video1]" not in args["prompt"]


def test_video_duration_clamped_to_family_window():
    _, args = build_video_args(FAMILY, "p", [], duration_s=99)
    assert args["duration"] == FAMILY["duration_range_s"][1]


class _FfmpegStub:
    def __init__(self, duration):
        self.duration = duration
        self.ran = False

    def probe(self, path):
        return {"format": {"duration": str(self.duration)}}

    def input(self, src, **kw):
        return self

    def output(self, dst, **kw):
        return self

    def overwrite_output(self):
        return self

    def run(self, quiet=True):
        self.ran = True


def test_overlong_clip_is_trimmed_before_submission(monkeypatch):
    stub = _FfmpegStub(duration=40)
    monkeypatch.setattr(edit_combine, "ffmpeg", stub)
    out = edit_combine.trim_clip("clip.mp4", max_s=15)
    assert stub.ran, "40s clip must be ffmpeg-trimmed to the endpoint window"
    assert out != "clip.mp4"


def test_short_clip_passes_untouched(monkeypatch):
    stub = _FfmpegStub(duration=8)
    monkeypatch.setattr(edit_combine, "ffmpeg", stub)
    assert edit_combine.trim_clip("clip.mp4", max_s=15) == "clip.mp4"
    assert not stub.ran


def test_enhance_raises_on_video():
    with pytest.raises(EnhanceMediumError):
        build_enhance_args("u", "video")


def test_enhance_photo_args_locked():
    args = build_enhance_args("u", "photo")
    assert args["scale"] == 1.5
    assert args["creativity"] <= 0.2  # sharpen, never invent block geometry
