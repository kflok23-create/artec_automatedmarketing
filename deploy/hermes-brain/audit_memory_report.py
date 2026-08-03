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


def scan(home: Path) -> dict:
    targets = [home / "MEMORY.md", home / "memories", home / "skills"]
    files: list[Path] = []
    for target in targets:
        if target.is_dir():
            files += sorted(f for f in target.rglob("*")
                            if f.is_file() and f.suffix in SCANNED_SUFFIXES)
        elif target.is_file():
            files.append(target)

    compiled = [(label, re.compile(pattern)) for label, pattern in METRIC_PATTERNS]
    hits = []
    for path in files:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(content.splitlines(), start=1):
            for label, pattern in compiled:
                match = pattern.search(line)
                if match:
                    hits.append({"file": str(path), "line": lineno, "kind": label,
                                 "match": match.group(0)})
    return {"scanned_files": len(files), "hits": hits, "clean": not hits,
            "audited_at": datetime.now(UTC).isoformat(),
            "scanned": [str(t) for t in targets if t.exists()]}


def main() -> int:
    home = os.environ.get("HERMES_HOME", "")
    if not home:
        print("audit_memory_report: HERMES_HOME unset", file=sys.stderr)
        return 0
    result = scan(Path(home))
    if result["clean"]:
        print(f"audit-memory: clean — {result['scanned_files']} file(s) scanned, no "
              "metric-shaped content in agent memory")
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
