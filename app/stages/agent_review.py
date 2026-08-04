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


# ---------------------------------------------------------------------------------------
# C6 — toolset IDENTIFIER drift.
#
# `agent.disabled_toolsets` disables by NAME. If an identifier is renamed between agent
# versions, the entry silently stops matching and the toolset comes back on — the config
# still parses, still looks right, and reports nothing. `kanban` → `todo` between 0.18.x
# and 0.19.x is the real instance; the profile lists BOTH for that reason.
#
# So the check is not "is it disabled" (the config says so) but "is the name we disabled
# still a name this build recognises". A name that has vanished is the alarm.
# ---------------------------------------------------------------------------------------

EXPECTED_DISABLED = (
    "terminal", "code_execution", "file", "delegation", "video", "project",
    "todo", "kanban", "tts", "bfl", "browser", "browser-cdp", "computer_use",
    "image_gen", "video_gen",
)

# Listed in pairs that have been the same board/capability under different ids across
# versions: if EITHER member is still recognised, the capability is still covered.
ALIASES = (("todo", "kanban"), ("browser", "browser-cdp"))


def _hermes_toolset_names(runner=None) -> list[str] | None:
    """Every toolset identifier this hermes build knows. None if hermes is not installed
    here — which is a SKIP, never a pass."""
    import shutil
    import subprocess

    if runner is None:
        if shutil.which("hermes") is None:
            return None

        def runner(args):
            return subprocess.run(args, capture_output=True, text=True, timeout=120).stdout

    try:
        out = runner(["hermes", "tools", "--summary"])
    except Exception:
        return None
    names = []
    for line in (out or "").splitlines():
        token = line.strip().split()[0] if line.strip() else ""
        token = token.strip("-•*:").lower()
        if token and re.fullmatch(r"[a-z0-9_\-]+", token):
            names.append(token)
    return names or None


def toolset_drift_check(runner=None, log=print) -> dict:
    """RED if an identifier we rely on has disappeared from this build."""
    names = _hermes_toolset_names(runner)
    if names is None:
        log("agent-review: hermes not installed here — toolset drift NOT CHECKED "
            "(run on artec-brain, or wherever hermes-agent is installed)")
        return {"checked": False, "missing": [], "known": []}

    known = set(names)
    missing = []
    for expected in EXPECTED_DISABLED:
        if expected in known:
            continue
        # An alias still present means the capability is still covered by the profile.
        covered = any(expected in pair and any(other in known for other in pair)
                      for pair in ALIASES)
        if not covered:
            missing.append(expected)

    if missing:
        log(f"agent-review: RED — {len(missing)} disabled toolset identifier(s) are not "
            f"recognised by this hermes build: {', '.join(missing)}. A disabled_toolsets "
            "entry that matches nothing disables nothing — check whether it was renamed.")
    else:
        log(f"agent-review: toolset identifiers OK — all {len(EXPECTED_DISABLED)} "
            f"expected-disabled names recognised ({len(known)} toolsets in this build)")
    return {"checked": True, "missing": missing, "known": sorted(known)}


# ---------------------------------------------------------------------------------------
# B″ — the toolset lockdown must not live only on a Railway volume.
#
# The brain's entrypoint already copies `deploy/hermes-brain/config.yaml` from the image
# onto the volume on EVERY boot, so the canonical file is version-controlled and a volume
# loss cannot quietly take the security posture with it. But hermes-agent also rewrites
# that file in place (four `config.yaml.bak.*` in one day on the live volume), and between
# boots the live file can differ from the committed one with nothing saying so.
# ---------------------------------------------------------------------------------------

def config_drift_check(repo_config: Path | None = None, log=print) -> dict:
    """Compare the live profile config against the repo-committed canonical one."""
    home = _hermes_home()
    if home is None:
        log("agent-review: HERMES_HOME not set — config drift NOT CHECKED")
        return {"checked": False, "drifted": False}
    if repo_config is None:
        repo_config = Path(__file__).resolve().parents[2] / "deploy" / "hermes-brain" / "config.yaml"
    profile = ""
    marker = home / "active_profile"
    if marker.is_file():
        profile = marker.read_text(encoding="utf-8", errors="replace").strip()
    live = (home / "profiles" / profile / "config.yaml") if profile else (home / "config.yaml")
    if not live.is_file() or not repo_config.is_file():
        log(f"agent-review: config drift NOT CHECKED — {live} or {repo_config} missing")
        return {"checked": False, "drifted": False, "live": str(live)}

    live_text = live.read_text(encoding="utf-8", errors="replace")
    repo_text = repo_config.read_text(encoding="utf-8", errors="replace")
    drifted = live_text.strip() != repo_text.strip()
    if drifted:
        log(f"agent-review: RED — the live profile config at {live} differs from the "
            f"repo-committed {repo_config.name}. The toolset lockdown is a security "
            "posture; it must not live only on a volume. Redeploy to restore it from the "
            "image, or commit the intended change.")
    else:
        log("agent-review: profile config matches the repo-committed canonical file")
    return {"checked": True, "drifted": drifted, "live": str(live)}
