"""IDEATE — drafts a 7-day plan sized by CHANNEL_CADENCE counts (a planning input only,
never a timer). One `posts` row per planned post, status=DRAFT, full creative genome.
Does NOT render or publish. Idempotent: re-running tops each channel up to its cadence
count instead of duplicating (DECISIONS.md #13).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import OperatorError, get_config, next_post_id
from app.models import Post
from app.spine import build_tracked_url, utm_dict
from app.stages.learn import render_brief


def next_week_start(today: date | None = None) -> date:
    today = today or datetime.now(UTC).date()
    return today - timedelta(days=today.weekday())


def ideate(session: Session, llm, week_start: date | None = None, log=print) -> dict:
    week = week_start or next_week_start()

    seeds = get_config(session, "seo_seeds", [])
    if not seeds or len(seeds) < 5:
        raise OperatorError(
            "SEO_SEEDS is empty or too short — seed 5–15 keywords before the first ideate: "
            "artec config set seo_seeds '[\"keyword one\", …]'"
        )
    cadence: dict[str, int] = get_config(session, "channel_cadence")
    site = get_config(session, "site_base_url")
    social_code = get_config(session, "social_code")
    email_code = get_config(session, "email_code")

    existing: dict[str, int] = dict(
        session.execute(
            select(Post.channel, func.count())
            .where(Post.week_start == week, Post.status != "REJECTED")
            .group_by(Post.channel)
        ).all()
    )
    deficit = {ch: max(0, int(n) - existing.get(ch, 0)) for ch, n in cadence.items()}
    if not any(deficit.values()):
        log(f"ideate {week}: cadence already satisfied — nothing to draft")
        return {"week": str(week), "created": 0}

    plan = llm.complete_json(
        "ideate_v1.md",
        {
            "brief": render_brief(session),
            "cadence": deficit,
            "seeds": seeds,
            "week_start": str(week),
        },
    )
    if not isinstance(plan, list):
        raise ValueError("ideate: model reply was not a JSON array of posts")

    created = 0
    remaining = dict(deficit)
    for item in plan:
        channel = item.get("channel")
        if channel not in remaining or remaining[channel] <= 0:
            continue  # over-cadence or unknown channel — trimmed, never published extra
        medium = "email" if channel == "email" else "organic"
        post_id = next_post_id(session)
        session.add(
            Post(
                post_id=post_id,
                week_start=week,
                channel=channel,
                status="DRAFT",
                angle=item.get("angle"),
                hook=item.get("hook"),
                cta_type=item.get("cta_type"),
                cta_placement=item.get("cta_placement"),
                keywords=item.get("keywords") or [],
                slot=item.get("slot"),  # a stored attribute / learning lever, never a timer
                code=email_code if medium == "email" else social_code,
                utm=utm_dict(post_id, channel, medium),
                tracked_url=build_tracked_url(
                    post_id, channel, medium,
                    site_base_url=site, social_code=social_code, email_code=email_code,
                ),
            )
        )
        remaining[channel] -= 1
        created += 1

    session.flush()
    log(f"ideate {week}: drafted {created} posts " + str({k: v for k, v in deficit.items() if v}))
    return {"week": str(week), "created": created}
