"""MEASURE — the operator hands figures to the service directly. NO channel APIs, NO CSV.

Interactive field-by-field entry, or `--json '<payload>'`, or POST /commands/measure with
the same payload. Blank entry = NULL = unmeasured. Never coerce a skipped field to 0.
Upserts on (post_id, channel, metric_date), source='manual'.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Metric, Post
from app.schemas import MeasurePayload, MeasureRow

FIELDS = ("impressions", "completion_rate", "watch_time_s", "saves", "shares", "clicks")
INT_FIELDS = {"impressions", "saves", "shares", "clicks"}


def upsert_rows(session: Session, rows: list[MeasureRow]) -> int:
    written = 0
    for r in rows:
        pk = (r.post_id, r.channel, r.metric_date)
        row = session.get(Metric, pk)
        values = {f: getattr(r, f) for f in FIELDS}
        if row is None:
            session.add(Metric(post_id=r.post_id, channel=r.channel, metric_date=r.metric_date,
                               source="manual", collected_at=datetime.now(UTC), **values))
        else:
            for f, v in values.items():
                if v is not None:  # blank stays whatever it was; explicit values overwrite
                    setattr(row, f, v)
            row.source = "manual"
            row.collected_at = datetime.now(UTC)
        written += 1
    session.flush()
    return written


def unmeasured_posts(session: Session, target_date: date) -> list[Post]:
    posts = list(session.execute(select(Post).where(Post.status == "PUBLISHED")).scalars())
    result = []
    for p in posts:
        if session.get(Metric, (p.post_id, p.channel, target_date)) is None:
            result.append(p)
    return result


def measure_json(session: Session, payload: dict, log=print) -> dict:
    parsed = MeasurePayload.model_validate(payload)
    n = upsert_rows(session, parsed.rows)
    log(f"measure: upserted {n} metric rows")
    return {"upserted": n}


def measure_interactive(session: Session, since: date | None = None, log=print) -> dict:
    target = since or (datetime.now(UTC).date() - timedelta(days=1))
    pending = unmeasured_posts(session, target)
    if not pending:
        log(f"measure: every PUBLISHED post already has a metrics row for {target}")
        return {"upserted": 0}
    rows: list[MeasureRow] = []
    log(f"Entering metrics for {target}. Blank = unmeasured (NULL), never 0. Ctrl+C aborts.")
    for p in pending:
        log(f"\n{p.post_id} · {p.channel} · {p.hook or ''}")
        values: dict = {}
        for field in FIELDS:
            raw = input(f"  {field} [blank=unmeasured]: ").strip()
            if raw == "":
                values[field] = None
                continue
            try:
                values[field] = int(raw) if field in INT_FIELDS else float(raw)
            except ValueError:
                log("  not a number — recorded as unmeasured")
                values[field] = None
        rows.append(MeasureRow(post_id=p.post_id, channel=p.channel, metric_date=target, **values))
    n = upsert_rows(session, rows)
    log(f"measure: upserted {n} metric rows for {target}")
    return {"upserted": n}
