"""`allow_person_assets` cannot fix an aspect mismatch, and the two were easy to conflate.

The match probe, run with `allow_person_assets=false`, found three posts unservicable:

    post_1494, post_1495, post_1496 — 0 candidates.
    All want assembled_blocks/video/VERTICAL; the bank holds one assembled video
    and it is LANDSCAPE.

The operator then flipped `allow_person_assets` to true and 265 assets entered the pool. The
tempting inference is that the three are now serviceable. **They are not, unless one of those
265 is a vertical or unknown-aspect assembled video** — because the two filters are
independent predicates in `find_candidates`:

    if aspect:      q = q.where((Asset.aspect == aspect) | (Asset.aspect.is_(None)))
    if not allow_person: q = q.where(has_person IS NULL OR has_person = false)

Relaxing the second does nothing to the first. A landscape video is still landscape.

The ONE escape is `Asset.aspect IS NULL` — unknown dimensions match any requested aspect
(DECISIONS 19). So a newly admitted video whose dimensions were never probed WOULD satisfy
a vertical request, which is worth knowing precisely because it is not obvious.
"""

from __future__ import annotations

from app.models import Asset
from app.toolbox.asset_match import find_candidates

SUBJECT = "assembled_blocks"


def _asset(session, drive_id, *, aspect, has_person, medium="video"):
    session.add(Asset(drive_file_id=drive_id, drive_path=f"raw-video/{drive_id}.mp4",
                      subject=SUBJECT, medium=medium, aspect=aspect,
                      has_person=has_person, status="active", times_used=0))
    session.flush()


def test_flipping_allow_person_does_not_rescue_a_landscape_video(session):
    """THE REAL SHAPE OF THE post_1494/1495/1496 PROBLEM."""
    _asset(session, "landscape_person", aspect="landscape", has_person=True)

    assert find_candidates(session, subject=SUBJECT, medium="video", aspect="vertical",
                           allow_person=False) == []
    assert find_candidates(session, subject=SUBJECT, medium="video", aspect="vertical",
                           allow_person=True) == [], (
        "a landscape video became eligible when allow_person_assets was flipped — the two "
        "filters are independent and relaxing the person one cannot change an aspect")


def test_a_vertical_person_video_IS_rescued_by_the_flip(session):
    """The flip does real work — on the person axis only. Asserted so the previous test is
    not read as 'the flag does nothing'."""
    _asset(session, "vertical_person", aspect="vertical", has_person=True)

    assert find_candidates(session, subject=SUBJECT, medium="video", aspect="vertical",
                           allow_person=False) == []
    found = find_candidates(session, subject=SUBJECT, medium="video", aspect="vertical",
                            allow_person=True)
    assert [a.drive_file_id for a in found] == ["vertical_person"]


def test_an_unknown_aspect_asset_matches_any_request(session):
    """DECISIONS 19: NULL aspect means dimensions were never probed, and matches anything.
    This is the only way one of the 265 newly admitted assets could rescue the three video
    posts, so it is the thing to look for in the probe output."""
    _asset(session, "unknown_aspect", aspect=None, has_person=True)

    found = find_candidates(session, subject=SUBJECT, medium="video", aspect="vertical",
                            allow_person=True)
    assert [a.drive_file_id for a in found] == ["unknown_aspect"]


def test_medium_is_also_independent(session):
    """A photo never satisfies a video request, however the person flag is set — worth
    pinning because 265 new assets are overwhelmingly photos."""
    _asset(session, "vertical_photo", aspect="vertical", has_person=True, medium="photo")

    assert find_candidates(session, subject=SUBJECT, medium="video", aspect="vertical",
                           allow_person=True) == []
