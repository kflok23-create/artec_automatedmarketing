"""D-2 · A.3 — the live catalog reaches the agent on the read it makes first.

WHAT SUPPLIES EACH SIDE. The list in the brief is generated from `HANDLERS`, the dispatch
table `register(ctx)` populates at session start — the same object every tool call resolves
through. Nothing writes it by hand and no document mirrors it, so the brief cannot claim a
tool that is not callable, nor omit one that is. The test supplies neither side: it asserts
that the brief's list IS `HANDLERS`, whatever `HANDLERS` happens to be.

That is what makes it a mechanism rather than an instruction. The honest limit is recorded
in `_capability_lines`' docstring and asserted below: this makes the truth present and
authoritative on the first read; it cannot reach inside the host's memory injection.
"""

from __future__ import annotations

import re

from plugins.artec.tools import HANDLERS, _capability_lines


def test_the_brief_lists_exactly_the_registered_handlers():
    """No hand-maintained list, no drift. If a tool is added or removed, this follows."""
    text_ = "\n".join(_capability_lines())
    listed = {ln.strip() for ln in text_.splitlines()
              if ln.startswith("    ") and re.fullmatch(r" {4}[a-z_0-9]+", ln)}
    assert listed == set(HANDLERS), (
        f"the brief's tool list and the dispatch table disagree: "
        f"only-in-brief={listed - set(HANDLERS)}, only-in-HANDLERS={set(HANDLERS) - listed}")


def test_the_count_is_derived_never_written():
    text_ = "\n".join(_capability_lines())
    assert f"you have {len(HANDLERS)} tools registered right now" in text_


def test_the_tools_the_agent_denied_are_named():
    """The four denials that cost a live session, and the tool Sunday's gate needs."""
    text_ = "\n".join(_capability_lines())
    for tool in ("record_metrics", "read_draft_posts", "review_video", "review_email"):
        assert f"    {tool}" in text_, f"{tool} is missing from the brief's catalog"


def test_the_brief_says_the_catalog_outranks_memory():
    text_ = "\n".join(_capability_lines())
    assert "THIS WINS" in text_
    assert "memory note" in text_


def test_read_brief_actually_carries_the_catalog(session, engine):
    """End to end through the real tool — a block that exists but is never emitted is not a
    mechanism. This is the check that the wiring, not just the function, is present."""
    from plugins.artec.tools import _read_brief_impl

    brief = _read_brief_impl(engine=engine)
    assert "== YOUR TOOLS — THE LIVE CATALOG, AUTHORITATIVE ==" in brief
    assert "read_draft_posts" in brief
    assert f"you have {len(HANDLERS)} tools" in brief


def test_the_catalog_block_precedes_the_spend_posture(session, engine):
    """Order matters under truncation: what the agent CAN do outranks how much it may spend
    doing it, and the 2200-char memory cap has already evicted content in this system."""
    from plugins.artec.tools import _read_brief_impl

    brief = _read_brief_impl(engine=engine)
    assert brief.index("YOUR TOOLS") < brief.index("AGENT SPEND")
