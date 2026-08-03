"""The six tool handlers — documented contract per
https://hermes-agent.nousresearch.com/docs/developer-guide/plugins:

    def handler(args: dict, **kwargs) -> str

A JSON STRING is returned ALWAYS — success and error — and handlers never raise
(exceptions break the agent's tool loop). Every handler wraps its implementation in the
same envelope: {"ok": true, "data": ...} or {"ok": false, "error": ...}.

Self-contained on purpose: sqlalchemy textual SQL only, never the artec app package.
There is no handler that writes orders, events, metrics or config — the capability
boundary is the security model. Every call logs to `runs` with its arguments.

Tests inject an engine via kwargs (the **kwargs channel the contract mandates for
forward compatibility); in production the engine comes from DATABASE_URL.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.types import JSON as SAJSON

PLAN_FIELDS = ("channel", "angle", "hook", "cta_type", "cta_placement", "keywords", "slot")
EDITABLE_FIELDS = ("angle", "hook", "cta_type", "cta_placement", "slot", "caption", "keywords")

_engine: Engine | None = None


def _get_engine(engine: Engine | None = None) -> Engine:
    global _engine
    if engine is not None:
        return engine
    if _engine is None:
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            raise RuntimeError("DATABASE_URL is not set")
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        _engine = create_engine(url, pool_pre_ping=True)
    return _engine


def _jtext(sql: str, *json_params: str):
    """Textual SQL with JSON-typed bind params — portable across sqlite JSON / pg JSONB."""
    stmt = text(sql)
    if json_params:
        stmt = stmt.bindparams(*[bindparam(p, type_=SAJSON()) for p in json_params])
    return stmt


def _log_run(conn, tool: str, args: dict) -> None:
    conn.execute(
        _jtext("INSERT INTO runs (command, args, started_at, finished_at, status, log) "
               "VALUES (:c, :a, :t, :t, 'ok', :l)", "a", "l"),
        {"c": f"agent-tool {tool}", "a": args, "t": datetime.now(UTC), "l": []},
    )


def _config(conn, key: str, default: Any = None) -> Any:
    row = conn.execute(text("SELECT value FROM config WHERE key = :k"), {"k": key}).first()
    if row is None:
        return default
    value = row[0]
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


# ---------------------------------------------------------------------------------------
# implementations — may raise; the handler wrappers below convert everything to JSON
# ---------------------------------------------------------------------------------------

def _read_brief_impl(engine: Engine | None = None) -> str:
    eng = _get_engine(engine)
    with eng.begin() as conn:
        _log_run(conn, "read_brief", {})
        lines = [f"[{s}] {line}" for s, line in
                 conn.execute(text("SELECT section, line FROM v_brief")).all()]

        lines.append("")
        lines.append("== REVENUE (orders only — money never mixes with engagement) ==")
        rev = conn.execute(text(
            "SELECT currency, COUNT(*), COALESCE(SUM(amount_minor), 0) FROM orders "
            "WHERE post_id IS NOT NULL GROUP BY currency")).all()
        if rev:
            for cur, n, amt in rev:
                lines.append(f"  {cur}: {n} attributed orders, {amt} minor units gross")
        else:
            lines.append("  no attributed orders yet")
        ua = conn.execute(text("SELECT COUNT(*) FROM orders WHERE post_id IS NULL")).scalar()
        lines.append(f"  UNATTRIBUTED: {ua} orders")

        lines.append("")
        lines.append("== ENGAGEMENT (events + metrics only — separate lane, never summed with revenue) ==")
        measured = conn.execute(text(
            "SELECT post_id, SUM(COALESCE(impressions, 0)), SUM(COALESCE(clicks, 0)) "
            "FROM metrics GROUP BY post_id ORDER BY post_id LIMIT 10")).all()
        if measured:
            for pid, imp, clicks in measured:
                lines.append(f"  {pid}: impressions={imp} clicks={clicks}")
        else:
            lines.append("  nothing measured yet (unmeasured stays NULL — it is not zero)")
        unmeasured = conn.execute(text(
            "SELECT COUNT(*) FROM posts p WHERE p.status = 'PUBLISHED' AND NOT EXISTS "
            "(SELECT 1 FROM metrics m WHERE m.post_id = p.post_id)")).scalar()
        lines.append(f"  unmeasured published posts: {unmeasured}")
    return "\n".join(lines)


def _read_learnings_impl(week_start: str, engine: Engine | None = None) -> list[dict]:
    eng = _get_engine(engine)
    with eng.begin() as conn:
        _log_run(conn, "read_learnings", {"week_start": week_start})
        rows = conn.execute(text(
            "SELECT lever, lever_value, kpi, score, sample_size, verdict FROM learnings "
            "WHERE week_start = :w ORDER BY lever, lever_value"), {"w": week_start}).all()
    return [{"lever": r[0], "lever_value": r[1], "kpi": r[2],
             "score": float(r[3]) if r[3] is not None else None,
             "sample_size": r[4], "verdict": r[5]} for r in rows]


def _read_asset_inventory_impl(engine: Engine | None = None) -> list[dict]:
    eng = _get_engine(engine)
    with eng.begin() as conn:
        _log_run(conn, "read_asset_inventory", {})
        rows = conn.execute(text(
            "SELECT subject, medium, COUNT(*), "
            "SUM(CASE WHEN times_used = 0 THEN 1 ELSE 0 END) "
            "FROM assets WHERE status = 'active' GROUP BY subject, medium "
            "ORDER BY subject, medium")).all()
    return [{"subject": r[0], "medium": r[1], "count": r[2], "unused": r[3]} for r in rows]


def _read_parked_posts_impl(engine: Engine | None = None) -> list[dict]:
    eng = _get_engine(engine)
    with eng.begin() as conn:
        _log_run(conn, "read_parked_posts", {})
        rows = conn.execute(text(
            "SELECT post_id, channel, hook, park_reason, asset_wishlist FROM posts "
            "WHERE status = 'PARKED' ORDER BY post_id")).all()
    out = []
    for pid, channel, hook, reason, wishlist in rows:
        if isinstance(wishlist, str):
            try:
                wishlist = json.loads(wishlist)
            except (TypeError, json.JSONDecodeError):
                wishlist = []
        out.append({"post_id": pid, "channel": channel, "hook": hook,
                    "park_reason": reason, "asset_wishlist": wishlist or []})
    return out


def _write_plan_impl(week_start: str, posts: list[dict], engine: Engine | None = None) -> dict:
    eng = _get_engine(engine)
    week = date.fromisoformat(str(week_start))
    created_posts: list[str] = []
    shadow_rows = 0
    with eng.begin() as conn:
        _log_run(conn, "write_plan", {"week_start": week_start, "posts": len(posts)})
        plan_source = _config(conn, "plan_source", "shadow")
        if plan_source == "bespoke":
            return {"disabled": True, "plan_source": "bespoke", "written": 0}

        for item in posts:
            fields = {k: item.get(k) for k in PLAN_FIELDS}
            slot = fields.get("slot") or "evening"
            channel = fields.get("channel")
            if not channel:
                continue

            exists_shadow = conn.execute(text(
                "SELECT id FROM plans_shadow WHERE week_start = :w AND channel = :c "
                "AND slot = :s AND source = 'agent'"),
                {"w": week, "c": channel, "s": slot}).first()
            if exists_shadow is None:
                conn.execute(
                    _jtext("INSERT INTO plans_shadow (week_start, channel, angle, hook, "
                           "cta_type, cta_placement, keywords, slot, source, created_at) "
                           "VALUES (:w, :c, :angle, :hook, :cta_type, :cta_placement, "
                           ":kw, :s, 'agent', :t)", "kw"),
                    {"w": week, "c": channel, "angle": fields.get("angle"),
                     "hook": fields.get("hook"), "cta_type": fields.get("cta_type"),
                     "cta_placement": fields.get("cta_placement"),
                     "kw": fields.get("keywords") or [], "s": slot,
                     "t": datetime.now(UTC)})
                shadow_rows += 1

            if plan_source == "agent":
                exists_post = conn.execute(text(
                    "SELECT post_id FROM posts WHERE week_start = :w AND channel = :c "
                    "AND slot = :s AND plan_source = 'agent' AND status != 'REJECTED'"),
                    {"w": week, "c": channel, "s": slot}).first()
                if exists_post is not None:
                    continue
                n = int(_config(conn, "post_id_counter", 1482))
                conn.execute(
                    _jtext("UPDATE config SET value = :v WHERE key = 'post_id_counter'", "v"),
                    {"v": n + 1})
                post_id = f"post_{n}"
                medium = "email" if channel == "email" else "organic"
                site = _config(conn, "site_base_url", "https://artec.my")
                code = _config(conn, "email_code" if medium == "email" else "social_code", "SOCIAL50")
                tracked = (f"{str(site).rstrip('/')}/?code={code}&utm_source={channel}"
                           f"&utm_medium={medium}&utm_campaign={post_id}")
                conn.execute(
                    _jtext("INSERT INTO posts (post_id, week_start, channel, status, angle, "
                           "hook, cta_type, cta_placement, keywords, slot, code, utm, "
                           "tracked_url, plan_source, created_at, updated_at) VALUES "
                           "(:pid, :w, :c, 'DRAFT', :angle, :hook, :cta_type, "
                           ":cta_placement, :kw, :s, :code, :utm, :turl, 'agent', :t, :t)",
                           "kw", "utm"),
                    {"pid": post_id, "w": week, "c": channel, "angle": fields.get("angle"),
                     "hook": fields.get("hook"), "cta_type": fields.get("cta_type"),
                     "cta_placement": fields.get("cta_placement"),
                     "kw": fields.get("keywords") or [], "s": slot, "code": code,
                     "utm": {"utm_source": channel, "utm_medium": medium,
                             "utm_campaign": post_id},
                     "turl": tracked, "t": datetime.now(UTC)})
                created_posts.append(post_id)
    return {"plan_source": plan_source, "post_ids": created_posts, "shadow_rows": shadow_rows}


def _inject_new_post(conn, edits: dict | None) -> dict:
    """v4 (closes A2): `inject` with post_id=null CREATES a post. Injection is a first-class
    path — the operator having a brand-new idea at the gate is normal, not an error."""
    fields = {k: (edits or {}).get(k) for k in PLAN_FIELDS}
    channel = fields.get("channel") or "instagram"
    slot = fields.get("slot") or "evening"
    # A7: an off-vocabulary slot would never publish and never error. Reject at write time.
    slot_times = _config(conn, "slot_times", {}) or {}
    if slot not in slot_times:
        raise ValueError(
            f"slot {slot!r} is not a key of slot_times {sorted(slot_times)} — a post with an "
            "unknown slot would never publish and never error"
        )
    n = int(_config(conn, "post_id_counter", 1482))
    conn.execute(_jtext("UPDATE config SET value = :v WHERE key = 'post_id_counter'", "v"),
                 {"v": n + 1})
    post_id = f"post_{n}"
    medium = "email" if channel == "email" else "organic"
    site = _config(conn, "site_base_url", "https://artec.my")
    code = _config(conn, "email_code" if medium == "email" else "social_code", "SOCIAL50")
    tracked = (f"{str(site).rstrip('/')}/?code={code}&utm_source={channel}"
               f"&utm_medium={medium}&utm_campaign={post_id}")
    now = datetime.now(UTC)
    conn.execute(
        _jtext("INSERT INTO posts (post_id, week_start, channel, status, angle, hook, "
               "cta_type, cta_placement, keywords, slot, code, utm, tracked_url, "
               "plan_source, gate_action, created_at, updated_at) VALUES "
               "(:pid, :w, :c, 'APPROVED', :angle, :hook, :cta_type, :cta_placement, :kw, "
               ":s, :code, :utm, :turl, 'agent', :g, :t, :t)", "kw", "utm", "g"),
        {"pid": post_id, "w": date.today(), "c": channel, "angle": fields.get("angle"),
         "hook": fields.get("hook"), "cta_type": fields.get("cta_type") or "discount",
         "cta_placement": fields.get("cta_placement") or "caption_end",
         "kw": fields.get("keywords") or [], "s": slot, "code": code,
         "utm": {"utm_source": channel, "utm_medium": medium, "utm_campaign": post_id},
         "turl": tracked,
         "g": {"action": "inject", "edits": edits or {}, "at": now.isoformat()},
         "t": now})
    return {"post_id": post_id, "action": "inject", "status": "APPROVED", "created": True}


def _record_gate_decision_impl(post_id: str | None, action: str, edits: dict | None = None,
                               engine: Engine | None = None) -> dict:
    if action not in ("approve", "edit", "reject", "inject"):
        raise ValueError(f"unknown gate action {action!r}")
    eng = _get_engine(engine)
    with eng.begin() as conn:
        _log_run(conn, "record_gate_decision", {"post_id": post_id, "action": action,
                                                "edits": edits or {}})
        if action == "inject" and not post_id:
            return _inject_new_post(conn, edits)
        row = conn.execute(text(
            "SELECT status, gate_action FROM posts WHERE post_id = :p"), {"p": post_id}).first()
        if row is None:
            return {"post_id": post_id, "error": "not found"}
        _status, existing = row
        if existing:  # idempotent: the first decision stands
            parsed = json.loads(existing) if isinstance(existing, str) else existing
            if parsed and parsed.get("action"):
                return {"post_id": post_id, "already": parsed.get("action")}

        gate_payload = {"action": action, "edits": edits or {},
                        "at": datetime.now(UTC).isoformat()}
        new_status = "REJECTED" if action == "reject" else "APPROVED"
        if action == "edit":
            sets, params = [], {"p": post_id}
            for field, value in (edits or {}).items():
                if field in EDITABLE_FIELDS and field != "keywords":
                    sets.append(f"{field} = :{field}")
                    params[field] = value
            if sets:
                conn.execute(text(f"UPDATE posts SET {', '.join(sets)} WHERE post_id = :p"), params)
            if "keywords" in (edits or {}):
                conn.execute(_jtext("UPDATE posts SET keywords = :kw WHERE post_id = :p", "kw"),
                             {"kw": edits["keywords"], "p": post_id})
        conn.execute(
            _jtext("UPDATE posts SET status = :s, gate_action = :g WHERE post_id = :p", "g"),
            {"s": new_status, "g": gate_payload, "p": post_id})
    return {"post_id": post_id, "action": action, "status": new_status}


# ---------------------------------------------------------------------------------------
# handlers — the documented contract: (args: dict, **kwargs) -> str, JSON always, no raise
# ---------------------------------------------------------------------------------------

def _envelope(fn, args: dict, **kwargs) -> str:
    try:
        data = fn(args, **kwargs)
        return json.dumps({"ok": True, "data": data}, default=str)
    except Exception as e:  # never raise — exceptions break the agent's tool loop
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, default=str)


def read_brief(args: dict, **kwargs) -> str:
    return _envelope(lambda a, **kw: _read_brief_impl(engine=kw.get("engine")), args, **kwargs)


def read_learnings(args: dict, **kwargs) -> str:
    return _envelope(
        lambda a, **kw: _read_learnings_impl(str(a["week_start"]), engine=kw.get("engine")),
        args, **kwargs)


def read_asset_inventory(args: dict, **kwargs) -> str:
    return _envelope(lambda a, **kw: _read_asset_inventory_impl(engine=kw.get("engine")),
                     args, **kwargs)


def read_parked_posts(args: dict, **kwargs) -> str:
    return _envelope(lambda a, **kw: _read_parked_posts_impl(engine=kw.get("engine")),
                     args, **kwargs)


def write_plan(args: dict, **kwargs) -> str:
    return _envelope(
        lambda a, **kw: _write_plan_impl(str(a["week_start"]), list(a["posts"]),
                                         engine=kw.get("engine")),
        args, **kwargs)


def record_gate_decision(args: dict, **kwargs) -> str:
    return _envelope(
        lambda a, **kw: _record_gate_decision_impl(
            a.get("post_id") or None,            # null is legal: inject creates a post
            str(a["action"]), a.get("edits"), engine=kw.get("engine")),
        args, **kwargs)


HANDLERS: dict[str, Any] = {
    "read_brief": read_brief,
    "read_learnings": read_learnings,
    "read_asset_inventory": read_asset_inventory,
    "read_parked_posts": read_parked_posts,
    "write_plan": write_plan,
    "record_gate_decision": record_gate_decision,
}

# v4 adds nine: read_draft_posts, read_digest, deliver_video, review_video, review_email,
# record_metrics, retry_post, fulfil_wishlist, acknowledge_price_table → FIFTEEN total.
# Imported at the END so tools_v4's back-import resolves against a fully defined module.
# Loaded tolerantly because this file runs BOTH as a package member (in the agent) and as a
# bare file load (tests, diagnostics) — the same dual-context problem __init__.py solves.
def _load_v4():
    try:
        from importlib import import_module

        return import_module(".tools_v4", package=__package__ or "artec")
    except (ImportError, TypeError, KeyError):
        import importlib.util
        import pathlib

        path = pathlib.Path(__file__).resolve().parent / "tools_v4.py"
        spec = importlib.util.spec_from_file_location("artec_plugin_tools_v4", path)
        mod = importlib.util.module_from_spec(spec)
        # tools_v4 back-imports the four shared helpers; when loaded bare we inject them
        # directly and strip that import line, so no parent package is needed.
        mod.__dict__["_get_engine"] = _get_engine
        mod.__dict__["_jtext"] = _jtext
        mod.__dict__["_log_run"] = _log_run
        mod.__dict__["_config"] = _config
        source = path.read_text(encoding="utf-8").replace(
            "from .tools import _config, _get_engine, _jtext, _log_run", "")
        exec(compile(source, str(path), "exec"), mod.__dict__)  # noqa: S102
        return mod


_v4 = _load_v4()
HANDLERS_V4 = _v4.HANDLERS_V4
transcription_violations = _v4.transcription_violations
HANDLERS.update(HANDLERS_V4)

# Built-in tool names blocked by the pre_tool_call hook — defense in depth on top of
# agent.disabled_toolsets: [terminal, code_execution, file] in the profile config
# (toolset names per https://hermes-agent.nousresearch.com/docs/reference/toolsets-reference).
BLOCKED_TOOL_PREFIXES = ("terminal", "shell", "exec", "write_file", "patch",
                         "code_execution", "run_python")


def pre_tool_call(tool_name: str, args: dict | None = None, task_id: str | None = None,
                  **kwargs) -> dict | None:
    """Hook: enforce metric TRANSCRIPTION, then block the file-write/shell families.
    The fifteen artec tools otherwise pass through."""
    # v4 §4 — the agent is a transcriber, never an originator. Every digit it submits must
    # appear in the operator's verbatim message. It may not compute, estimate, infer,
    # round, or carry a figure forward.
    if tool_name == "record_metrics":
        payload = args or {}
        message = str(payload.get("operator_message") or "")
        if not message.strip():
            return {"action": "block",
                    "message": "record_metrics requires operator_message — the operator's "
                               "verbatim text is the audit trail and the source of every digit"}
        bad = transcription_violations(payload.get("figures") or {}, message)
        if bad:
            return {"action": "block",
                    "message": ("refused: these figures contain digits absent from the "
                                f"operator's message — {', '.join(bad)}. Ask the operator "
                                "for the number; do not compute, estimate, or infer it.")}
        return None

    if tool_name in HANDLERS:
        return None
    lowered = str(tool_name).lower()
    if any(lowered.startswith(p) or p in lowered for p in BLOCKED_TOOL_PREFIXES):
        return {"action": "block",
                "message": "file-write and shell are disabled for the artec profile — "
                           "use the six artec tools only"}
    return None


def dispatch(tool: str, /, **kwargs) -> str:
    """Test/diagnostic entry point mirroring the agent's dispatch: unknown tool is
    'no such tool' — the capability does not exist to be permitted."""
    fn = HANDLERS.get(tool)
    if fn is None:
        raise LookupError(f"no such tool: {tool!r} (available: {sorted(HANDLERS)})")
    engine = kwargs.pop("engine", None)
    return fn(kwargs, engine=engine)
