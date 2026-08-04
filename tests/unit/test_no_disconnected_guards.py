"""THE DISCONNECTED GUARD — every named guard must be reachable from production code.

Three defects in this build shared a shape that is NOT the wrong-comparison pattern. Each
guard was well written, correct in isolation, and connected to nothing:

  1. the branch-protection ruleset   enforcement disabled, `include: []` — no branches
  2. `audit_memory_report`           correct logic, pointed where memory does not live
  3. `validate_required_config`      correct logic, called by nothing outside tests/

The standing review question does not catch these. It asks what supplies each side of the
comparison, and in all three the comparison was fine — nothing ran it. The question that
catches them is different:

    IS THIS GUARD REACHABLE FROM PRODUCTION CODE AT ALL?

Each of the three was found by accident, one pass at a time, after it had already failed to
protect something real: a red Postgres suite that blocked no merge, a stale capability claim
the agent then acted on for a whole session, and a render spend cap that was never read.

This test is the mechanical version. It is deliberately crude — a symbol reference outside
`tests/` — because the failure it catches is crude. A guard can still be called from a code
path that never executes; that is a different defect and this does not claim to catch it.
What it does catch is the case where the call site does not exist at all, which is what
happened three times.

WHAT SUPPLIES EACH SIDE: the guard list is written here by hand, deliberately — a
guard-discovery heuristic would drift with naming and quietly stop covering things. The
call sites come from the source tree. Neither side can satisfy the other.
"""

from __future__ import annotations

import re

import pytest

# (symbol, what it guards, why its disconnection would be invisible)
#
# Adding a guard here is the point of the exercise. If a future guard is not in this list it
# is not covered, so the list itself is the thing to keep honest — see
# test_the_guard_list_covers_the_three_that_actually_broke.
NAMED_GUARDS: tuple[tuple[str, str, str], ...] = (
    ("validate_required_config",
     "the config manifest — refuses boot when a load-bearing key is absent",
     "every read passes a default, so a missing key looks exactly like a configured one"),
    ("audit_memory_report",
     "agent memory — numbers and stale capability claims",
     "it reported `clean` while scanning zero files"),
    ("purge_memory_claims",
     "removal of capability claims that contradict the live registry",
     "a purge nobody runs leaves the claim injected into every turn"),
    ("wait_for_schema",
     "the scheduler ticking against a schema whose migrations have not landed",
     "a missed publish slot is silent — the digest shows posts still RENDERED"),
    ("verify_publish_target",
     "that a post landed on the page it was aimed at",
     "a wrong target returns SUCCESS and is invisible until metrics never arrive"),
    ("page_targets",
     "the configured page ids, read by publish AND analytics from one source",
     "a second source is a second place to drift, invisibly"),
    ("skip_reason",
     "email and video never publishing without a recorded human approval",
     "the only irreversible surface in the system"),
    ("sweep_orphaned_slots",
     "RENDERED posts whose slot matches no slot_times key",
     "they are never selected by any pass and sit forever, silently"),
    ("sweep_expired_reviews",
     "reviews that are never answered PARK rather than auto-approving",
     "there is no expire-to-send under any framing"),
    ("digest_date_for",
     "the one place the digest key is computed, for both writer and reader",
     "two correct implementations disagreed and the digest was never delivered"),
    ("pre_tool_call",
     "metric transcription, cron mutation, and the shell/file-write families",
     "the agent could otherwise originate a figure or change the schedule from a chat"),
    ("assert_complete",
     "that a post which changed state appears somewhere in the digest",
     "a post that appears nowhere is invisible to the operator permanently"),
)

PRODUCTION_ROOTS = ("app", "plugins", "deploy", ".github")


def _production_references(repo_root, symbol: str) -> list[str]:
    """Every non-test file that names this symbol, excluding its own definition."""
    pattern = re.compile(rf"\b{re.escape(symbol)}\b")
    definition = re.compile(rf"^\s*(def|class)\s+{re.escape(symbol)}\b", re.MULTILINE)
    hits = []
    for root in PRODUCTION_ROOTS:
        base = repo_root / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in (".py", ".sh", ".yml", ".yaml"):
                continue
            if "__pycache__" in path.parts or "tests" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if not pattern.search(text):
                continue
            # A file that ONLY defines the symbol is not a call site.
            stripped = definition.sub("", text)
            if pattern.search(stripped):
                hits.append(str(path.relative_to(repo_root)))
    return hits


@pytest.mark.parametrize("symbol,guards,why", NAMED_GUARDS,
                         ids=[g[0] for g in NAMED_GUARDS])
def test_every_named_guard_has_a_production_call_site(repo_root, symbol, guards, why):
    references = _production_references(repo_root, symbol)
    assert references, (
        f"DISCONNECTED GUARD: {symbol!r} has no reference outside tests/.\n"
        f"  it guards: {guards}\n"
        f"  why that matters: {why}\n"
        f"A guard that nothing calls is not a guard. This has happened three times in this "
        f"build — the ruleset that applied to no branches, the memory audit that scanned no "
        f"files, and validate_required_config, which was called by nothing but its own "
        f"tests while its docstring said 'Called at boot'."
    )


def test_the_guard_list_covers_the_three_that_actually_broke(repo_root):
    """The list is hand-written, so the risk is that it stops being representative. These
    three are the ones with a production incident attached; they may never leave it."""
    covered = {g[0] for g in NAMED_GUARDS}
    for symbol in ("validate_required_config", "audit_memory_report", "wait_for_schema"):
        assert symbol in covered


def test_the_check_can_actually_fail(repo_root):
    """A guard that has never failed is not known to be a guard — including this one.

    Asserts the detector reports NOTHING for a symbol that exists in tests only, which is
    precisely the state `validate_required_config` was in.
    """
    assert _production_references(repo_root, "test_no_disconnected_guards") == []
    assert _production_references(repo_root, "a_symbol_that_does_not_exist_anywhere") == []


def test_declared_platform_limits_are_read_somewhere(repo_root):
    """INSTANCE #5, generalised. `PLATFORM_RULES["youtube"]["max_title"] = 100` was defined
    in two files and read in none, and post_1487 died on an HTTP 400 because of it.

    A limit that is declared and never consulted is not a rule — it is a comment that looks
    like enforcement, which is worse than no comment at all.
    """
    import re

    from app.integrations.upload_post_client import PLATFORM_RULES

    keys = {k for rules in PLATFORM_RULES.values() for k in rules}
    for key in sorted(keys):
        if key == "media":
            continue
        readers = _production_references(repo_root, key)
        assert readers, (
            f"PLATFORM_RULES declares {key!r} and nothing outside tests/ reads it. "
            "max_title was in exactly this state and cost an HTTP 400 that would have "
            "recurred on every retry.")
        assert re.search(r"\w", str(readers))
