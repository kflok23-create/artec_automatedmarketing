"""v3 RULE 0 — NO MODEL RENDERS TEXT. EVER.

Diffusion models cannot render letterforms reliably ("Robotics class 1 termı $15",
"One box of blucks $9%" both shipped from production prompts) and no prompt tuning fixes
it. All words, numbers and prices go through Pillow and ffmpeg drawtext with the committed
brand fonts. This guard raises on any prompt bound for any image or video endpoint that
contains a quoted string, a price pattern, or a text-rendering word. It is applied inside
`Fal.run` itself so no call site can forget it — there is no fallback to a model when the
Python path is inconvenient.
"""

from __future__ import annotations

import re


class TextRenderForbidden(RuntimeError):
    pass


# "..." / '...' / “…” / ‘…’ with at least two characters inside — a caption being smuggled.
_QUOTED = re.compile(r'["“‘\']([^"”’\']{2,})["”’\']')

# Currency and percentage shapes across our markets.
_PRICE = re.compile(
    r"""(?xi)
    (   [$€£¥] \s? \d
      | \b (USD|SGD|MYR|RM|S\$) \s? \d
      | \d+ (\.\d+)? \s? %
      | \b \d+ \s? (dollars?|cents?|ringgit|bucks)\b
    )"""
)

_TEXT_WORDS = re.compile(r"(?i)\b(text|caption|title|label|write|says)\b")


def assert_no_text_render(prompt: str, endpoint: str = "") -> None:
    """Raise TextRenderForbidden if the prompt asks a model to place words on pixels."""
    if not prompt:
        return
    where = f" (endpoint {endpoint})" if endpoint else ""
    m = _QUOTED.search(prompt)
    if m:
        raise TextRenderForbidden(
            f"prompt contains a quoted string {m.group(0)!r}{where} — models do not render "
            "text; use Pillow / ffmpeg drawtext with the brand fonts"
        )
    m = _PRICE.search(prompt)
    if m:
        raise TextRenderForbidden(
            f"prompt contains a price pattern {m.group(0)!r}{where} — prices are Pillow "
            "overlays, never model-rendered"
        )
    m = _TEXT_WORDS.search(prompt)
    if m:
        raise TextRenderForbidden(
            f"prompt contains the text-rendering word {m.group(0)!r}{where} — all lettering "
            "goes through Pillow / ffmpeg drawtext"
        )
