"""Pydantic payloads shared by the CLI and the API mirrors."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from app.taxonomy import WISHLIST_FOLDERS


class EventBeacon(BaseModel):
    """POST /event — behaviour only. artec.my forwards code, utm_*, and session_id."""

    session_id: str
    event_type: str  # page_view | add_to_cart | checkout_start
    url: str = ""
    code: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    occurred_at: datetime | None = None


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
