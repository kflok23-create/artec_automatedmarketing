"""Independent read of what the OPERATOR actually typed this session.

Amendment 1 says the agent is a transcriber, never an author. The guard that enforced it
compared the agent's `figures` against the agent's own `operator_message` argument — the
agent supplied both sides, so the check was comparing something against itself and
reporting green. Same shape as the StaticPool advisory-lock test.

This module is the other side of that comparison: hermes-agent's own message store, which
the agent does not author.

PROBED, NOT INFERRED (against a real hermes-agent install, v4 Stage 2c-i — see VERIFY.md):

    $HERMES_HOME/active_profile                       -> 'artec-brain'
    $HERMES_HOME/profiles/<profile>/state.db          SQLite, WAL mode
      sessions(id, source, started_at, …)          id e.g. '20260802_212025_9f03c9'
      messages(id, session_id, role, content, tool_name, timestamp, …)
        role ∈ {'user', 'assistant', 'tool'}
        content is TEXT — a plain string for operator turns

The first implementation looked for `$HERMES_HOME/sessions/{task_id}.jsonl`. That directory
EXISTS but holds `request_dump_*.json` — debug artefacts written only on non-retryable API
errors, containing a constructed message list. A glob fallback would have matched one when
the dump filename carried the session id, and parsed provider-format `user` entries out of
it. That is guessing wrong in the PERMISSIVE direction, so the guess is gone: there is one
source, and no fallback.

Why role matters: hermes stores tool results as role='tool', NOT as a user turn. A tool
result carrying digits therefore cannot authorise those digits. That property is the reason
this store is usable as an authority at all.
"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3

# Only this role is the operator. 'tool' and 'assistant' are the agent's own side of the
# conversation and are never a source for a figure.
OPERATOR_ROLE = "user"

# Which identifier the runtime actually passes, observed once and reported.
_OBSERVED: dict = {"logged": False, "column": None}


REQUIRED_TABLES = ("sessions", "messages")


def _has_required_tables(path: pathlib.Path) -> bool:
    """A file at the right path is not the right file. Checked because the decoy below
    OPENS CLEANLY and answers with a plausible error rather than an absence."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return False
    try:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.Error:
        return False
    finally:
        conn.close()
    return all(table in names for table in REQUIRED_TABLES)


def store_path() -> pathlib.Path | None:
    """The message store, or None if this process cannot see one.

    THE STORE IS PROFILE-SCOPED:
        $HERMES_HOME/profiles/$(cat $HERMES_HOME/active_profile)/state.db

    `$HERMES_HOME/state.db` ALSO EXISTS on the deployed brain — exactly 1048576 bytes — and
    it is a decoy in the most dangerous available form: it sits at the obvious path, it
    opens cleanly, and it answers `no such table: sessions` rather than being absent. An
    earlier probe hit it and concluded the schema was wrong.

    There is NO fallback, no glob and no search: if the profile cannot be resolved, or the
    per-profile file is missing, or the file does not carry the expected tables, this
    returns None and the caller REFUSES. Fail closed beats reading a wrong-shaped database.
    """
    explicit = os.environ.get("ARTEC_TRANSCRIPT_DB")
    if explicit:
        path = pathlib.Path(explicit)
        return path if path.is_file() and _has_required_tables(path) else None

    home = os.environ.get("HERMES_HOME")
    if not home:
        return None
    root = pathlib.Path(home)
    try:
        profile = (root / "active_profile").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not profile:
        return None
    path = root / "profiles" / profile / "state.db"
    return path if path.is_file() and _has_required_tables(path) else None


def _text_of(content) -> str:
    """Operator turns are plain strings; typed blocks are tolerated defensively."""
    if content is None:
        return ""
    if isinstance(content, str):
        stripped = content.strip()
        if stripped[:1] in ("[", "{"):
            try:
                return _text_of(json.loads(stripped))
            except json.JSONDecodeError:
                return content
        return content
    if isinstance(content, list):
        return " ".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content)
    if isinstance(content, dict):
        return str(content.get("text", ""))
    return str(content)


def operator_turns(task_id: str | None) -> list[str] | None:
    """Every message the OPERATOR sent in this session, or None if the store cannot be read
    or holds no such session.

    None is 'cannot verify' — never 'nothing to check against'. The caller REFUSES on None:
    an unverifiable transcription is not a verified one.
    """
    if not task_id:
        return None
    path = store_path()
    if path is None:
        return None
    try:
        # Read-only, and never creating: this process is a reader of somebody else's
        # database and must not be able to alter or resurrect it.
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None
    try:
        # EXACTLY TWO columns, both exact matches, no LIKE and no prefix matching. A
        # Telegram session row carries two plausible identifiers —
        #   id          = '20260803_134659_591d8efc'
        #   session_key = 'agent:main:telegram:dm:2111270140'
        # — and rather than guess which one `pre_tool_call` passes, accept either and
        # nothing else. Deterministic, not widened: both name the same row in the same
        # table, so a match cannot resolve to a different conversation.
        session = conn.execute(
            "SELECT id, session_key FROM sessions WHERE id = ? OR session_key = ?",
            (str(task_id), str(task_id))).fetchone()
        if session is None:
            # A task id that names no session cannot be vouched for.
            return None
        session_id = session[0]
        if not _OBSERVED["logged"]:
            # Once, at debug, so VERIFY.md can record WHICH identifier the runtime passes
            # instead of carrying both forever.
            matched = "id" if session[0] == str(task_id) else "session_key"
            print(f"artec transcript: task_id matched sessions.{matched}")
            _OBSERVED["logged"] = True
            _OBSERVED["column"] = matched
        # NOTE: a Telegram session is long-lived — `ended_at` is NULL while the gateway
        # runs, and the digest conversation happens inside one. Nothing here filters on it.
        rows = conn.execute(
            "SELECT content FROM messages WHERE session_id = ? AND role = ? ORDER BY id",
            (session_id, OPERATOR_ROLE)).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return [text for text in (_text_of(r[0]) for r in rows) if text]


def store_status() -> dict:
    """For boot reporting and `artec doctor` — describes what this process can see, without
    deciding policy. 'available' does not mean any particular session is verifiable."""
    path = store_path()
    if path is None:
        home = os.environ.get("HERMES_HOME", "")
        detail = "HERMES_HOME is not set"
        if home:
            root = pathlib.Path(home)
            if not (root / "active_profile").is_file():
                detail = f"{root / 'active_profile'} is missing — cannot resolve the profile"
            else:
                profile = (root / "active_profile").read_text(
                    encoding="utf-8", errors="replace").strip()
                candidate = root / "profiles" / profile / "state.db"
                detail = (f"{candidate} is missing or does not carry "
                          f"{list(REQUIRED_TABLES)}")
        return {"available": False,
                "reason": f"no message store: {detail}",
                "consequence": "record_metrics REFUSES; figures enter via `artec measure`"}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            turns = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE role = ?", (OPERATOR_ROLE,)).fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as e:
        return {"available": False, "path": str(path),
                "reason": f"store present but unreadable: {type(e).__name__}: {e}"}
    return {"available": True, "path": str(path), "sessions": sessions,
            "operator_turns": turns}
