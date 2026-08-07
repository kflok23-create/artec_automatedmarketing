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


PARK_REASON_LIMIT = 500


def park_reason_text(prefix: str, message: str, log=print) -> str:
    """Fit a provider message into `park_reason` WITHOUT losing the cause.

    THE TRUNCATION HAS NOW COST TWO DIAGNOSES. `park_reason` is capped at 500 characters and
    provider messages put the cause at the END, so the cut lands exactly where the answer
    begins:

        HTTP 400: {"success":false            <- the reason was 'YouTube title is too long
                                                 (2017 characters)'
        … maximum megapixels that can be processed is 3…   <- the number was 32

    Both had to be re-derived from container logs days later, and the second produced a
    constant that was wrong by an order of magnitude.

    So the message is logged IN FULL first — the run log is not capped — and the stored text
    keeps the HEAD and the TAIL with the elision marked, because the tail is where providers
    put the reason. An elision that says it happened is recoverable; a silent cut is not.
    """
    full = f"{prefix}: {message}"
    log(f"park: {full}")
    if len(full) <= PARK_REASON_LIMIT:
        return full
    marker = " …[cut, full text in the run log]… "
    keep = PARK_REASON_LIMIT - len(marker)
    head = keep * 2 // 3
    return full[:head] + marker + full[-(keep - head):]
