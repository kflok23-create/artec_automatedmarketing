"""post_1487's HTTP 400: a constant defined twice and read nowhere.

    🔁 RETRY — post_1487 · youtube · publish:
       UploadPostError: upload-post /upload failed: HTTP 400

`PLATFORM_RULES["youtube"]["max_title"] = 100` has existed since the client was written.
`grep -rn max_title` returned TWO DEFINITIONS and ZERO READS. `validate_for_platform`
checks `max_caption` and video duration; it never checked the title. So publish sent
`f"{caption}\n{tracked_url}"` into a 100-character field whose caption is bounded only by
`max_caption: 5000`.

A 400 is a CONTRACT ANSWER, not a transient. The platform said the field was too long, and
`retry_post` would have failed identically every time — which is how third-party contract
drift gets mistaken for flakiness. Diagnosed by reading the contract, not by retrying.

**post_1496 in Sunday's draft set is also youtube and carries the same defect.** It is not a
one-off; it is systematic for the channel, which is why the fix is a rule and not an edit to
one row.

Instance #5 of the disconnected guard: correct logic, connected to nothing. Same shape as
the ruleset that targeted no branches, the memory audit that scanned no files, and
`validate_required_config` that nothing called.
"""

from __future__ import annotations

import pytest

from app.integrations.upload_post_client import PLATFORM_RULES, title_and_description

# Longer than youtube's 100-char title limit, shorter than its 5000-char caption limit —
# i.e. a perfectly ordinary caption, which is the point.
LONG = ("Five logic challenges you can set with twelve blocks, and the one most children "
        "solve fastest is not the one adults expect when they try it themselves first")
URL = "https://artec.my/p/1487"


def test_youtube_title_is_bounded_by_max_title():
    """THE DEFECT. Before the fix this field carried the whole caption."""
    title, _ = title_and_description("youtube", LONG, URL)
    limit = PLATFORM_RULES["youtube"]["max_title"]
    assert len(title) <= limit, f"title is {len(title)} chars; youtube's limit is {limit}"


def test_nothing_is_lost_the_caption_moves_to_description():
    """Truncating without sending `description` would fix the 400 by discarding the post.
    `description` is a documented shared field (DECISIONS #1) that had never been sent."""
    _, description = title_and_description("youtube", LONG, URL)
    assert LONG in description
    assert URL in description


def test_the_title_is_cut_at_a_word_boundary():
    title, _ = title_and_description("youtube", LONG, URL)
    assert title.endswith("…")
    assert not title.removesuffix("…").endswith(" ")
    # The ellipsis lives INSIDE the limit, not appended past it.
    assert len(title) <= PLATFORM_RULES["youtube"]["max_title"]


def test_a_short_youtube_caption_is_not_truncated():
    title, description = title_and_description("youtube", "Twelve blocks, six shapes", URL)
    assert title == "Twelve blocks, six shapes"
    assert URL in description


@pytest.mark.parametrize("channel", ["instagram", "tiktok", "facebook", "linkedin"])
def test_platforms_without_a_title_limit_are_UNCHANGED(channel):
    """These four have been publishing successfully. A fix for a broken surface must not
    alter a working one — the title still carries caption + URL exactly as before."""
    title, description = title_and_description(channel, LONG, URL)
    assert title == f"{LONG}\n{URL}"
    assert description == title


def test_every_declared_max_title_is_actually_enforced():
    """THE CLASS, not the instance. If a `max_title` is added to another platform later,
    this fails unless `title_and_description` honours it — so the constant cannot go back
    to being decorative.

    WHAT SUPPLIES EACH SIDE: the limit comes from PLATFORM_RULES; the title comes from the
    function under test. Neither is written by this test.
    """
    for channel, rules in PLATFORM_RULES.items():
        limit = rules.get("max_title")
        if not limit:
            continue
        title, _ = title_and_description(channel, "x" * (limit * 3), URL)
        assert len(title) <= limit, (
            f"{channel} declares max_title={limit} and title_and_description ignored it — "
            "a constant that is defined and never read is not a rule, it is a comment")
