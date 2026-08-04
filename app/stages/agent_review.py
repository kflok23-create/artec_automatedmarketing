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


# F2 — the same two detectors the brain-side audit runs. Duplicated because the brain image
# carries no app package; a test asserts the pattern lists stay identical, because
# duplicated is fine and silently divergent is not.
MEMORY_CAP_CHARS = 2200

WORD_NUMBERS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
                "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16}

CAPABILITY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("stale capability claim",
     r"(?i)\b(?:exactly\s+)?(\d{1,2}|" + "|".join(WORD_NUMBERS) + r")\s+tools?\b"),
    ("stale capability claim",
     r"(?i)\b(?:there\s+is\s+no|there\s+are\s+no|has\s+no|no)\s+"
     r"([a-z_0-9,\s]{2,120}?)\s+tools?\b"),
    ("imperative in memory",
     r"(?im)(?:^|[.;]\s+)(don't|do not|never|always|refuse|say so|tell the|ask the|"
     r"promise|make sure|be sure|remember to|you must|you should)\b"),
)


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


# ---------------------------------------------------------------------------------------
# D2 — the merge gate is a WRITTEN RULE, so it needs somewhere to be visible.
#
# Branch protection is unavailable on this plan (HTTP 403: "Upgrade to GitHub Pro or make
# this repository public") and the repo stays private, so condition 5 cannot be enforced by
# GitHub. The rule stands anyway:
#
#   NO MERGE TO `main` WITHOUT A GREEN CI RUN ON THE EXACT COMMIT BEING MERGED — every
#   `pg` test EXECUTED, not skipped.
#
# A rule nobody can see is a rule nobody keeps, so this checks it after the fact: does
# main's current HEAD have a successful CI run? It cannot PREVENT a bad merge — nothing
# available on this plan can — but it makes one visible the next time anybody looks.
#
# "Cannot check" is reported as NOT CHECKED and never as a pass, matching the toolset
# drift check: an unverifiable rule is not a kept one.
# ---------------------------------------------------------------------------------------

GITHUB_API = "https://api.github.com"
DEFAULT_REPO = "kflok23-create/artec_automatedmarketing"


def main_ci_gate_check(fetch=None, repo: str | None = None, log=print) -> dict:
    """Whether main's HEAD carries a green CI run. `fetch(url) -> dict | None` is injected
    by tests; in production it is an authenticated GitHub API GET."""
    repo = repo or os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPO

    if fetch is None:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
        if not token:
            log("agent-review: merge-gate NOT CHECKED — no GITHUB_TOKEN, and this repo is "
                "private. The written rule still stands: no merge to main without a green "
                "CI run on the exact commit being merged.")
            return {"checked": False, "green": False, "reason": "no GITHUB_TOKEN"}

        def fetch(url: str):
            import httpx

            response = httpx.get(url, timeout=30, headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json"})
            return response.json() if response.status_code == 200 else None

    try:
        head = fetch(f"{GITHUB_API}/repos/{repo}/commits/main")
        sha = (head or {}).get("sha")
        if not sha:
            log("agent-review: merge-gate NOT CHECKED — could not resolve main's HEAD")
            return {"checked": False, "green": False, "reason": "HEAD unresolved"}
        runs = fetch(f"{GITHUB_API}/repos/{repo}/actions/runs?head_sha={sha}") or {}
    except Exception as e:                                    # noqa: BLE001
        log(f"agent-review: merge-gate NOT CHECKED — {type(e).__name__}: {e}")
        return {"checked": False, "green": False, "reason": f"{type(e).__name__}"}

    workflow_runs = runs.get("workflow_runs") or []
    if not workflow_runs:
        # The dangerous case, and the one the rule exists to catch: a commit on main that
        # CI never ran against at all.
        log(f"agent-review: RED — main HEAD {sha[:7]} has NO CI run. The written merge rule "
            "requires a green run on the exact commit being merged.")
        return {"checked": True, "green": False, "sha": sha, "run_id": None,
                "reason": "no CI run for this commit"}

    latest = workflow_runs[0]
    green = latest.get("conclusion") == "success"
    if green:
        log(f"agent-review: merge gate held — main HEAD {sha[:7]} has a green CI run "
            f"({latest.get('id')})")
    else:
        log(f"agent-review: RED — main HEAD {sha[:7]} CI run {latest.get('id')} concluded "
            f"{latest.get('conclusion')!r}, not 'success'.")
    return {"checked": True, "green": green, "sha": sha, "run_id": latest.get("id"),
            "reason": latest.get("conclusion")}
