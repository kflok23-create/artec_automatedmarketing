"""D-2 · A.1 — CAPTURE THE STALE CAPABILITY CLAIMS VERBATIM, THEN REMOVE THEM.

On 2026-08-04 the agent asserted for an entire live session that the artec plugin exposes
six tools. It denied `record_metrics` four times and built a confident analysis — "the loop
is broken at measurement" — on top of that denial. Fifteen tools exist. Its own account:
it trusted a stale note instead of searching the catalog.

Memory is injected into EVERY turn, so this is not a stale note. It is a standing
instruction to misreport, and the next session it would have poisoned is the 2026-08-09
weekly gate, which needs `read_draft_posts`.

THIS SCRIPT PRINTS EVERYTHING BEFORE IT CHANGES ANYTHING, and writes the full before/after
into `config.memory_purge`. The Postgres copy is the one that matters: brain boot logs have
already been dropped once at Railway's 500/s rate limit, and evidence that lives only in a
log is evidence that can vanish. How memory accretes a false capability claim is worth more
than the deletion itself.

SELECTIVE, NOT WHOLESALE. Only lines carrying capability claims are removed. Preferences,
patterns, hypotheses and taste are what the store is FOR and they stay. Every removed line
is printed with its file, line number and the reason it matched.

Idempotent: a second run finds nothing and says so.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from audit_memory_report import (  # noqa: E402
    IMPERATIVE_RE,
    NO_SUCH_TOOL_RE,
    SCANNED_SUFFIXES,
    TOOL_COUNT_RE,
    WORD_NUMBERS,
    memory_roots,
    registered_tools,
)


def _is_capability_claim(line: str, tools: set[str]) -> str | None:
    """Why this line must go, or None to keep it.

    The two comparisons that matter both take the LIVE REGISTRY as one side. A line is
    removed for contradicting what actually exists — never for merely mentioning tools.
    """
    if not tools:
        return None                     # unknown never accuses, and never deletes
    for match in TOOL_COUNT_RE.finditer(line):
        raw = match.group(1).lower()
        claimed = WORD_NUMBERS.get(raw, int(raw) if raw.isdigit() else None)
        if claimed is not None and claimed != len(tools):
            return (f"claims {claimed} tools; the registry has {len(tools)} "
                    f"(matched {match.group(0)!r})")
    for match in NO_SUCH_TOOL_RE.finditer(line):
        named = {w.strip(" ,.") for w in match.group(1).split()}
        contradicted = sorted(named & tools)
        if contradicted:
            return f"denies tools that EXIST: {contradicted} (matched {match.group(0)[:60]!r})"
    return None


def _memory_files(home: Path) -> list[Path]:
    files: list[Path] = []
    for root in memory_roots(home):
        # skills/ excluded, matching the audit: it recurses into shipped skill packages
        # (documentation, templates, other people prose) and is not autonomously written
        # agent memory, which is what memory.write_approval: false makes dangerous.
        for target in (root / "MEMORY.md", root / "memories", root / "memory"):
            if target.is_dir():
                files += sorted(f for f in target.rglob("*")
                                if f.is_file() and f.suffix in SCANNED_SUFFIXES)
            elif target.is_file():
                files.append(target)
    # De-duplicate while keeping order: profile paths and $HERMES_HOME can overlap.
    seen, unique = set(), []
    for f in files:
        resolved = str(f.resolve())
        if resolved not in seen:
            seen.add(resolved)
            unique.append(f)
    return unique


def main() -> int:
    home_raw = os.environ.get("HERMES_HOME", "")
    if not home_raw:
        print("purge_memory_claims: HERMES_HOME unset — nothing to do", file=sys.stderr)
        return 0
    home = Path(home_raw)
    tools = registered_tools(home)
    files = _memory_files(home)

    print("=== D-2 · AGENT MEMORY, VERBATIM, BEFORE ANY CHANGE ===")
    print(f"registry: {len(tools)} tools — {sorted(tools)}")
    print(f"files found: {len(files)}")
    if not files:
        # Zero files is NOT success here either. The audit reported "clean — 0 file(s)"
        # for weeks while the claim sat in the store; a purge that finds nothing must be
        # just as loud about having looked in the wrong place.
        print("NO MEMORY FILES FOUND. This is not 'already clean' — the agent quotes its "
              "memory in sessions, so zero files means these paths are wrong.")
        print(f"searched roots: {[str(r) for r in memory_roots(home)]}")

    before: dict[str, str] = {}
    removed: list[dict] = []
    for path in files:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            print(f"  !! unreadable {path}: {e}")
            continue
        before[str(path)] = content

        kept: list[str] = []
        hit_count = 0
        for lineno, line in enumerate(content.splitlines(), start=1):
            reason = _is_capability_claim(line, tools)
            if reason is None:
                kept.append(line)
                continue
            hit_count += 1
            removed.append({"file": str(path), "line": lineno,
                            "content": line, "reason": reason})

        # DO NOT PRINT EVERY FILE. The first corrected run found 401 files and dumping them
        # all cost 74,812 dropped log messages at Railway's 500/s limit — destroying the
        # very evidence this script exists to preserve, in the same run that gathered it.
        # The warning against precisely this is in this file's own docstring. That is twice
        # in two passes that a correct diagnosis sat directly above the code that ignored it.
        #
        # The full content still reaches config.memory_purge, in Postgres, whole. What is
        # PRINTED is MEMORY.md — the block injected into every turn, the thing that caused
        # the incident — and any file that actually produced a hit. The rest is counted.
        if path.name == "MEMORY.md":
            print(f"\n--- {path} ({len(content)} chars) — INJECTED INTO EVERY TURN ---")
            print(content)
        elif hit_count:
            print(f"\n--- {path} ({len(content)} chars) — printed because it HIT ---")
            print(content)

        if len(kept) != len(content.splitlines()):
            path.write_text("\n".join(kept) + ("\n" if content.endswith("\n") else ""),
                            encoding="utf-8")

    print("\n=== REMOVED ===")
    if not removed:
        print("no capability claims found — memory holds no line contradicting the registry")
    for hit in removed:
        print(f"  {hit['file']}:{hit['line']}  {hit['reason']}")
        print(f"    | {hit['content']}")

    # Imperatives are REPORTED, never auto-removed: "always check the bank before ideating"
    # is a legitimate preference, and deleting operator-taught guidance to satisfy a linter
    # would destroy the thing the store is for. The operator decides.
    imperatives = [
        {"file": p, "line": n, "content": ln}
        for p, text_ in before.items()
        for n, ln in enumerate(text_.splitlines(), start=1)
        if IMPERATIVE_RE.search(ln) and not _is_capability_claim(ln, tools)
    ]
    if imperatives:
        print("\n=== IMPERATIVES — reported, NOT removed (operator's call) ===")
        for hit in imperatives:
            print(f"  {hit['file']}:{hit['line']}  | {hit['content']}")

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
        record = {
            "purged_at": datetime.now(UTC).isoformat(),
            "registry_tools": sorted(tools),
            "files_found": [str(p) for p in files],
            "searched_roots": [str(r) for r in memory_roots(home)],
            "before": before,
            "removed": removed,
            "imperatives_reported": imperatives,
        }
        engine = create_engine(url, pool_pre_ping=True)
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO config (key, value, updated_at) VALUES "
                     "('memory_purge', :v, :t) "
                     "ON CONFLICT (key) DO UPDATE SET value = :v, updated_at = :t")
                .bindparams(bindparam("v", type_=SAJSON())),
                {"v": record, "t": datetime.now(UTC)})
        print(f"\nrecorded to config.memory_purge ({len(removed)} removed)")
    except Exception as e:                                   # noqa: BLE001
        print(f"purge_memory_claims: could not record: {type(e).__name__}: {e}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
