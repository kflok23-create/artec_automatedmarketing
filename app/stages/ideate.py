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
    """The Monday of the week ABOUT TO BEGIN — the week this plan is for.

    IT USED TO RETURN THE MONDAY THAT HAD ALREADY PASSED. `today - today.weekday()` is the
    Monday of the CURRENT week, so on a Sunday — the only day ideate runs — weekday() is 6
    and it returned six days BACKWARDS, despite the name.

    The consequence was not an error. Ideate would target the week that was ending, find its
    cadence already met by existing posts, add nothing, and the 09:00 gate would open on a
    board someone had already emptied. **A Sunday that looks like it worked**, and a week of
    planning lost with no line anywhere saying so.

    Deferred through 2026-08-09 (DECISIONS 107, D-vii) because the nine drafts for week
    2026-08-03 WERE that Sunday's plan and changing date arithmetic 72 hours before a first
    run is how this system has been broken before. **All nine have now been gated** — six
    approved, three rejected — so the week is spent and the reason to defer expired with it.

    THE MONDAY STRICTLY AFTER TODAY. On a Sunday that is tomorrow; on any other day it is
    the start of the next week, because the current one has already begun and cannot be
    planned. `learn`/`report` still read `last_week_start`, the week just finished, so the
    Sunday pair is coherent: learn from the week that ended, plan the week that starts.

    `today` is a parameter, never a hidden clock — DECISIONS 112.
    """
    today = today or datetime.now(UTC).date()
    return today + timedelta(days=7 - today.weekday())


def ideate(session: Session, llm, week_start: date | None = None, log=print) -> dict:
    week = week_start or next_week_start()

    seeds = get_config(session, "seo_seeds", [])
    if not seeds or len(seeds) < 5:
        raise OperatorError(
            "SEO_SEEDS is empty or too short — seed 5–15 keywords before the first ideate: "
            "artec config set seo_seeds '[\"keyword one\", …]'"
        )
    # v4 §7·A7 — a slot that is not a key of slot_times would never publish and never
    # error: the scheduler only looks for slots it knows about. Reject at write time, in
    # BOTH planners. The seam already does this; bespoke ideate did not.
    slot_times: dict[str, str] = get_config(session, "slot_times")
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
        slot = item.get("slot")
        if slot not in slot_times:
            raise OperatorError(
                f"ideate produced slot {slot!r} for {channel}, which is not a key of "
                f"slot_times {sorted(slot_times)}. A post with an unknown slot would never "
                "publish and never error, so it is rejected at write time."
            )
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
                slot=slot,  # a real firing time in v4 AND still a learned lever
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
