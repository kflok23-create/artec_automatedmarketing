"""Pydantic payloads shared by the CLI and the API mirrors."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.taxonomy import WISHLIST_FOLDERS


class EventBeacon(BaseModel):
    """POST /event — behaviour only. Two shapes share this endpoint:

    - browser beacons (page_view | add_to_cart | checkout_start): session_id required
      (deferred on artec.my for now, but the contract stands)
    - server-side `order_created` from checkout.php, sent the moment a Billplz bill is
      created, BEFORE payment: bill_id required. Stored as a pending order keyed on
      bill_id; the paid webhook joins against it to resolve post_id.
    """

    event_type: str
    session_id: str | None = None
    url: str = ""
    code: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_content: str | None = None
    occurred_at: datetime | None = None
    ts: datetime | None = None  # checkout.php's timestamp field name
    # order_created fields
    bill_id: str | None = None
    post_id: str | None = None
    value: float | None = None
    currency: str | None = None
    pack: str | None = None
    market: str | None = None
    gateway: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> EventBeacon:
        if self.event_type == "order_created":
            if not self.bill_id:
                raise ValueError("order_created requires bill_id")
        elif not self.session_id:
            raise ValueError("browser events require session_id")
        return self


class MeasureRow(BaseModel):
    """One metrics upsert. Absent/None fields stay NULL — unmeasured, never 0."""

    post_id: str
    channel: str
    metric_date: date
    impressions: int | None = None
    completion_rate: float | None = None
    watch_time_s: float | None = None
    saves: int | None = None
    shares: int | None = None
    clicks: int | None = None


class MeasurePayload(BaseModel):
    rows: list[MeasureRow]


class WishlistEntry(BaseModel):
    """Structured PARK wishlist, expressed in the bank's own vocabulary."""

    target_folder: str
    medium: str
    aspect: str | None = None
    duration_s: str | None = None
    description: str

    @field_validator("target_folder")
    @classmethod
    def _valid_folder(cls, v: str) -> str:
        if v not in WISHLIST_FOLDERS:
            raise ValueError(f"target_folder must be one of {WISHLIST_FOLDERS}, got {v!r}")
        return v


class ToolPlan(BaseModel):
    """The toolbox routing decision — an ordered tool list plus chosen asset ids."""

    subject: str
    tools: list[str] = Field(min_length=1)
    asset_ids: list[str] = Field(default_factory=list)
    prompt: str = ""


class CommandRequest(BaseModel):
    """Generic body for POST /commands/<stage>."""

    week: date | None = None
    post_id: str | None = None
    all_approved: bool = False
    all_rendered: bool = False
    full: bool = False
