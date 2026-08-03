"""Independent read of what the OPERATOR actually typed this session.

Amendment 1 says the agent is a transcriber, never an author. The guard that enforced it
compared the agent's `figures` against the agent's own `operator_message` argument — the
agent supplied both sides, so the check was comparing something against itself and
reporting green. Same shape as the StaticPool advisory-lock test.

This module is the other side of the comparison: the session transcript, read from
hermes-agent's own store, which the agent does not author. Digits are verified against
messages whose role is the OPERATOR's.

WHAT IS AND IS NOT VERIFIED HERE:
  * The store LAYOUT is not verified against a live hermes-agent — VERIFY.md carries no
    such fact, and inventing one is the failure class this project keeps hitting. So this
    module DISCOVERS the store at runtime across the plausible layouts and returns None
    when it finds nothing.
  * `None` means "cannot verify", and the hook treats that as REFUSE, not as pass. An
    unverifiable transcription is refused with the fallback named (`artec measure`), which
    is loud. The alternative — assuming the transcript is absent means the agent is honest
    — is how the circular check got here.
"""

from __future__ import annotations

import json
import os
import pathlib

# Roles hermes-agent may use for the human side. Anything not in this set is NOT the
# operator: an assistant turn quoting a figure back must never count as a source.
OPERATOR_ROLES = frozenset({"user", "human", "operator"})

MAX_SCAN_FILES = 200          # bounded: a transcript store is not a filesystem crawl


def _store_roots() -> list[pathlib.Path]:
    roots = []
    override = os.environ.get("ARTEC_TRANSCRIPT_DIR")
    if override:
        roots.append(pathlib.Path(override))
    home = os.environ.get("HERMES_HOME")
    if home:
        base = pathlib.Path(home)
        profile = os.environ.get("HERMES_PROFILE", "artec-brain")
        roots += [base / "sessions", base / "history",
                  base / "profiles" / profile / "sessions",
                  base / "profiles" / profile / "history"]
    return [r for r in roots if r.is_dir()]


def _candidate_files(task_id: str) -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for root in _store_roots():
        for suffix in (".jsonl", ".json", ".ndjson"):
            direct = root / f"{task_id}{suffix}"
            if direct.is_file():
                files.append(direct)
        if not files:
            for path in sorted(root.glob(f"*{task_id}*"))[:MAX_SCAN_FILES]:
                if path.is_file():
                    files.append(path)
    return files


def _text_of(content) -> str:
    """Message content is a string, or a list of typed blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    if isinstance(content, dict):
        return str(content.get("text", ""))
    return ""


def _turns_from_object(obj) -> list[str]:
    if isinstance(obj, dict) and isinstance(obj.get("messages"), list):
        obj = obj["messages"]
    if not isinstance(obj, list):
        obj = [obj]
    out = []
    for entry in obj:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role") or entry.get("sender") or entry.get("author") or "")
        if role.lower() in OPERATOR_ROLES:
            text = _text_of(entry.get("content", entry.get("text", "")))
            if text:
                out.append(text)
    return out


def operator_turns(task_id: str | None) -> list[str] | None:
    """Every message the OPERATOR sent in this session, or None if no transcript store
    could be read. None is 'cannot verify' — never 'nothing to check against'."""
    if not task_id:
        return None
    files = _candidate_files(str(task_id))
    if not files:
        return None
    turns: list[str] = []
    found = False
    for path in files:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        found = True
        try:
            turns += _turns_from_object(json.loads(raw))
            continue
        except json.JSONDecodeError:
            pass
        for line in raw.splitlines():          # jsonl
            line = line.strip()
            if not line:
                continue
            try:
                turns += _turns_from_object(json.loads(line))
            except json.JSONDecodeError:
                continue
    return turns if found else None


def available() -> bool:
    """Whether a transcript store exists at all — for boot reporting, not for policy."""
    return bool(_store_roots())
