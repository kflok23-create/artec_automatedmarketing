"""v3 toolbox — Pillow/ffmpeg pipeline (acceptance 6, 7), enhance whitelist, trim guard.
The kontext/Seedance routing tests this file used to hold are INVERTED by v3: those
endpoints must not exist (see test_v3_toolbox.py's repo scan)."""

import shutil

import pytest
from PIL import Image

from app.config import OPERATOR_CONSTANTS
from app.toolbox import edit_combine
from app.toolbox.edit_combine import moov_before_mdat
from app.toolbox.enhance import (
    EnhanceMediumError,
    EnhanceNotWhitelisted,
    build_enhance_args,
    enhance_image,
)

WHITELIST = OPERATOR_CONSTANTS["enhance_whitelist"]
ENDPOINTS = OPERATOR_CONSTANTS["model_endpoints"]

HAS_FFMPEG = shutil.which("ffmpeg") is not None


class _FfmpegStub:
    """Records the filter/output kwargs the pipeline would hand to ffmpeg."""

    def __init__(self, duration=40):
        self.duration = duration
        self.filters: list[tuple] = []
        self.output_kwargs: list[dict] = []
        self.ran = 0

    def probe(self, path):
        return {"format": {"duration": str(self.duration)}}

    def input(self, src, **kw):
        return self

    def filter(self, name, *args, **kw):
        self.filters.append((name, args, kw))
        return self

    def output(self, dst, **kw):
        self.output_kwargs.append(kw)
        return self

    def overwrite_output(self):
        return self

    def run(self, quiet=True):
        self.ran += 1


def test_overlong_clip_trimmed_before_any_use(monkeypatch):
    stub = _FfmpegStub(duration=40)
    monkeypatch.setattr(edit_combine, "ffmpeg", stub)
    out = edit_combine.trim_clip("clip.mp4", max_s=15)
    assert stub.ran == 1 and out != "clip.mp4"


def test_every_video_output_carries_faststart(monkeypatch):
    # v3: without a leading moov atom platforms reject the upload (post_1485's failure).
    stub = _FfmpegStub(duration=40)
    monkeypatch.setattr(edit_combine, "ffmpeg", stub)
    edit_combine.trim_clip("clip.mp4", max_s=10)
    edit_combine.frame_video_aspect("clip.mp4", "9:16")
    edit_combine.speed_video("clip.mp4", 1.5)
    edit_combine.drawtext_overlay("clip.mp4", "hi", font_path="f.ttf")
    for kw in stub.output_kwargs:
        assert kw.get("movflags") == "+faststart", f"output missing faststart: {kw}"


def test_drawtext_always_uses_textfile_never_inline(monkeypatch, tmp_path):
    # v3 acceptance 7 (structure): colons/commas/apostrophes/percent break inline
    # escaping — the filter must receive textfile=, and the file must hold the raw text.
    stub = _FfmpegStub()
    monkeypatch.setattr(edit_combine, "ffmpeg", stub)
    nasty = "Deal: 50% off, don't miss it, really"
    edit_combine.drawtext_overlay("clip.mp4", nasty, font_path="f.ttf")
    drawtext = [f for f in stub.filters if f[0] == "drawtext"]
    assert drawtext, "drawtext filter not applied"
    kw = drawtext[0][2]
    assert "textfile" in kw and "text" not in kw
    with open(kw["textfile"], encoding="utf-8") as fh:
        assert fh.read() == nasty


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg binary not on PATH")
def test_video_post_end_to_end_ffmpeg_only(tmp_path):
    # v3 acceptance 6: raw footage in → platform mp4 out, ZERO model calls, moov leading.
    import ffmpeg as real_ffmpeg

    src = str(tmp_path / "src.mp4")
    (
        real_ffmpeg.input("color=c=blue:s=320x240:d=4", f="lavfi")
        .output(src, pix_fmt="yuv420p", **{"movflags": "+faststart"})
        .overwrite_output()
        .run(quiet=True)
    )
    from app.toolbox.text_card import FONTS_DIR

    font = str(FONTS_DIR / OPERATOR_CONSTANTS["fonts"]["display"])
    out = edit_combine.edit_video_pipeline(
        src, duration_s=3, aspect_ratio="9:16",
        caption="Deal: 50% off, don't miss it", font_path=font,
    )
    meta = real_ffmpeg.probe(out)  # passes ffprobe
    stream = next(s for s in meta["streams"] if s["codec_type"] == "video")
    assert (int(stream["width"]), int(stream["height"])) == (1080, 1920)
    assert moov_before_mdat(out), "moov atom must lead (faststart) or platforms reject it"


def test_moov_detector():
    import os
    import tempfile

    fd, p = tempfile.mkstemp(suffix=".mp4")
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"\x00\x00\x00\x18ftypmp42" + b"\x00\x00\x00\x08moov" + b"\x00\x00\x00\x08mdat")
    assert moov_before_mdat(p)
    fd, p2 = tempfile.mkstemp(suffix=".mp4")
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"\x00\x00\x00\x18ftypmp42" + b"\x00\x00\x00\x08mdat" + b"\x00\x00\x00\x08moov")
    assert not moov_before_mdat(p2)


def _jpeg(tmp_path):
    p = str(tmp_path / "photo.jpg")
    Image.new("RGB", (64, 64), "#0168B7").save(p)
    return p


def test_enhance_off_whitelist_not_callable(tmp_path):
    with pytest.raises(EnhanceNotWhitelisted):
        enhance_image(None, ENDPOINTS, WHITELIST, "background_swap", _jpeg(tmp_path), "photo")
    with pytest.raises(EnhanceNotWhitelisted):
        # known op, but struck from the configured whitelist → still not callable
        enhance_image(None, ENDPOINTS, ["upscale"], "color_correct", _jpeg(tmp_path), "photo")


def test_enhance_raises_on_video(tmp_path):
    with pytest.raises(EnhanceMediumError):
        enhance_image(None, ENDPOINTS, WHITELIST, "upscale", _jpeg(tmp_path), "video")
    with pytest.raises(EnhanceMediumError):
        build_enhance_args("u", "video")


def test_enhance_pillow_ops_need_no_model(tmp_path):
    # color_correct / autocontrast are Pillow — fal=None proves zero model calls.
    out = enhance_image(None, ENDPOINTS, WHITELIST, "color_correct", _jpeg(tmp_path), "photo")
    assert out.endswith(".jpg")
    out = enhance_image(None, ENDPOINTS, WHITELIST, "autocontrast", _jpeg(tmp_path), "photo")
    assert out.endswith(".jpg")


def test_enhance_upscale_args_locked():
    args = build_enhance_args("u", "photo")
    assert args["scale"] == 1.5
    assert args["creativity"] <= 0.2
