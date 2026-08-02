"""artec ↔ hermes-agent seam — SIX tools, nothing more.

Deployed to $HERMES_HOME/plugins/ on hermes-brain; hermes-agent loads plugin files from
that directory and registers their tools without forking. Self-contained on purpose: it
imports only sqlalchemy (textual SQL), never the artec app package, so the brain image
stays independent of the bespoke codebase.

THE CAPABILITY BOUNDARY IS THE SECURITY MODEL. There is no tool that writes orders,
events, metrics or config; no raw SQL tool; no generic query tool. "The model never edits
money rows" is enforced by the absence of a capability, not by an instruction the model is
asked to honour. Every tool call logs to `runs` with its arguments.

Tools:
  read_brief()                        v_brief text + REVENUE / ENGAGEMENT as SEPARATE
                                      blocks, never a blended figure. READ ONLY.
  read_learnings(week_start)          deterministic lever scores + verdicts. READ ONLY.
  read_asset_inventory()              per-subject/medium counts + unused. READ ONLY.
  read_parked_posts()                 PARKED posts + wishlists. READ ONLY.
  write_plan(week_start, posts)       DRAFT rows (agent mode) / plans_shadow (shadow mode);
                                      idempotent on (week_start, channel, slot).
  record_gate_decision(post_id, action, edits)  approve | edit | reject | inject;
                                      idempotent on post_id. Rejected → REJECTED, and no
                                      replacement is ever generated.
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


# -- READ ONLY ---------------------------------------------------------------------------

def read_brief(engine: Engine | None = None) -> str:
    """The v_brief view (≤40 rows) plus REVENUE and ENGAGEMENT as separate blocks.
    The lane rule survives into the text: no blended figure exists to quote."""
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


def read_learnings(week_start: str, engine: Engine | None = None) -> list[dict]:
    eng = _get_engine(engine)
    with eng.begin() as conn:
        _log_run(conn, "read_learnings", {"week_start": week_start})
        rows = conn.execute(text(
            "SELECT lever, lever_value, kpi, score, sample_size, verdict FROM learnings "
            "WHERE week_start = :w ORDER BY lever, lever_value"), {"w": week_start}).all()
    return [{"lever": r[0], "lever_value": r[1], "kpi": r[2],
             "score": float(r[3]) if r[3] is not None else None,
             "sample_size": r[4], "verdict": r[5]} for r in rows]


def read_asset_inventory(engine: Engine | None = None) -> list[dict]:
    eng = _get_engine(engine)
    with eng.begin() as conn:
        _log_run(conn, "read_asset_inventory", {})
        rows = conn.execute(text(
            "SELECT subject, medium, COUNT(*), "
            "SUM(CASE WHEN times_used = 0 THEN 1 ELSE 0 END) "
            "FROM assets WHERE status = 'active' GROUP BY subject, medium "
            "ORDER BY subject, medium")).all()
    return [{"subject": r[0], "medium": r[1], "count": r[2], "unused": r[3]} for r in rows]


def read_parked_posts(engine: Engine | None = None) -> list[dict]:
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


# -- NARROW WRITES -----------------------------------------------------------------------

def write_plan(week_start: str, posts: list[dict], engine: Engine | None = None) -> dict:
    """Insert the 7-day plan. Routing by config.plan_source:
      shadow  → plans_shadow with source='agent' (nothing the agent produces goes live)
      agent   → DRAFT rows in posts (plan_source='agent'), mirrored to plans_shadow
      bespoke → DISABLED: writes nothing (full rollback is one config row)
    Idempotent on (week_start, channel, slot)."""
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


def record_gate_decision(post_id: str, action: str, edits: dict | None = None,
                         engine: Engine | None = None) -> dict:
    """approve | edit | reject | inject. Idempotent on post_id. The edit DELTAS are stored
    in gate_action — the deltas are what train taste. Rejected slots are never refilled."""
    if action not in ("approve", "edit", "reject", "inject"):
        raise ValueError(f"unknown gate action {action!r}")
    eng = _get_engine(engine)
    with eng.begin() as conn:
        _log_run(conn, "record_gate_decision", {"post_id": post_id, "action": action,
                                                "edits": edits or {}})
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
        if action == "reject":
            new_status = "REJECTED"  # fewer posts this week — no replacement, ever
        else:
            new_status = "APPROVED"
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


# -- registry ----------------------------------------------------------------------------

TOOLS: dict[str, Any] = {
    "read_brief": read_brief,
    "read_learnings": read_learnings,
    "read_asset_inventory": read_asset_inventory,
    "read_parked_posts": read_parked_posts,
    "write_plan": write_plan,
    "record_gate_decision": record_gate_decision,
}


def dispatch(tool: str, /, **kwargs) -> Any:
    """The agent's only entry point. An unknown tool is 'no such tool' — not a permission
    error, because the capability does not exist to be permitted."""
    fn = TOOLS.get(tool)
    if fn is None:
        raise LookupError(f"no such tool: {tool!r} (available: {sorted(TOOLS)})")
    return fn(**kwargs)


def register(agent) -> None:
    """hermes-agent plugin hook: register the six tools, tolerant of registrar shape."""
    for name, fn in TOOLS.items():
        registrar = getattr(agent, "register_tool", None) or getattr(agent, "add_tool", None)
        if registrar is None:
            raise RuntimeError("hermes-agent exposed no tool registrar on this version")
        registrar(name=name, fn=fn, description=(fn.__doc__ or "").strip().splitlines()[0])
