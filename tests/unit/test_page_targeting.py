"""§B — POSTS MUST GO OUT AS THE PAGE, AND THE WRONG PAGE MUST BE UNREACHABLE.

Facebook and LinkedIn are OAuth'd through a personal account that administers the Artec
page. Omit the page parameter and Upload-Post returns SUCCESS — the post lands on the
personal timeline. Nothing in the response distinguishes the two outcomes, so no downstream
check could have caught it.

It breaks measurement, not only branding. LinkedIn exposes NO member-level analytics, so a
personally-published post can never be measured at all; Facebook analytics requires
`page_id`. Published personally, `metrics` stays NULL forever while `learn` scores those
channels as though it had measured them — stale reading as zero, the lane rule's own
failure mode, arriving through the publisher.

THE CENTRAL TEST HERE IS THE NEGATIVE ONE. The account administers a second organisation,
`urn:li:organization:74925843` "Tech Up Advance l GoTechUp", which is a different business.
Configuring the right URN proves the right URN works. It does NOT prove the wrong one is
unreachable. Those are different claims and this build has repeatedly been burned by the
first standing in for the second — `publish-by-slot` reported PROVEN over an empty board on
exactly that confusion.
"""

from __future__ import annotations

import pytest

from app.integrations.upload_post_client import (
    FOREIGN_ORGANISATIONS,
    PAGE_TARGET_CONFIG_KEY,
    PAGE_TARGET_FIELD,
    PAGE_TARGETED_PLATFORMS,
    ForeignPageTarget,
    MissingPageTarget,
    UploadPost,
    page_targets,
    verify_publish_target,
)

ARTEC_FB = "574903736241765"
ARTEC_LI = "urn:li:organization:97212204"
FOREIGN_LI = "urn:li:organization:74925843"

GOOD = {"facebook": ARTEC_FB, "linkedin": ARTEC_LI}


@pytest.fixture
def client():
    class S:
        UPLOAD_POST_API_KEY = "test-key"
        UPLOAD_POST_USER = "ArtecMy"

    return UploadPost(S())


# --- the parameter is actually sent, under the name the docs specify --------------------

@pytest.mark.parametrize("platform,expected_field,expected_value", [
    ("facebook", "facebook_page_id", ARTEC_FB),
    ("linkedin", "target_linkedin_page_id", ARTEC_LI),
])
def test_the_page_parameter_is_passed_under_the_documented_name(
        client, platform, expected_field, expected_value):
    """The two field names are NOT symmetric and are not guessable — they come from
    docs.upload-post.com. A plausible-looking wrong name fails silently, exactly like
    omitting it."""
    fields = client._page_fields(platform, GOOD)
    assert fields == {expected_field: expected_value}


def test_platforms_that_do_not_publish_as_a_page_get_no_parameter(client):
    for platform in ("instagram", "tiktok", "youtube"):
        assert client._page_fields(platform, GOOD) == {}


# --- THE NEGATIVE TEST -----------------------------------------------------------------

def test_the_other_administered_organisation_is_REFUSED(client):
    """THE TEST THIS PART EXISTS FOR.

    The OAuth'd account administers this org, so Upload-Post would accept the post and
    return success. This refusal is the only thing between an Artec post and another
    company's page.
    """
    with pytest.raises(ForeignPageTarget) as excinfo:
        client._page_fields("linkedin", {"linkedin": FOREIGN_LI})
    message = str(excinfo.value)
    assert FOREIGN_LI in message
    assert "Tech Up Advance" in message
    assert "different business" in message.lower()


def test_every_known_foreign_organisation_is_refused(client):
    """Guards the list itself: adding an entry to FOREIGN_ORGANISATIONS must block it."""
    for urn in FOREIGN_ORGANISATIONS:
        with pytest.raises(ForeignPageTarget):
            client._page_fields("linkedin", {"linkedin": urn})


# --- refuses rather than defaults ------------------------------------------------------

@pytest.mark.parametrize("platform", PAGE_TARGETED_PLATFORMS)
@pytest.mark.parametrize("value", ["", None, "   "])
def test_a_missing_target_refuses_and_never_falls_through(client, platform, value):
    with pytest.raises(MissingPageTarget) as excinfo:
        client._page_fields(platform, {platform: value})
    assert PAGE_TARGET_CONFIG_KEY[platform] in str(excinfo.value)
    assert "PERSONAL" in str(excinfo.value)


def test_no_target_at_all_refuses(client):
    for platform in PAGE_TARGETED_PLATFORMS:
        with pytest.raises(MissingPageTarget):
            client._page_fields(platform, None)


# --- config is the one source, read by publish AND analytics ---------------------------

def test_page_targets_reads_the_seeded_config(session):
    targets = page_targets(session)
    assert targets["facebook"] == ARTEC_FB
    assert targets["linkedin"] == ARTEC_LI


