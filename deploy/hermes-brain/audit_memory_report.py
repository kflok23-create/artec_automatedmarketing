"""A4 — run the agent-memory audit where the memory actually is, and report it to Postgres.

`memory.write_approval: false` means the agent writes memory autonomously. The standing
invariant is that NUMBERS NEVER LIVE IN AGENT MEMORY — the store is playbook only: patterns,
hypotheses, taste. `artec audit-memory` is the check for that, and it can only run on the
brain, because $HERMES_HOME is the brain's volume and artec-api cannot see it.

So the audit runs here and writes `config.memory_audit`, which the digest renders. It runs
at boot and is intended to ride job 10 (the weekly doctor sweep) when the twelve jobs are
registered in 2c-iv — the point being that autonomous memory writes are OBSERVED from the
first week rather than audited after something has already gone in.

The patterns live in app/stages/agent_review.py and are duplicated here ONLY because the
brain image does not carry the app package. A test asserts the two lists stay identical.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

METRIC_PATTERNS: tuple[tuple[str, str], ...] = (
    ("currency amount", r"(?i)([$€£¥]\s?\d[\d,.]*|\b(USD|SGD|MYR|RM|S\$)\s?\d[\d,.]*)"),
    ("percentage", r"\b\d+(\.\d+)?\s?%"),
    ("metric figure",
     r"(?i)\b(CAC|CM|CPA|ROAS|impressions?|clicks?|conversions?|saves?|shares?)\b[^.\n]{0,20}?\d"),
    ("date-stamped count", r"\b\d{4}-\d{2}-\d{2}\b[^.\n]{0,30}?\b\d+\b"),
)

SCANNED_SUFFIXES = (".md", ".txt", ".py")

# hermes-agent's MEMORY block cap, observed in the live system prompt.
MEMORY_CAP_CHARS = 2200

# ---------------------------------------------------------------------------------------
# F2 — memory that contradicts the build, and memory that gives orders.
#
# The live MEMORY block asserted "artec plugin exposes exactly 6 tools ... There is NO
# render, publish, measure ... tool ... Don't promise those; say so plainly" while the seam
# had grown to FIFTEEN. Memory is injected into EVERY turn, so at the next gate the agent
# would have been told, with authority, that capabilities it holds do not exist — and
# instructed to tell the operator so. That is not a stale note; it is a standing
# instruction to misreport.
#
# The figure patterns above would never have caught it: they look for numbers and dates,
# not for capability claims that have gone false.
# ---------------------------------------------------------------------------------------

WORD_NUMBERS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
                "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16}

TOOL_COUNT_RE = re.compile(
    r"(?i)\b(?:exactly\s+)?(\d{1,2}|" + "|".join(WORD_NUMBERS) + r")\s+tools?\b")

NO_SUCH_TOOL_RE = re.compile(
    r"(?i)\b(?:there\s+is\s+no|there\s+are\s+no|has\s+no|no)\s+"
    r"([a-z_0-9,\s]{2,120}?)\s+tools?\b")

# An imperative in memory is re-read as a directive in every later session. The agent's own
# guidance says memories are declarative facts; this catches the ones that are not.
IMPERATIVE_RE = re.compile(
    r"(?im)(?:^|[.;]\s+)(don't|do not|never|always|refuse|say so|tell the|ask the|"
    r"promise|make sure|be sure|remember to|you must|you should)\b")


def registered_tools(home: Path) -> set[str]:
    """The LIVE registry, read from the plugin manifest on the volume — the same file that
    determines what registers. Empty set = unknown, and unknown never accuses."""
    for candidate in (home / "plugins" / "artec" / "plugin.yaml",
                      *(home / "profiles").glob("*/plugins/artec/plugin.yaml")):
        try:
            text_ = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        names, collecting = set(), False
        for line in text_.splitlines():
            if line.startswith("provides_tools:"):
                collecting = True
                continue
            if collecting:
                stripped = line.strip()
                if stripped.startswith("- "):
                    names.add(stripped[2:].strip())
                elif stripped and not stripped.startswith("#"):
                    break
        if names:
            return names
    return set()


def capability_hits(path: Path, content: str, tools: set[str]) -> list[dict]:
    """Claims that contradict the live registry, and imperatives."""
    hits: list[dict] = []
    for lineno, line in enumerate(content.splitlines(), start=1):
        if tools:
            for match in TOOL_COUNT_RE.finditer(line):
                raw = match.group(1).lower()
                claimed = WORD_NUMBERS.get(raw, int(raw) if raw.isdigit() else None)
                if claimed is not None and claimed != len(tools):
                    hits.append({"file": str(path), "line": lineno,
                                 "kind": "stale capability claim",
                                 "match": match.group(0),
                                 "detail": f"memory says {claimed} tools; the registry has "
                                           f"{len(tools)}"})
            for match in NO_SUCH_TOOL_RE.finditer(line):
                named = {w.strip(" ,.") for w in match.group(1).split()}
                contradicted = sorted(named & tools)
                if contradicted:
                    hits.append({"file": str(path), "line": lineno,
                                 "kind": "stale capability claim",
                                 "match": match.group(0)[:80],
                                 "detail": f"memory denies tools that EXIST: {contradicted}"})
        for match in IMPERATIVE_RE.finditer(line):
            hits.append({"file": str(path), "line": lineno, "kind": "imperative in memory",
                         "match": match.group(1),
                         "detail": "memories are declarative facts; an imperative is re-read "
                                   "as a directive in every later session"})
    return hits


def memory_roots(home: Path) -> list[Path]:
    """Every directory agent memory can live in, PROFILE-SCOPED FIRST.

    THE AUDIT HAS NEVER SEEN A SINGLE BYTE. Its first production run, 2026-08-04, printed:

        audit-memory: clean — 0 file(s) scanned, no metric-shaped content in agent memory

    while the memory it exists to audit demonstrably held a stale capability claim — the
    agent quoted the claim back verbatim in a live session the same day. The scan looked at
    $HERMES_HOME/MEMORY.md, $HERMES_HOME/memories and $HERMES_HOME/skills. hermes-agent
    stores memory under the ACTIVE PROFILE: $HERMES_HOME/profiles/<profile>/.

    This is the identical trap already documented for the transcript store in VERIFY.md —
    "$HERMES_HOME/state.db is a decoy that opens cleanly and answers with a plausible
    error". The same decoy shape, in a different subsystem, caught the same way: by asking
    what the check actually read rather than what it reported.

    Both layouts are returned, because an installation could use either and a scanner that
    guesses wrong is what produced this defect.
    """
    roots = [home]
    active = ""
    try:
        active = (home / "active_profile").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    if active:
        roots.append(home / "profiles" / active)
    # Any other profile too: a stale claim under a profile that is not currently active is
    # still one restart away from being injected into every turn.
    profiles = home / "profiles"
    if profiles.is_dir():
        roots += [p for p in sorted(profiles.iterdir())
                  if p.is_dir() and p not in roots]
    return roots


def scan(home: Path) -> dict:
    targets: list[Path] = []
    for root in memory_roots(home):
        # `skills` is DELIBERATELY NOT HERE. The first corrected run scanned 401 files
        # because skills/ recurses into shipped skill packages — documentation, templates,
        # other people's prose. None of it is autonomously written agent memory, which is
        # what `memory.write_approval: false` makes dangerous and what this audits. Widening
        # the net until it catches everything is how a signal becomes noise.
        targets += [root / "MEMORY.md", root / "memories", root / "memory"]
    files: list[Path] = []
    for target in targets:
        if target.is_dir():
            files += sorted(f for f in target.rglob("*")
                            if f.is_file() and f.suffix in SCANNED_SUFFIXES)
        elif target.is_file():
            files.append(target)

    compiled = [(label, re.compile(pattern)) for label, pattern in METRIC_PATTERNS]
    tools = registered_tools(home)
    hits = []
    memory_chars = 0
    for path in files:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if path.name == "MEMORY.md":
            memory_chars = len(content)
        for lineno, line in enumerate(content.splitlines(), start=1):
            for label, pattern in compiled:
                match = pattern.search(line)
                if match:
                    hits.append({"file": str(path), "line": lineno, "kind": label,
                                 "match": match.group(0)})
        hits += capability_hits(path, content, tools)
    # A SCAN OF NOTHING IS NOT A CLEAN SCAN. `clean: not hits` was True when zero files were
    # read, so the audit reported health for a store it had never opened — one side of the
    # comparison absent while the check still passes, which is the standing review question
    # verbatim. Zero files is now its own state: NOT `clean`, and it says why.
    #
    # It is deliberately NOT fatal. The brain must boot; an audit that takes the service
    # down is the always-wrong-check-made-louder mistake that already caused one outage.
    # It reports, the digest carries it, and it never claims to have looked when it has not.
    nothing_scanned = not files
    return {"scanned_files": len(files), "hits": hits,
            "clean": (not hits) and not nothing_scanned,
            "nothing_scanned": nothing_scanned,
            "scan_warning": (
                "0 files scanned — the audit found no memory store to read. This is NOT a "
                "clean result: agent memory demonstrably exists (the agent quotes it), so "
                "zero files means the paths are wrong, not that the store is empty."
            ) if nothing_scanned else None,
            "audited_at": datetime.now(UTC).isoformat(),
            "registered_tools": len(tools),
            "memory_chars": memory_chars, "memory_cap": MEMORY_CAP_CHARS,
            # At the cap, NEW durable facts silently evict OLD ones with no signal. The
            # utilisation is surfaced so an eviction is visible before it happens.
            "memory_utilisation": (round(memory_chars / MEMORY_CAP_CHARS, 2)
                                   if memory_chars else 0.0),
            "scanned": [str(t) for t in targets if t.exists()],
            # Every path LOOKED AT, not only those found. When the answer is zero files the
            # only useful question is where it looked, and the old result could not say.
            "searched": [str(t) for t in targets]}


def main() -> int:
    home = os.environ.get("HERMES_HOME", "")
    if not home:
        print("audit_memory_report: HERMES_HOME unset", file=sys.stderr)
        return 0
    result = scan(Path(home))
    if result.get("nothing_scanned"):
        print("audit-memory: NO MEMORY STORE FOUND — 0 files scanned. This is NOT clean.")
        print(f"  looked in: {result['searched']}")
        print("  agent memory demonstrably exists (the agent quotes it in sessions), so "
              "zero files means these paths are wrong.")
    elif result["clean"]:
        print(f"audit-memory: clean — {result['scanned_files']} file(s) scanned, no "
              "metric-shaped content in agent memory")
        for name in result["scanned"]:
            print(f"  read: {name}")
    else:
        print(f"audit-memory: {len(result['hits'])} metric-shaped hit(s) — numbers belong "
              "in Postgres, not agent memory:")
        for hit in result["hits"][:20]:
            print(f"  {hit['file']}:{hit['line']} [{hit['kind']}] {hit['match']!r}")

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return 0
    try:
        from sqlalchemy import bindparam, create_engine, text
        from sqlalchemy.types import JSON as SAJSON

        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        engine = create_engine(url, pool_pre_ping=True)
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO config (key, value, updated_at) VALUES "
                     "('memory_audit', :v, :t) "
                     "ON CONFLICT (key) DO UPDATE SET value = :v, updated_at = :t")
                .bindparams(bindparam("v", type_=SAJSON())),
                {"v": result, "t": datetime.now(UTC)})
    except Exception as e:
        print(f"audit_memory_report: could not record: {type(e).__name__}: {e}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
