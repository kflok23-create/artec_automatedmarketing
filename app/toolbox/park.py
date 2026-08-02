"""§7.6 FALLBACK · PARK — never publish a weak visual to avoid parking.

The wishlist is structured JSON in the bank's own vocabulary so the human knows exactly
which folder to drop the file into. target_folder is validated against the taxonomy.
"""

from __future__ import annotations

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models import Post
from app.schemas import WishlistEntry


class ParkError(RuntimeError):
    pass


def validate_wishlist(entries: list[dict]) -> list[dict]:
    if not entries:
        raise ParkError("a PARKED post requires at least one wishlist entry")
    validated = []
    for e in entries:
        try:
            validated.append(WishlistEntry.model_validate(e).model_dump())
        except ValidationError as err:
            raise ParkError(f"invalid wishlist entry {e!r}: {err.errors()[0]['msg']}") from None
    return validated


def park_post(session: Session, post: Post, reason: str, wishlist: list[dict]) -> None:
    post.asset_wishlist = validate_wishlist(wishlist)
    post.park_reason = reason
    post.status = "PARKED"
    session.flush()
