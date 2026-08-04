"""§5 DATA MODEL — Postgres in production, SQLite in unit tests.

`posts` is the board, the log, and the ledger of record. Lanes are physically separate:
orders = MONEY ONLY, events = BEHAVIOUR ONLY, metrics = ENGAGEMENT ONLY (NULL = unmeasured).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSONVariant = JSON().with_variant(JSONB(), "postgresql")

# SQLite only autoincrements INTEGER PRIMARY KEY — BIGINT pks need the variant for tests.
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")

POST_STATUSES = ("DRAFT", "APPROVED", "REJECTED", "PARKED", "RENDERED", "PUBLISHED", "FAILED")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Post(Base):
    __tablename__ = "posts"

    post_id: Mapped[str] = mapped_column(Text, primary_key=True)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)

    angle: Mapped[str | None] = mapped_column(Text)
    hook: Mapped[str | None] = mapped_column(Text)
    cta_type: Mapped[str | None] = mapped_column(Text)
    cta_placement: Mapped[str | None] = mapped_column(Text)
    keywords: Mapped[list | None] = mapped_column(JSONVariant)
    visual_tools: Mapped[list | None] = mapped_column(JSONVariant)
    slot: Mapped[str | None] = mapped_column(Text)  # an ATTRIBUTE (learning lever), never a timer
    source_asset_ids: Mapped[list | None] = mapped_column(JSONVariant)

    caption: Mapped[str | None] = mapped_column(Text)
    media_url: Mapped[str | None] = mapped_column(Text)          # fal-hosted fallback only
    media_drive_file_id: Mapped[str | None] = mapped_column(Text)  # canonical: Drive _generated/

    asset_wishlist: Mapped[list | None] = mapped_column(JSONVariant)
    park_reason: Mapped[str | None] = mapped_column(Text)

    code: Mapped[str | None] = mapped_column(Text)
    utm: Mapped[dict | None] = mapped_column(JSONVariant)
    tracked_url: Mapped[str | None] = mapped_column(Text)

    external_post_id: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # v3: which planner authored this row ('bespoke' | 'agent'), recorded at insert; and
    # the gate verdict WITH the edit deltas — the deltas are what train taste.
    plan_source: Mapped[str | None] = mapped_column(Text)
    gate_action: Mapped[dict | None] = mapped_column(JSONVariant)

    # v4 §5 — the two surfaces that never auto-publish. Each holds for human review inside
    # the 21:00 digest; no configuration value can exempt either.
    #   email_review: {decision, edits, reviewed_at, expiry, reason}
    #   video_review: {decision, reason, telegram_message_id, delivered_at, reviewed_at, expiry}
    # telegram_message_id is the proof the operator was actually SHOWN the video —
    # review_video refuses without it.
    email_review: Mapped[dict | None] = mapped_column(JSONVariant)
    video_review: Mapped[dict | None] = mapped_column(JSONVariant)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class Asset(Base):
    __tablename__ = "assets"

    drive_file_id: Mapped[str] = mapped_column(Text, primary_key=True)
    drive_path: Mapped[str] = mapped_column(Text, nullable=False)
    layer1: Mapped[str | None] = mapped_column(Text)
    layer2: Mapped[str | None] = mapped_column(Text)
    medium: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text)
    has_person: Mapped[bool | None] = mapped_column(Boolean)
    name: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    md5_checksum: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_s: Mapped[float | None] = mapped_column(Numeric)
    aspect: Mapped[str | None] = mapped_column(Text)  # vertical | square | landscape
    description: Mapped[str | None] = mapped_column(Text)  # human notes from Drive
    drive_modified_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")  # active | missing
    times_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Order(Base):
    """MONEY ONLY. post_id joins via client_reference_id (Stripe) / reference_1 (Billplz);
    NULL post_id = UNATTRIBUTED — reported, never guessed."""

    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_orders_source_external"),)

    order_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)  # stripe | billplz
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    post_id: Mapped[str | None] = mapped_column(Text)
    code: Mapped[str | None] = mapped_column(Text)
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str | None] = mapped_column(Text)
    customer_email_hash: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict | None] = mapped_column(JSONVariant)


class Event(Base):
    """BEHAVIOUR ONLY."""

    __tablename__ = "events"

    event_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    dedupe_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    post_id: Mapped[str | None] = mapped_column(Text)
    session_id: Mapped[str | None] = mapped_column(Text)
    event_type: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    code: Mapped[str | None] = mapped_column(Text)
    utm: Mapped[dict | None] = mapped_column(JSONVariant)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict | None] = mapped_column(JSONVariant)


class Metric(Base):
    """ENGAGEMENT ONLY. NULL = unmeasured — never coerced to 0."""

    __tablename__ = "metrics"

    post_id: Mapped[str] = mapped_column(Text, primary_key=True)
    channel: Mapped[str] = mapped_column(Text, primary_key=True)
    metric_date: Mapped[date] = mapped_column(Date, primary_key=True)
    impressions: Mapped[int | None] = mapped_column(BigInteger)
    completion_rate: Mapped[float | None] = mapped_column(Numeric)
    watch_time_s: Mapped[float | None] = mapped_column(Numeric)
    saves: Mapped[int | None] = mapped_column(BigInteger)
    shares: Mapped[int | None] = mapped_column(BigInteger)
    clicks: Mapped[int | None] = mapped_column(BigInteger)
    # v4: 'operator_via_agent' when transcribed through the digest conversation, 'manual'
    # when entered directly. operator_message is the VERBATIM audit trail — the agent is a
    # transcriber, never an originator, and this is how that is auditable after the fact.
    source: Mapped[str | None] = mapped_column(Text)
    operator_message: Mapped[str | None] = mapped_column(Text)
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Learning(Base):
    __tablename__ = "learnings"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    week_start: Mapped[date | None] = mapped_column(Date)
    lever: Mapped[str | None] = mapped_column(Text)
    lever_value: Mapped[str | None] = mapped_column(Text)
    kpi: Mapped[str | None] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Numeric)
    sample_size: Mapped[int | None] = mapped_column(Integer)
    verdict: Mapped[str | None] = mapped_column(Text)  # keep | kill | test | insufficient data
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class Config(Base):
    __tablename__ = "config"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict | list | str | int | bool | None] = mapped_column(JSONVariant)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 'seed' = came from OPERATOR_CONSTANTS · 'operator' = chosen deliberately ·
    # NULL = unknown (predates the column). Supersession may correct a 'seed' value and
    # must never touch the other two. See DECISIONS.md #35.
    set_by: Mapped[str | None] = mapped_column(Text)


class EndpointPrice(Base):
    """v4 §7·C5 — the fal price table, moved OUT of `config`.

    Reconciliation must be able to write prices, and no tool may write `config`; a separate
    table is how both invariants survive. Prices are UNIT-AWARE: clarity-upscaler and
    qwen-lora bill per MEGAPIXEL, which a flat per-call figure cannot represent — cost scales
    with requested output resolution, so the estimator must compute megapixels before the
    call is made.

    rate_micros = USD micro-dollars (1e-6 USD) per `unit`. Integer, never float, near money.
      $0.030 per megapixel → 30_000 micros, unit='per_megapixel'
      $0.040 per image     → 40_000 micros, unit='per_image'
    """

    __tablename__ = "endpoint_prices"

    endpoint: Mapped[str] = mapped_column(Text, primary_key=True)
    rate_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False)  # per_megapixel | per_image
    source: Mapped[str | None] = mapped_column(Text)         # 'invoice' | 'fal_api' | 'seed'
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Inactive endpoints stay UNCALLABLE but priced — if one is ever re-enabled it arrives
    # already priced rather than unpriced-and-therefore-blocked at the worst moment.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Digest(Base):
    """v4 §6 — job 11 prepares, job 12 delivers. The digest is the ONLY place failures
    surface, so its completeness is safety-critical."""

    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    digest_date: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONVariant)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class PlanShadow(Base):
    """v3 shadow mode: the non-live planner's output, for `artec plan-diff`. Mirrors the
    planning columns of posts. Nothing here ever publishes."""

    __tablename__ = "plans_shadow"
    __table_args__ = (
        UniqueConstraint("week_start", "channel", "slot", "source", name="uq_shadow_week_channel_slot_src"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    angle: Mapped[str | None] = mapped_column(Text)
    hook: Mapped[str | None] = mapped_column(Text)
    cta_type: Mapped[str | None] = mapped_column(Text)
    cta_placement: Mapped[str | None] = mapped_column(Text)
    keywords: Mapped[list | None] = mapped_column(JSONVariant)
    slot: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False)  # 'agent' | 'bespoke'
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class AgentRun(Base):
    """v3: one row per hermes-agent job run — observability for the Sunday brain."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    job: Mapped[str | None] = mapped_column(Text)          # learn_ideate | weekly_gate
    session_id: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str | None] = mapped_column(Text)
    tools_called: Mapped[list | None] = mapped_column(JSONVariant)
    tokens: Mapped[int | None] = mapped_column(BigInteger)
    cost_cents: Mapped[int | None] = mapped_column(BigInteger)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    args: Mapped[dict | None] = mapped_column(JSONVariant)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str | None] = mapped_column(Text)  # ok | error
    log: Mapped[list | None] = mapped_column(JSONVariant)
    # v4: fal spend for this run, in micro-dollars. The digest's SPEND & HEALTH block needs
    # a queryable source for week-to-date render spend; parsing it out of `log` would be
    # fragile. `agent_runs.cost_cents` is the equivalent meter on the agent side.
    cost_micros: Mapped[int | None] = mapped_column(BigInteger)


