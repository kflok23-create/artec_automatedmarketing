"""The second loop: monthly wishlist → daily human uploads → the bank expands ahead of the
ideas. `show` prints open entries grouped by target Drive folder; `match` returns PARKED
posts to APPROVED once the bank can service them; `fulfil` is the manual override.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_config
from app.models import Asset, Post
from app.taxonomy import path_to_tags
from app.toolbox.asset_match import find_candidates


def show(session: Session, log=print) -> dict:
    parked = list(session.execute(select(Post).where(Post.status == "PARKED")).scalars())
    grouped: dict[str, list[str]] = defaultdict(list)
    for p in parked:
        for w in p.asset_wishlist or []:
            grouped[w.get("target_folder", "?")].append(
                f"{p.post_id}: {w.get('medium', '?')}/{w.get('aspect', 'any')} — {w.get('description', '')}"
            )
    if not grouped:
        log("wishlist: nothing parked — the bank is ahead of the ideas")
        return {}
    for folder in sorted(grouped):
        log(f"\n→ upload into  {folder}/")
        for line in grouped[folder]:
            log(f"   {line}")
    return dict(grouped)


def _entry_satisfied(session: Session, entry: dict, allow_person: bool) -> Asset | None:
    tags = path_to_tags(entry.get("target_folder", ""))
    if tags.subject == "unknown":
        return None
    medium = entry.get("medium") or (tags.medium if tags.medium != "mixed" else None)
    candidates = find_candidates(
        session, subject=tags.subject, medium=medium,
        aspect=entry.get("aspect"), allow_person=allow_person, limit=1,
    )
    return candidates[0] if candidates else None


def match(session: Session, log=print) -> dict:
    """After a sync: re-check PARKED posts against newly arrived assets; auto-return any
    fully-serviceable post to APPROVED, recording which asset satisfied each entry."""
    allow_person = bool(get_config(session, "allow_person_assets", False))
    parked = list(session.execute(select(Post).where(Post.status == "PARKED")).scalars())
    returned = 0
    for p in parked:
        entries = p.asset_wishlist or []
        if not entries:
            continue
        satisfied: list[dict] = []
        all_ok = True
        for entry in entries:
            asset = _entry_satisfied(session, entry, allow_person)
            if asset is None:
                all_ok = False
                break
            satisfied.append({**entry, "fulfilled_by": asset.drive_file_id})
        if all_ok:
            p.asset_wishlist = satisfied
            p.status = "APPROVED"
            p.park_reason = None
            returned += 1
            log(f"{p.post_id}: wishlist satisfied → APPROVED")
    session.flush()
    if not returned:
        log("wishlist match: no parked post can be serviced yet")
    return {"returned": returned}


def fulfil(session: Session, post_id: str, drive_file_id: str, log=print) -> dict:
    """Manual override: pin a specific bank asset onto a parked post and re-approve it."""
    post = session.get(Post, post_id)
    if post is None or post.status != "PARKED":
        raise ValueError(f"{post_id} is not a PARKED post")
    asset = session.get(Asset, drive_file_id)
    if asset is None or asset.status != "active":
        raise ValueError(f"asset {drive_file_id} not found or not active")
    entries = post.asset_wishlist or []
    post.asset_wishlist = [{**e, "fulfilled_by": drive_file_id} for e in entries] or [
        {"target_folder": asset.layer1 or "UGC", "medium": asset.medium or "photo",
         "description": "manually fulfilled", "fulfilled_by": drive_file_id}
    ]
    post.status = "APPROVED"
    post.park_reason = None
    session.flush()
    log(f"{post_id}: manually fulfilled with {drive_file_id} → APPROVED")
    return {"post_id": post_id, "asset": drive_file_id}
