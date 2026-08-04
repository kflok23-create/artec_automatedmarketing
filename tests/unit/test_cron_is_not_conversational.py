"""THE TWELVE JOBS ARE A DEPLOYMENT ARTEFACT, NOT A CONVERSATIONAL ONE.

The cron wrapper delivered on 2026-08-04 ended with:

    To stop or manage this job, send me a new message
    (e.g. "stop reminder nightly-digest").

The surface carrying touches 2–7 was advertising its own kill switch, and the instruction
is live. `stop reminder nightly-digest` removes the digest. The same capability creates a
thirteenth job, or removes job 5 — the weekly gate, the one human touch in the week.

WHY NEITHER EXISTING GUARD COVERS IT, which is the point:
  - the repo test asserting exactly twelve jobs reads `app/jobs.py`; a job created at
    runtime is invisible to it
  - the boot-time listing check runs at boot, which may be days away

Between those two, the schedule can change and nothing notices. Applying the standing
review question: for the "exactly twelve" guard, one side is `app/jobs.py` and the other
side is also `app/jobs.py`. The live cron list never entered the comparison.

What supplies each side HERE: the block list is code; the tool names are supplied by the
test as the strings a real agent would emit. The doctor check compares `app/jobs.py`
against the LIVE cron list — two genuinely different sources.
"""

from __future__ import annotations

import pytest

from plugins.artec.tools import _is_cron_mutation, pre_tool_call

# Names an agent could plausibly emit, across the naming conventions hermes-agent and MCP
# servers actually use. A block that a rename walks around is not a block.
MUTATING = [
    "cron_create", "cron.create", "cron-create", "cronCreate",
    "cron_delete", "cron_update", "cron_modify", "cron_remove",
    "create_cron", "delete_cron", "update_cron",
    "cron_stop", "cron_pause", "cron_disable", "cron_enable",
    "stop_reminder", "delete_reminder", "create_reminder",
    "schedule_create", "schedule_delete",
]

# Listing is load-bearing: verify-by-listing is what caught job 12's missing prompt file
# when `hermes cron create` exited 0 on failure. It must NOT be blocked.
READING = ["cron_list", "cron.list", "cron_status", "list_cron", "cron_show"]


@pytest.mark.parametrize("tool", MUTATING)
def test_every_cron_mutation_spelling_is_blocked(tool):
    assert _is_cron_mutation(tool) is True, f"{tool} would pass through"
    verdict = pre_tool_call(tool, {})
    assert verdict is not None and verdict["action"] == "block", (
        f"{tool} was not blocked — a chat message could change the schedule")
    assert "deployment artefact" in verdict["message"]


@pytest.mark.parametrize("tool", READING)
def test_cron_listing_is_never_blocked(tool):
    assert _is_cron_mutation(tool) is False, (
        f"{tool} is a READ. Blocking it would break verify-by-listing, which is the only "
        "thing that catches `hermes cron create` exiting 0 on failure.")
    verdict = pre_tool_call(tool, {})
    assert verdict is None or verdict.get("action") != "block"


def test_the_fifteen_artec_tools_still_pass_through():
    """The block must not catch the tools the gate and the digest depend on."""
    from plugins.artec.tools import HANDLERS

    for name in HANDLERS:
        assert _is_cron_mutation(name) is False, f"{name} was caught by the cron block"


def test_the_stop_instruction_from_the_live_wrapper_is_blocked():
    """The exact capability the 2026-08-04 message told the operator to use."""
    verdict = pre_tool_call("stop_reminder", {"name": "nightly-digest"})
    assert verdict is not None and verdict["action"] == "block"
