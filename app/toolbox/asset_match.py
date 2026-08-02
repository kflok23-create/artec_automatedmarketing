"""§7.0 ASSET MATCHING — BANK-FIRST IS A HARD RULE.

Always queried before generating anything (the `assets` table, never Drive live).
Preference: least-recently-used, then lowest times_used, so the same photo does not
appear every week.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Asset


def find_candidates(
    session: Session,
    subject: str,
    medium: str | None = None,
    aspect: str | None = None,
    allow_person: bool = False,
    limit: int = 5,
) -> list[Asset]:
    q = select(Asset).where(Asset.status == "active", Asset.subject == subject)
    if medium:
        q = q.where(Asset.medium == medium)
    if aspect:
        # NULL aspect (unknown dimensions) matches any requested aspect.
        q = q.where((Asset.aspect == aspect) | (Asset.aspect.is_(None)))
    if not allow_person:
        # Exclude confirmed-person assets; unknown (NULL, i.e. UGC) stays eligible
        # per the spec's letter — see DECISIONS.md #20.
        q = q.where((Asset.has_person.is_(None)) | (Asset.has_person == False))  # noqa: E712
    q = q.order_by(
        Asset.last_used_at.asc().nulls_first(),
        Asset.times_used.asc(),
        Asset.drive_file_id.asc(),
    ).limit(limit)
    return list(session.execute(q).scalars())


def mark_used(session: Session, asset: Asset) -> None:
    asset.times_used = (asset.times_used or 0) + 1
    asset.last_used_at = datetime.now(UTC)
    session.flush()