def test_analytics_and_publish_read_the_SAME_config_key():
    """One source. A second place to put these is a second place for them to drift, and
    drift is invisible here — a wrong page returns success AND returns some analytics."""
    from app.integrations.upload_post_client import PAGE_ANALYTICS_FIELD

    assert set(PAGE_ANALYTICS_FIELD) == set(PAGE_TARGET_CONFIG_KEY) == set(PAGE_TARGET_FIELD)


# --- post-publish verification ---------------------------------------------------------

def test_a_post_that_landed_on_the_foreign_org_is_detected():
    """Item 6: caught on the FIRST occurrence, not at the first missing metric weeks later."""
    response = {"page_urn": FOREIGN_LI, "url": "https://linkedin.com/feed/update/123"}
    mismatch = verify_publish_target("linkedin", GOOD, response)
    assert mismatch and "DIFFERENT BUSINESS" in mismatch
    assert FOREIGN_LI in mismatch


def test_a_post_on_the_right_page_reports_no_mismatch():
    assert verify_publish_target("facebook", GOOD, {"page_id": ARTEC_FB}) is None
    assert verify_publish_target("linkedin", GOOD, {"page_urn": ARTEC_LI}) is None


def test_an_unknown_owner_is_not_reported_as_a_mismatch():
    """Unknown is not a match and not a mismatch. Inventing a mismatch from a silent
    response would park correctly-published posts; the doctor check covers the unknown."""
    assert verify_publish_target("linkedin", GOOD, {"status": "ok"}) is None


def test_untargeted_platforms_are_not_verified():
    assert verify_publish_target("instagram", GOOD, {"page_id": "anything"}) is None


# --- the config keys are required so a service cannot boot without them ----------------

def test_both_ids_are_required_config_keys():
    from app.config import REQUIRED_CONFIG_KEYS

    common = REQUIRED_CONFIG_KEYS["common"]
    assert "facebook_page_id" in common
    assert "linkedin_organization_urn" in common


def test_the_config_manifest_is_ACTUALLY_VALIDATED_AT_BOOT(repo_root):
    """`validate_required_config` existed, its docstring said "Called at boot", and NOTHING
    called it — the only references in the repo were in tests. `REQUIRED_CONFIG_KEYS` was a
    list that guaranteed nothing, so adding the page-target keys to it would have
    guaranteed nothing either.

    Same shape as the ruleset that applied to no branches and the memory audit that scanned
    no files: present in the source, absent from the running system. This asserts the wiring
    rather than the function, because the function was never the problem.
    """
    for path, role in (("app/main.py", "api"), ("app/scheduler.py", "scheduler")):
        src = (repo_root / path).read_text(encoding="utf-8")
        assert "validate_required_config" in src, (
            f"{path} does not validate the config manifest at boot — REQUIRED_CONFIG_KEYS "
            "is then a list nothing enforces")
        assert f'validate_required_config(session, "{role}")' in src


# --- D-iv · withdrawn posts ------------------------------------------------------------

def test_learn_excludes_withdrawn_posts_and_never_scores_them_as_zero(session):
    """post_1488 and post_1489 published to a PERSONAL profile before page targeting existed
    and were removed at source by the operator.

    Their rows still say PUBLISHED, correctly — they were. But scoring them tells `learn`
    that facebook and linkedin published and earned nothing, marking down two channels for a
    targeting defect rather than for the creative. That is the `email_min_recipients` trap
    arriving on two other channels, and invariant 2 in a new costume: stale is not zero, and
    removed-at-source is not zero either.

    WHAT SUPPLIES EACH SIDE: the posts are inserted here with real published state; the
    exclusion comes from `learn` reading `withdrawn_at`. The test asserts on the SET that
    reaches scoring, not on a count — an empty board proves nothing.
    """
    from datetime import UTC, date, datetime

    from app.models import Post
    from app.stages.learn import learn

    week = date(2026, 8, 3)
    for pid, channel, withdrawn in (
        ("post_kept", "instagram", None),
        ("post_gone_fb", "facebook", datetime(2026, 8, 5, tzinfo=UTC)),
        ("post_gone_li", "linkedin", datetime(2026, 8, 5, tzinfo=UTC)),
    ):
        session.add(Post(
            post_id=pid, channel=channel, week_start=week, status="PUBLISHED",
            slot="evening", external_post_id=f"ext_{pid}",
            posted_at=datetime(2026, 8, 4, tzinfo=UTC),
            withdrawn_at=withdrawn,
            withdrawn_reason=("published to the wrong surface before page targeting existed"
                              if withdrawn else None)))
    session.flush()

    seen: list[str] = []
    learn(session, llm=None, week_start=week, log=lambda m: seen.append(str(m)))

    joined = " ".join(seen)
    assert "EXCLUDING 2 withdrawn post(s)" in joined
    assert "post_gone_fb" in joined and "post_gone_li" in joined
    # Named as withdrawn, and explicitly NOT as zero.
    assert "not zero" in joined


