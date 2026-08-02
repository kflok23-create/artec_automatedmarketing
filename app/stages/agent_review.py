"""v3 guardrail 1 tooling.

`artec audit-memory` — numbers live in Postgres only; agent memory holds hypotheses,
patterns and taste, never metrics. This scans MEMORY.md and every skill file for
metric-shaped content and reports every hit. It cannot be perfect; it must be visible —
run monthly and in CI against a fixture.

`artec agent-review` — prints the current skill list and MEMORY.md for the monthly
first-Sunday session (pairs with `hermes curator`).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# Metric-shaped content: currency amounts, percentages, CAC/CM/impression figures,
# date-stamped counts.
METRIC_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("currency amount", re.compile(r"(?i)([$€£¥]\s?\d[\d,.]*|\b(USD|SGD|MYR|RM|S\$)\s?\d[\d,.]*)")),
    ("percentage", re.compile(r"\b\d+(\.\d+)?\s?%")),
    ("metric figure", re.compile(r"(?i)\b(CAC|CM|CPA|ROAS|impressions?|clicks?|conversions?|saves?|shares?)\b[^.\n]{0,20}?\d")),
    ("date-stamped count", re.compile(r"\b\d{4}-\d{2}-\d{2}\b[^.\n]{0,30}?\b\d+\b")),
)


def _hermes_home() -> Path | None:
    home = os.environ.get("HERMES_HOME", "")
    return Path(home) if home else None


def audit_memory(paths: list[Path] | None = None, log=print) -> list[dict]:
    """Scan MEMORY.md + all skill files for metric-shaped content. Returns every hit."""
    if paths is None:
        home = _hermes_home()
        if home is None:
            log("audit-memory: HERMES_HOME not set — pass --path or run on hermes-brain")
            return []
        paths = [home / "MEMORY.md", home / "skills"]

    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(sorted(f for f in p.rglob("*") if f.is_file() and f.suffix in (".md", ".txt", ".py")))
        elif p.is_file():
            files.append(p)

    hits: list[dict] = []
    for f in files:
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(content.splitlines(), start=1):
            for label, pattern in METRIC_PATTERNS:
                m = pattern.search(line)
                if m:
                    hits.append({"file": str(f), "line": lineno, "kind": label,
                                 "match": m.group(0), "text": line.strip()[:120]})

    if hits:
        log(f"audit-memory: {len(hits)} metric-shaped hit(s) — numbers belong in Postgres, "
            "not agent memory:")
        for h in hits:
            log(f"  {h['file']}:{h['line']} [{h['kind']}] {h['match']!r} — {h['text']}")
    else:
        log("audit-memory: clean — no metric-shaped content in agent memory")
    return hits


def agent_review(log=print) -> dict:
    """Print the skill list and MEMORY.md for the monthly first-Sunday session."""
    home = _hermes_home()
    if home is None or not home.exists():
        log("agent-review: HERMES_HOME not set or missing — run on hermes-brain")
        return {"skills": [], "memory": None}
    skills_dir = home / "skills"
    skills = sorted(str(p.relative_to(skills_dir)) for p in skills_dir.rglob("*")
                    if p.is_file()) if skills_dir.is_dir() else []
    log(f"skills ({len(skills)}):")
    for s in skills:
        log(f"  {s}")
    memory_path = home / "MEMORY.md"
    memory = memory_path.read_text(encoding="utf-8", errors="ignore") if memory_path.exists() else None
    log("\nMEMORY.md:" if memory else "\nMEMORY.md: <absent>")
    if memory:
        log(memory)
    log("\nreminder: run `hermes curator` for the monthly skill hygiene pass")
    return {"skills": skills, "memory": memory}
