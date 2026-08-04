"""D-vii and D-5 — the gate states which week it is gating, and how to act on what it shows.

D-vii · `next_week_start` returns the Monday that ALREADY PASSED when called on a Sunday, so
2026-08-09's gate reads week 2026-08-03 — a week that has ended and already holds five
PUBLISHED posts. The operator decided to leave that arithmetic alone through Sunday rather
than change a date function 72 hours before it first fires. **A deferral is only a decision
if the thing deferred is visible**, so the gate opens by naming the week, the count, and the
age. If the week is wrong it is wrong ON THE SCREEN at 09:00, not discovered afterwards from
a plan that quietly never happened.

D-5 · The operator typed `Job 11 approved` at the first live digest. A reasonable thing to
type, and not a defined action — nothing in the message had said what was. The agent does not
infer operator intent any more than it originates a number.
"""

from __future__ import annotations

from datetime import date

# tools_v4 must be imported THROUGH the package — importing it first hits a pre-existing
# circular import between tools and tools_v4.
import plugins.artec.tools  # noqa: F401
from plugins.artec.tools_v4 import (
    EDITABLE_GENOME,
    GATE_ACTIONS,
    _gate_opening_summary,
    gate_vocabulary,
)


def test_the_summary_names_the_week_being_gated():
    lines = _gate_opening_summary("2026-08-03", [{"post_id": "post_1497"}],
                                  today=date(2026, 8, 9))
    assert any("week_start 2026-08-03" in line for line in lines)
    assert any("1 draft(s) found" in line for line in lines)


def test_the_exact_sunday_case_is_flagged():
    """THE D-vii CASE, on the exact day it matters.

    Week 2026-08-03 runs Mon 03 → Sun 09. Sunday's gate fires on 2026-08-09, which is DAY 6
    — the week is ending, not ended. The first version of this check tested `age >= 7` and
    so would never have fired on the one day it exists for: a guard that cannot fail on the
    case it was written for. The date is pinned here rather than read from the clock, so the
    test asserts the real scenario instead of whatever today happens to be.
    """
    lines = _gate_opening_summary("2026-08-03", [{"post_id": "post_1497"}],
                                  today=date(2026, 8, 9))
    joined = " ".join(lines)
    assert "OVER OR ENDING TODAY" in joined
    assert "day 6 of 7" in joined
    assert "DECISIONS.md 107" in joined


def test_a_week_that_is_genuinely_ahead_is_not_flagged():
    """The warning must not fire on every gate, or it stops being read. A gate run on
    Sunday 2026-08-09 for week 2026-08-10 is the CORRECT behaviour — day -1."""
    lines = _gate_opening_summary("2026-08-10", [{"post_id": "post_x"}],
                                  today=date(2026, 8, 9))
    assert "OVER OR ENDING" not in " ".join(lines)


def test_no_drafts_says_it_might_be_the_wrong_week():
    """An empty result is ambiguous between 'nobody planned' and 'wrong week'. Reporting
    only the first would be the same conflation as job 12's 'job 11 did not run'."""
    lines = _gate_opening_summary("2026-08-03", [], today=date(2026, 8, 9))
    assert any("wrong week" in line for line in lines)


def test_an_off_vocabulary_slot_is_flagged_BEFORE_approval():
    """A7 at the gate rather than after the render. `sweep_orphaned_slots` catches these
    only once RENDERED — which is after the fal spend."""
    lines = _gate_opening_summary("2026-08-03", [
        {"post_id": "post_ok", "slot_off_vocabulary": False},
        {"post_id": "post_bad", "slot_off_vocabulary": True},
    ], today=date(2026, 8, 9))
    joined = " ".join(lines)
    assert "post_bad" in joined and "post_ok" not in joined
    assert "never errors" in joined
    assert "spends render money" in joined


def test_every_action_states_its_exact_wording():
    """D-5: the operator must never have to guess the phrasing."""
    lines = gate_vocabulary()
    joined = "\n".join(lines)
    for action in ("approve", "edit", "reject", "inject"):
        assert action in GATE_ACTIONS
        assert f"{action} " in joined
    assert "approve post_1497" in joined          # a worked example, not a grammar


def test_edit_names_the_fields_it_will_overwrite():
    """`edit` states which genome fields it touches BEFORE applying, rather than the
    operator discovering afterwards which parts moved."""
    joined = "\n".join(gate_vocabulary())
    for field in EDITABLE_GENOME:
        assert field in joined


def test_an_unrecognised_reply_asks_rather_than_guesses():
    """The transcriber invariant generalised: the agent does not infer operator intent any
    more than it originates a figure."""
    joined = "\n".join(gate_vocabulary())
    assert "I will ask rather than guess" in joined
    assert "do not infer" in joined
