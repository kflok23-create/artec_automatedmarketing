"""§7.4 TEXT CARD — punchline text on a solid brand background, rendered locally with
Pillow and the committed static fonts. Zero-asset, zero-cost; must always succeed (given
fonts on disk — doctor checks them).

Backgrounds draw from the three approved pairings ONLY, each with its locked text colour
(never light text on amber). Consecutive cards rotate across the three.
"""

from __future__ import annotations

import os
import tempfile
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.orm import Session

from app.config import get_config, set_config

# Package-relative on purpose: the fonts live INSIDE the `app` package
# (app/assets/fonts/) so they ship in the wheel and resolve identically from the source
# tree and from site-packages. nixpacks runs `pip install .`, and at runtime imports come
# from /opt/venv/.../site-packages — a repo-root-relative path would silently miss the
# installed copy (that divergence was a live doctor RED). Doctor imports this same
# constant, so the runtime loader and the check can never diverge.
FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"

CANVAS = {"vertical": (1080, 1920), "square": (1080, 1080), "landscape": (1920, 1080)}


class TextCardError(RuntimeError):
    pass


def next_pairing(session: Session) -> dict:
    """Rotate across the three approved pairings so consecutive cards differ."""
    pairings = get_config(session, "text_card_pairings")
    idx = int(get_config(session, "text_card_pairing_idx", 0)) % len(pairings)
    set_config(session, "text_card_pairing_idx", (idx + 1) % len(pairings))
    return pairings[idx]


def _font(filename: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONTS_DIR / filename
    if not path.exists():
        raise TextCardError(
            f"font file missing: assets/fonts/{filename} — commit the STATIC .ttf instances "
            "(variable fonts render Regular where Bold was asked; see docs/ASSET_BANK.md)"
        )
    return ImageFont.truetype(str(path), size)


def render_text_card(
    text: str,
    pairing: dict,
    fonts_cfg: dict,
    aspect: str = "square",
    eyebrow: str | None = None,
) -> str:
    if aspect not in CANVAS:
        raise TextCardError(f"unknown aspect {aspect!r}")
    w, h = CANVAS[aspect]
    img = Image.new("RGB", (w, h), pairing["bg"])
    draw = ImageDraw.Draw(img)

    display = _font(fonts_cfg["display"], size=int(h * 0.07))
    margin = int(w * 0.10)
    wrapped = textwrap.fill(text, width=16 if aspect == "vertical" else 20)

    if eyebrow:
        label = _font(fonts_cfg["label"], size=int(h * 0.022))
        draw.text((margin, int(h * 0.12)), eyebrow.upper(), font=label, fill=pairing["text"])

    bbox = draw.multiline_textbbox((0, 0), wrapped, font=display, spacing=int(h * 0.015))
    ty = (h - (bbox[3] - bbox[1])) // 2
    draw.multiline_text((margin, ty), wrapped, font=display, fill=pairing["text"], spacing=int(h * 0.015))

    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    img.save(path, "PNG")
    return path