def test_a_withdrawn_post_keeps_its_external_id_so_republish_still_refuses(session):
    """Withdrawal is an ADDITIONAL fact, not an erasure. `posts` is the ledger of record,
    and the never-republish guard reads `external_post_id`, which must survive untouched."""
    from datetime import UTC, date, datetime

    from app.models import Post

    session.add(Post(post_id="post_w", channel="facebook", week_start=date(2026, 8, 3),
                     status="PUBLISHED", slot="lunch", external_post_id="ext_w",
                     posted_at=datetime(2026, 8, 4, tzinfo=UTC),
                     withdrawn_at=datetime(2026, 8, 5, tzinfo=UTC),
                     withdrawn_reason="removed at source"))
    session.flush()
    row = session.get(Post, "post_w")
    assert row.status == "PUBLISHED"      # it WAS published; that stays true
    assert row.external_post_id == "ext_w"
    assert row.withdrawn_at is not None


# --- §1.2 · three-state target verification, surfaced in NEEDS YOU ---------------------

def test_the_three_target_states_are_never_collapsed():
    """`verify_publish_target` returns None for BOTH "verified correct" and "could not
    check". Merging them is how an unperformed check reads as a pass."""
    from app.integrations.upload_post_client import _target_owner_reported

    assert _target_owner_reported({"page_id": ARTEC_FB}) is True
    assert _target_owner_reported({"status": "ok"}) is False      # nothing to check against
    assert _target_owner_reported({}) is False


@pytest.mark.parametrize("state,expected", [
    ("mismatch", "WRONG SURFACE"),
    ("unverified", "TARGET UNVERIFIED"),
])
def test_both_non_verified_states_reach_NEEDS_YOU(session, state, expected):
    """Sunday publishes unattended, so surfacing IS the safety net. post_1488 and post_1489
    sat on a personal timeline for days because this lived only in a run log."""
    from datetime import UTC, date, datetime

    from app.models import Post
    from app.stages.digest import build_payload, render_digest_text

    session.add(Post(
        post_id="post_bad", channel="linkedin", week_start=date(2026, 8, 3),
        status="PUBLISHED", slot="lunch", external_post_id="ext_bad",
        posted_at=datetime(2026, 8, 4, tzinfo=UTC),
        target_check={"state": state, "intended": ARTEC_LI,
                      "detail": "post landed on urn:li:organization:74925843",
                      "checked_at": "2026-08-04T01:00:00+00:00"}))
    session.flush()

    payload = build_payload(session, brevo=None, target=date(2026, 8, 4))
    assert payload["needs_you"]["empty"] is False, (
        "a target alert did not make NEEDS YOU non-empty — the section renders one line and "
        "stops when empty, so the alert would never be seen")
    text = render_digest_text(payload)
    assert expected in text
    assert "post_bad" in text
    assert "withdraw post_bad" in text          # the action vocabulary, inline


def test_a_verified_target_raises_nothing(session):
    from datetime import UTC, date, datetime

    from app.models import Post
    from app.stages.digest import build_payload

    session.add(Post(
        post_id="post_ok", channel="facebook", week_start=date(2026, 8, 3),
        status="PUBLISHED", slot="lunch", external_post_id="ext_ok",
        posted_at=datetime(2026, 8, 4, tzinfo=UTC),
        target_check={"state": "verified", "intended": ARTEC_FB, "detail": "confirmed"}))
    session.flush()
    payload = build_payload(session, brevo=None, target=date(2026, 8, 4))
    assert payload["needs_you"]["target_alerts"] == []


def test_a_withdrawn_post_stops_alerting(session):
    """Once removed at source the alert is answered; repeating it every night would train
    the operator to skip the section."""
    from datetime import UTC, date, datetime

    from app.models import Post
    from app.stages.digest import build_payload

    session.add(Post(
        post_id="post_gone", channel="linkedin", week_start=date(2026, 8, 3),
        status="PUBLISHED", slot="lunch", external_post_id="ext_gone",
        posted_at=datetime(2026, 8, 4, tzinfo=UTC),
        withdrawn_at=datetime(2026, 8, 5, tzinfo=UTC),
        withdrawn_reason="removed at source",
        target_check={"state": "mismatch", "intended": ARTEC_LI, "detail": "wrong page"}))
    session.flush()
    payload = build_payload(session, brevo=None, target=date(2026, 8, 4))
    assert payload["needs_you"]["target_alerts"] == []
