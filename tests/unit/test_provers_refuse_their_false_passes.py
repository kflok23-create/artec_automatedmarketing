"""Four of the nine provers could report PROVEN without the capability working.

They were found by an adversarial audit asking, of each prover, the standing review
question: name what supplies each side of the comparison. In all four the prover supplied a
side itself, or one side could be wholly absent while the check still passed.

It mattered more than it would have a week ago. Since the boot sweep went unattended
(DECISIONS 121) these verdicts land in `config.proofs` every twelve hours with nobody
reading them, and `proof_status` then drops a "proven" capability off the digest's unproven
list and out of doctor's YELLOW. A false PROVEN is not a stale fact — it actively removes
the surface that would have reported the truth.

Every test below attempts the false pass and asserts it is now refused.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.stages import prove as prove_mod
from app.stages.prove import NotProvable

# --- publish-by-slot: an all-held board demonstrates withholding -------------------------

def test_a_board_where_everything_is_held_is_not_proof_of_publishing(session):
    """THE FALSE PASS. `evaluated = would_publish + held` defended only "zero posts to
    select", so an all-held board made `evaluated` large, `would_publish` EMPTY, and this
    returned ok=True with the detail "0 would publish".

    And all-held is the ORDINARY end-of-week state: `select_due_posts` filters on
    `external_post_id IS NULL`, so photo posts leave the board as they publish and what
    remains is the email and video posts held pending an approval receipt.
    """
    from app.models import Post

    session.add(Post(post_id="post_held_email", week_start=date(2026, 8, 24),
                     channel="email", status="RENDERED", slot="evening"))
    session.flush()

    with pytest.raises(NotProvable) as excinfo:
        prove_mod.run(session, "publish-by-slot", log=lambda *_: None)
    assert "HELD by a review gate" in str(excinfo.value)
    assert "not publishing" in str(excinfo.value)


def test_not_provable_here_is_not_recorded_as_a_verdict(session):
    """`run()` re-raises NotProvable BEFORE `record`, so an all-held Friday must not write
    anything — least of all overwrite a real proof from a day when something did publish."""
    from app.config import get_config
    from app.models import Post

    session.add(Post(post_id="post_held_email_2", week_start=date(2026, 8, 24),
                     channel="email", status="RENDERED", slot="evening"))
    session.flush()
    with pytest.raises(NotProvable):
        prove_mod.run(session, "publish-by-slot", log=lambda *_: None)
    assert "publish-by-slot" not in (get_config(session, "proofs", {}) or {})


def test_a_board_with_one_publishable_post_still_proves(session):
    """The fix must not swallow the real pass — one sendable post is all it ever needed."""
    from app.models import Post

    session.add(Post(post_id="post_held_email_3", week_start=date(2026, 8, 24),
                     channel="email", status="RENDERED", slot="evening"))
    session.add(Post(post_id="post_ok_photo", week_start=date(2026, 8, 24),
                     channel="instagram", status="RENDERED", slot="morning",
                     media_drive_file_id="gen_9.jpg"))
    session.flush()
    proof = prove_mod.run(session, "publish-by-slot", log=lambda *_: None)
    assert proof.ok is True
    assert proof.evidence["would_publish"] == ["post_ok_photo"]
    assert proof.evidence["held"] == ["post_held_email_3"]


# --- stripe-attribution: the prover wrote the reference it then asserted -----------------

def test_a_fabricated_client_reference_id_is_not_end_to_end_attribution(session):
    """THE FALSE PASS. The prover builds the Checkout event itself, INCLUDING
    `client_reference_id`, then asserts the webhook copied it — so the one untested link in
    the I19 chain (does artec.my's Payment Link populate that field from
    `utm_campaign=post_XXXX` at all?) was the exact link being manufactured.

    `run_all`'s docstring already said what should happen: "needs a real card purchase that
    no code can manufacture ... reports what is missing instead". It reported PROVEN.
    """
    with pytest.raises(NotProvable) as excinfo:
        prove_mod.run(session, "stripe-attribution", log=lambda *_: None)
    message = str(excinfo.value)
    assert "NO REAL STRIPE ORDER" in message
    assert "utm_campaign" in message          # names the link that is actually untested


def test_a_real_stripe_order_proves_it(session):
    """One real order, arriving from a real card with a reference this code did not write,
    is the whole difference. The webhook join is still verified first — the proof is both
    halves, not either."""
    from app.models import Order, Post

    session.add(Post(post_id="post_real_sg", week_start=date(2026, 8, 24),
                     channel="instagram", status="PUBLISHED"))
    session.flush()
    session.add(Order(source="stripe", external_id="cs_live_real", post_id="post_real_sg",
                      amount_minor=14900, currency="SGD", occurred_at=datetime.now(UTC)))
    session.flush()

    proof = prove_mod.run(session, "stripe-attribution", log=lambda *_: None)
    assert proof.ok is True
    assert proof.evidence["real_post_id"] == "post_real_sg"


# --- brevo-send: a measured outcome that never entered the comparison --------------------

def _install_brevo(monkeypatch, delete_ok: bool):
    """The real `Brevo` class, swapped where the prover imports it. `FakeBrevo` already
    carries a template with all six variables, two of them spaced `{{ mirror }}` — the
    whitespace form that broke the skeleton check once."""
    from app.integrations import brevo_client
    from app.integrations.fakes import FakeBrevo

    class _Stub(FakeBrevo):
        def __init__(self, settings=None):
            super().__init__()

        def delete_campaign(self, campaign_id: int) -> bool:
            return delete_ok

    monkeypatch.setattr(brevo_client, "Brevo", _Stub)


def test_a_failed_delete_fails_the_proof_and_names_the_campaign(session, monkeypatch):
    """THE FALSE PASS. `ok` was the literal `True`; `deleted` reached only the detail string.

    The campaign is created on the PRODUCTION account against the live consumer list, so a
    refused DELETE left a send-ready campaign aimed at every subscriber while the proof said
    PROVEN. `delete_campaign`'s own docstring names the consequence: "eventually get one
    sent by accident." Unattended, these accumulate at up to one per api boot.
    """
    _install_brevo(monkeypatch, delete_ok=False)
    proof = prove_mod.run(session, "brevo-send", log=lambda *_: None)
    assert proof.ok is False
    assert "NOT deleted" in proof.detail
    assert proof.evidence["action_required"] == "delete campaign in Brevo"
    assert proof.evidence["campaign_id"] is not None      # says WHICH one to remove


def test_a_clean_create_and_delete_still_proves(session, monkeypatch):
    _install_brevo(monkeypatch, delete_ok=True)
    proof = prove_mod.run(session, "brevo-send", log=lambda *_: None)
    assert proof.ok is True and proof.evidence["deleted"] is True


# --- video-pipeline: pre-flighted in a configuration no channel uses ---------------------

def test_the_preflight_spec_is_read_from_channel_media_not_from_literals(session):
    """THE FALSE PASS. The prover passed `aspect_ratio="16:9"` and
    `duration_bounds=(1.0, 10.0)`. Production reads both from `channel_media`: the only two
    video channels are 9:16 with bounds derived from `duration_s` (tiktok 6-24s, youtube
    7.5-30s). So the check ran in a configuration that CANNOT OCCUR, and the bytes that made
    it green are bytes production would park twice over.

    ASSERTED BEHAVIOURALLY, not by scanning the source for absent literals. The first
    version of this test grepped `prove.py` and tripped on the literal inside the comment
    that EXPLAINS the defect — a guard defeated by its own documentation, which would have
    forced the explanation to be deleted to make the test pass.

    THE SAME FIXTURE, TWO CONFIGS, OPPOSITE VERDICTS. That is only possible if config
    genuinely supplies one side of the comparison; a prover holding the spec in literals
    would return the same answer to both.
    """
    from app.config import set_config

    # The committed fixture is 1920x1080 @ 3.0s — i.e. 16:9.
    set_config(session, "channel_media",
               {"tiktok": {"media": "video", "aspect_ratio": "1:1", "duration_s": 4}},
               set_by="test")
    square = prove_mod.run(session, "video-pipeline", log=lambda *_: None)
    assert square.ok is False, "a 16:9 fixture cannot satisfy a 1:1 channel"
    assert "tiktok" in square.detail

    set_config(session, "channel_media",
               {"tiktok": {"media": "video", "aspect_ratio": "16:9", "duration_s": 4}},
               set_by="test")
    wide = prove_mod.run(session, "video-pipeline", log=lambda *_: None)
    assert wide.ok is True, f"the same fixture must pass a 16:9 channel: {wide.detail}"
    assert wide.evidence["checks"]["tiktok"]["duration_bounds"] == (2.0, 8.0), (
        "the bounds must be derived from duration_s, not from a literal")


def test_a_config_with_no_video_channel_is_not_provable(session):
    """If nothing is configured for video there is no real contract to prove against, and
    inventing one is how this came to pass on a file production would reject."""
    from app.config import set_config

    set_config(session, "channel_media", {"instagram": {"media": "photo"}}, set_by="test")
    with pytest.raises(NotProvable) as excinfo:
        prove_mod.run(session, "video-pipeline", log=lambda *_: None)
    assert "no channel" in str(excinfo.value)


def test_a_video_channel_missing_duration_s_is_reported_not_defaulted(session):
    """A DEFAULT IS A FALLBACK, NOT A SETTING. Substituting a duration here would rebuild
    exactly the defect being fixed — bounds nobody configured."""
    from app.config import set_config

    set_config(session, "channel_media",
               {"tiktok": {"media": "video", "aspect_ratio": "9:16"}}, set_by="test")
    proof = prove_mod.run(session, "video-pipeline", log=lambda *_: None)
    assert proof.ok is False
    assert "no duration_s" in proof.detail


# --- sweep_orphaned_slots: a literal where the rest of the system uses the constant ------

def test_an_approved_post_on_an_orphaned_slot_is_reported(session):
    """`sweep_orphaned_slots` matched the literal "RENDERED" while `select_due_posts` uses
    PUBLISHABLE_STATUSES, which also holds APPROVED_TO_SEND. So an APPROVED post on a slot
    matching no `slot_times` key was invisible to the only A7 guard: never selected, never
    reported, shown in the digest as queued for delivery.

    Approval is exactly when a post is most likely to sit on an orphaned slot, because it is
    when the operator is most likely to have just edited slot_times.
    """
    from app.models import Post
    from app.scheduler import sweep_orphaned_slots

    session.add(Post(post_id="post_orphan_approved", week_start=date(2026, 8, 24),
                     channel="email", status="APPROVED_TO_SEND", slot="midnight"))
    session.add(Post(post_id="post_orphan_rendered", week_start=date(2026, 8, 24),
                     channel="instagram", status="RENDERED", slot="midnight"))
    session.flush()

    orphans = {o["post_id"] for o in sweep_orphaned_slots(session)}
    assert orphans == {"post_orphan_approved", "post_orphan_rendered"}


def test_a_post_on_a_real_slot_is_not_an_orphan(session):
    """The sweep must not start reporting the whole board as orphaned."""
    from app.models import Post
    from app.scheduler import sweep_orphaned_slots

    session.add(Post(post_id="post_fine", week_start=date(2026, 8, 24),
                     channel="instagram", status="APPROVED_TO_SEND", slot="evening"))
    session.flush()
    assert sweep_orphaned_slots(session) == []
