"""THE AUDIT REPORTED CLEAN WHILE READING ZERO FILES.

First production run, 2026-08-04, verbatim:

    audit-memory: clean — 0 file(s) scanned, no metric-shaped content in agent memory

The same day, in a live session, the agent asserted the artec plugin has six tools, denied
`record_metrics` four times, and built "the loop is broken at measurement" on top of it.
Fifteen tools exist. The claim was sitting in the store the audit had just called clean.

Two independent defects, and the second is the one that makes it a non-guard:

  1. WRONG PATHS. The scan looked at $HERMES_HOME/{MEMORY.md,memories,skills}. hermes-agent
     stores memory under the ACTIVE PROFILE, $HERMES_HOME/profiles/<profile>/. Identical to
     the transcript-store decoy already documented in VERIFY.md.
  2. `clean: not hits` WAS TRUE FOR ZERO FILES. A scan of nothing reported health. One side
     of the comparison was absent and the check still passed — the standing review question
     word for word.

WHAT SUPPLIES EACH SIDE, since that is the question that has to be answerable:
  - the CLAIM comes from the agent's own memory files, which the agent writes autonomously
  - the TRUTH comes from `provides_tools:` in the plugin manifest on the volume — the file
    that determines what actually registers
Memory cannot influence the manifest and the manifest cannot influence memory, so this IS a
guard. It is a weaker one than the live catalog: the manifest is what SHOULD register, and
only a session can observe what DID. That gap is stated rather than papered over — see
test_the_manifest_is_not_the_live_catalog below.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy" / "hermes-brain"))

from audit_memory_report import memory_roots, registered_tools, scan  # noqa: E402
from purge_memory_claims import _is_capability_claim  # noqa: E402

# The claim as the agent reported holding it. Kept verbatim: a paraphrase would drift and
# the test would stop covering the thing that actually happened.
STALE_MEMORY = """\
# MEMORY

- artec plugin exposes exactly 6 tools: read_brief, read_learnings, read_asset_inventory,
  read_parked_posts, write_plan, record_gate_decision.
- There is no record_metrics tool and no render or publish tool. Don't promise those; say
  so plainly if the operator asks.
- The operator prefers short digests and dislikes hedging.
"""

MANIFEST = """\
name: artec
provides_tools:
  - read_brief
  - read_learnings
  - read_asset_inventory
  - read_parked_posts
  - write_plan
  - record_gate_decision
  - read_draft_posts
  - read_digest
  - deliver_video
  - review_video
  - review_email
  - retry_post
  - record_metrics
  - fulfil_wishlist
  - acknowledge_price_table
"""


@pytest.fixture
def brain_home(tmp_path):
    """A volume laid out the way the REAL one is: profile-scoped, with the decoy top level
    left empty — which is exactly what produced '0 file(s) scanned'."""
    (tmp_path / "active_profile").write_text("artec-brain", encoding="utf-8")
    profile = tmp_path / "profiles" / "artec-brain"
    (profile / "plugins" / "artec").mkdir(parents=True)
    (profile / "plugins" / "artec" / "plugin.yaml").write_text(MANIFEST, encoding="utf-8")
    (profile / "MEMORY.md").write_text(STALE_MEMORY, encoding="utf-8")
    return tmp_path


def test_the_profile_scoped_store_is_actually_found(brain_home):
    """The bug, directly: the old roots were $HERMES_HOME only."""
    roots = [str(r) for r in memory_roots(brain_home)]
    assert str(brain_home / "profiles" / "artec-brain") in roots


def test_the_registry_is_read_from_the_manifest_not_from_memory(brain_home):
    tools = registered_tools(brain_home)
    assert len(tools) == 15
    assert "read_draft_posts" in tools and "record_metrics" in tools


def test_the_stale_claim_is_caught(brain_home):
    result = scan(brain_home)
    assert result["scanned_files"] > 0
    assert result["clean"] is False
    kinds = {h["kind"] for h in result["hits"]}
    assert "stale capability claim" in kinds
    detail = " ".join(h.get("detail", "") for h in result["hits"])
    assert "6 tools" in detail or "says 6" in detail
    assert "record_metrics" in detail


def test_zero_files_is_never_reported_as_clean(tmp_path):
    """THE DEFECT THAT MADE IT A NON-GUARD. An empty volume must not report health."""
    result = scan(tmp_path)
    assert result["scanned_files"] == 0
    assert result["clean"] is False, (
        "a scan that read nothing reported clean — this is the exact failure that let a "
        "stale capability claim sit in the store while the audit called it healthy")
    assert result["nothing_scanned"] is True
    assert "NOT a clean result" in result["scan_warning"]
    assert result["searched"], "a zero-file result must say where it looked"


def test_the_purge_removes_claims_and_keeps_preferences(brain_home):
    tools = registered_tools(brain_home)
    lines = STALE_MEMORY.splitlines()

    removed = [ln for ln in lines if _is_capability_claim(ln, tools)]
    kept = [ln for ln in lines if not _is_capability_claim(ln, tools)]

    assert any("exactly 6 tools" in ln for ln in removed)
    assert any("no record_metrics tool" in ln for ln in removed)
    # The store exists FOR preferences. Deleting them to satisfy a linter destroys the point.
    assert any("prefers short digests" in ln for ln in kept)


def test_an_unknown_registry_never_deletes(brain_home):
    """Empty registry = unknown. Unknown must not accuse, and must never remove."""
    assert _is_capability_claim("artec plugin exposes exactly 6 tools", set()) is None


def test_a_correct_claim_is_not_removed(brain_home):
    tools = registered_tools(brain_home)
    assert _is_capability_claim("the artec plugin exposes 15 tools", tools) is None


def test_the_manifest_is_not_the_live_catalog():
    """STATED, NOT HIDDEN. `registered_tools` reads what SHOULD register. Only a session
    observes what DID. This test exists so the limitation is written down where the next
    reader finds it, rather than being discovered again in production."""
    import audit_memory_report

    src = Path(audit_memory_report.__file__).read_text(encoding="utf-8")
    assert "plugin.yaml" in src, (
        "the boot-time audit reads the MANIFEST. If this ever claims to read the live "
        "catalog, check that it is not doing so at boot — tools register at session start, "
        "and a boot-time check of session-time state is always wrong.")