# ---------------------------------------------------------------------------------------
# v_brief — a SQL VIEW, max 40 rows, rendered to plain text before every LEARN/IDEATE run.
# Written in the portable SQL subset so the same string runs on Postgres and SQLite
# (subquery-with-LIMIT instead of parenthesized UNION limbs; CAST(... AS TEXT); ||).
# ---------------------------------------------------------------------------------------
V_BRIEF_SQL = """
CREATE VIEW v_brief AS
SELECT section, line FROM (
  SELECT 1 AS ord, 'post' AS section,
         p.post_id || ' ' || p.channel || ' [' || p.status || ']'
         || ' angle=' || COALESCE(p.angle, '-')
         || ' hook=' || COALESCE(p.hook, '-')
         || ' cta=' || COALESCE(p.cta_type, '-') || '/' || COALESCE(p.cta_placement, '-')
         || ' slot=' || COALESCE(p.slot, '-') AS line
  FROM (
    -- The 14 most recent posts, PLUS every PARKED post regardless of age.
    --
    -- THE DEFECT THIS FIXES (found by the agent in production and written into its own
    -- memory, never into the gap register): the post section was a pure recency window,
    -- so a post parked weeks ago — post_1485 is exactly this — fell out of it entirely.
    -- read_brief then under-reported the parked backlog while read_parked_posts returned
    -- it, and read_brief is the read the planner makes FIRST. A backlog the planner cannot
    -- see is a backlog it plans over.
    SELECT * FROM posts WHERE post_id IN (
      SELECT post_id FROM (
        SELECT post_id FROM posts ORDER BY week_start DESC, post_id LIMIT 14
      )
      UNION
      SELECT post_id FROM (
        SELECT post_id FROM posts WHERE status = 'PARKED' ORDER BY post_id LIMIT 20
      )
    )
  ) p
  UNION ALL
  SELECT 2, 'learning',
         l.lever || '=' || COALESCE(l.lever_value, '')
         || ' kpi=' || COALESCE(l.kpi, '')
         || ' verdict=' || COALESCE(l.verdict, '')
  FROM (SELECT * FROM learnings ORDER BY week_start DESC, id DESC LIMIT 10) l
  UNION ALL
  SELECT 3, 'config', c.key || '=' || CAST(c.value AS TEXT)
  FROM (SELECT * FROM config WHERE key IN (
        'cm_per_unit_sgd_minor','cm_per_unit_myr_minor',
        'discount_sgd_minor','discount_myr_minor',
        'kill_line_cac_sgd_minor','kill_line_cac_myr_minor',
        'seo_seeds','channel_cadence','kpi_weights','allow_person_assets')) c
  UNION ALL
  SELECT 4, 'parked', 'parked_awaiting_assets: ' || CAST(COUNT(*) AS TEXT)
  FROM posts WHERE status = 'PARKED'
  UNION ALL
  SELECT 5, 'inventory',
         a.subject || '/' || a.medium || ': ' || CAST(COUNT(*) AS TEXT) || ' assets, '
         || CAST(SUM(CASE WHEN a.times_used = 0 THEN 1 ELSE 0 END) AS TEXT) || ' unused'
  FROM (SELECT * FROM assets WHERE status = 'active') a
  GROUP BY a.subject, a.medium
) b
ORDER BY section, line
-- Raised from 40 when PARKED posts joined the post section. The outer LIMIT sorts by
-- section, so posts come first and the sections at risk of being silently eaten are the
-- PARKED COUNT and the asset INVENTORY — the two the planner needs in order to know what
-- it cannot service. The cap still exists (an unbounded brief is a cost problem), but it
-- now has headroom above the bounded inputs: 14 recent + at most 20 parked + 10 learnings
-- + 10 config + 1 parked count + inventory.
LIMIT 70
"""

DROP_V_BRIEF_SQL = "DROP VIEW IF EXISTS v_brief"
