"""Pillow overlays — captions, prices and brand blocks on STILLS. Zero model calls.

This is where words and numbers are allowed to touch pixels (v3 Rule 0): real letterforms
from the four committed brand fonts, never a diffusion model's guess at the alphabet.
"""

from __future__ import annotations

import os
import tempfile
import textwrap

from PIL import Image, ImageDraw, ImageFont

from app.toolbox.text_card import FONTS_DIR


class OverlayError(RuntimeError):
    pass


def _font(filename: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONTS_DIR / filename
    if not path.exists():
        raise OverlayError(f"font file missing: {path}")
    return ImageFont.truetype(str(path), size)


def overlay_caption(image_path: str, caption: str, fonts_cfg: dict,
                    band_color: str = "#12212F", text_color: str = "#F5F3EE") -> str:
    """Caption band along the bottom of a still — brand font, solid band, full contrast."""
    with Image.open(image_path) as im:
        im = im.convert("RGB")
        w, h = im.size
        band_h = max(int(h * 0.14), 96)
        font = _font(fonts_cfg["display"], size=int(band_h * 0.34))
        draw = ImageDraw.Draw(im)
        draw.rectangle([(0, h - band_h), (w, h)], fill=band_color)
        wrapped = textwrap.fill(caption, width=34)
        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font)
        tx = (w - (bbox[2] - bbox[0])) // 2
        ty = h - band_h + (band_h - (bbox[3] - bbox[1])) // 2
        draw.multiline_text((tx, ty), wrapped, font=font, fill=text_color, align="center")
        fd, dst = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        im.save(dst, "JPEG", quality=92)
    return dst


def overlay_price(image_path: str, price_text: str, fonts_cfg: dict,
                  badge_color: str = "#E8A840", text_color: str = "#12212F") -> str:
    """Price badge, top-right. Prices are Pillow's job — never a model's (v3 Rule 0)."""
    with Image.open(image_path) as im:
        im = im.convert("RGB")
        w, h = im.size
        font = _font(fonts_cfg["display"], size=max(int(h * 0.05), 40))
        draw = ImageDraw.Draw(im)
        bbox = draw.textbbox((0, 0), price_text, font=font)
        pad = 28
        bw, bh = bbox[2] - bbox[0] + 2 * pad, bbox[3] - bbox[1] + 2 * pad
        x0, y0 = w - bw - 48, 48
        draw.rounded_rectangle([(x0, y0), (x0 + bw, y0 + bh)], radius=18, fill=badge_color)
        draw.text((x0 + pad, y0 + pad - bbox[1]), price_text, font=font, fill=text_color)
        fd, dst = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        im.save(dst, "JPEG", quality=92)
    return dst
