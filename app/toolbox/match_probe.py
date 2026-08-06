"""Can a DRAFT be serviced from the bank? Read-only, no render, no spend.

Matching is BANK-ONLY: subject, medium, aspect, no generation fallback, no match → PARK.
`wishlist.match()` only inspects PARKED posts, and a DRAFT has no wishlist — a wishlist is
written when a post PARKS at render. So "no parked post can be serviced yet" says nothing
about the drafts, and nobody had asked.

If they cannot be serviced, job 6 parks everything the operator just approved and the week
produces nothing. Not a crash: another Sunday that looks like it worked.

This calls the SAME `find_candidates` the render path calls, with the SAME arguments render
derives from `channel_media` (app/stages/render.py:174). No LLM, no fal, no ffmpeg, no
writes. It answers whether an asset EXISTS for render to try — not whether the ffmpeg
pipeline will succeed on it, which no read can answer.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_config
from app.models import Asset, Post
from app.toolbox.asset_match import find_candidates

# THE SET JOB 6 RENDERS, not the set that happened to exist when this was written.
#
# The first version probed status == "DRAFT" only. The operator then gated all nine, and the
# probe went blind at exactly the moment its answer mattered: job 6 at SUN 10:00 renders
# APPROVED posts, and there were zero DRAFTs left to look at. A diagnostic scoped to a
# transient state stops answering the moment the state moves — which is the same shape as
# `wishlist.match()` inspecting only PARKED, the gap this probe was built to close.
RENDERABLE_STATUSES = ("APPROVED", "DRAFT")


def probe_drafts(session: Session, statuses=RENDERABLE_STATUSES, log=print) -> dict:
    from app.stages.render import DEFAULT_SUBJECT_BY_MEDIA

    cfg_media = get_config(session, "channel_media", {}) or {}
    allow_person = bool(get_config(session, "allow_person_assets", False))
    log(f"match probe: allow_person_assets={allow_person}")

    inventory: dict[str, int] = {}
    for subject, medium, aspect in session.execute(
        select(Asset.subject, Asset.medium, Asset.aspect).where(Asset.status == "active")
    ).all():
        key = f"{subject}/{medium}/{aspect or 'NULL'}"
        inventory[key] = inventory.get(key, 0) + 1
    if not inventory:
        log("match probe: THE BANK HAS NO ACTIVE ASSETS — every draft would park")
    for key, count in sorted(inventory.items()):
        log(f"  bank: {key} = {count}")

    results, blocked = [], []
    for post in session.execute(
        select(Post).where(Post.status.in_(tuple(statuses))).order_by(Post.post_id)
    ).scalars():
        spec = cfg_media.get(post.channel) or {}
        media = spec.get("media", "photo")
        aspect = spec.get("aspect")
        if post.channel == "email":
            # Email renders a hero image, so it takes the photo path. The PUBLISHED hero
            # must be a publicly reachable URL — `publish_email` uploads the Drive bytes to
            # fal storage for exactly that reason, because a Drive link is a viewer page,
            # not an image. That is a publish-time concern; the bank question is the same.
            media, aspect = "photo", spec.get("aspect", "square")
        subject = DEFAULT_SUBJECT_BY_MEDIA.get(media, media)
        found = find_candidates(session, subject=subject, medium=media, aspect=aspect,
                                allow_person=allow_person, limit=5)
        entry = {
            "post_id": post.post_id, "status": post.status,
            "channel": post.channel, "media": media,
            "aspect": aspect, "subject": subject, "candidates": len(found),
            "example": found[0].drive_file_id if found else None,
            "servicable": bool(found),
        }
        results.append(entry)
        if found:
            log(f"  {post.post_id} [{post.status}] {post.channel:<10} {media:<6} "
                f"aspect={str(aspect):<9} OK  {len(found)} candidate(s), "
                f"e.g. {found[0].drive_file_id}")
        else:
            blocked.append(post.post_id)
            log(f"  {post.post_id} [{post.status}] {post.channel:<10} {media:<6} "
                f"aspect={str(aspect):<9} *** NO ASSET — would PARK "
                f"(needs subject={subject} medium={media} aspect={aspect})")
            # WHY it has none. `allow_person_assets` gates ONLY the has_person filter;
            # aspect is an independent predicate, so flipping it to true adds assets to the
            # person dimension and cannot turn a landscape video into a vertical one. The
            # single escape is Asset.aspect IS NULL, which matches any requested aspect.
            same_kind = session.execute(select(
                Asset.aspect, Asset.has_person).where(
                Asset.status == "active", Asset.subject == subject,
                Asset.medium == media)).all()
            if not same_kind:
                log(f"      the bank holds NO active {subject}/{media} at any aspect")
            else:
                shapes = {}
                for asp, person in same_kind:
                    shapes[(asp or "NULL", bool(person))] = shapes.get(
                        (asp or "NULL", bool(person)), 0) + 1
                log(f"      bank has {len(same_kind)} {subject}/{media} but none at "
                    f"aspect={aspect}: " + ", ".join(
                        f"aspect={a} person={p}: {n}" for (a, p), n in sorted(
                            shapes.items(), key=lambda x: str(x[0]))))
                log("      NOTE: allow_person_assets does not affect ASPECT. Only an asset "
                    "with aspect NULL or the exact aspect can satisfy this.")

    log(f"match probe: {len(results) - len(blocked)} servicable, {len(blocked)} would park "
        f"{blocked}")
    if blocked:
        log("match probe: *** job 6 parks each of these. Approved at the gate, nothing "
            "publishes, and no error line says so.")
    return {"drafts": results, "would_park": blocked, "bank": inventory,
            "allow_person_assets": allow_person}
