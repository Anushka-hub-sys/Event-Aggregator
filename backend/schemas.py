"""Pydantic models: the API contract (what clients send and receive)."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Mode = Literal["online", "offline", "hybrid"]

ALLOWED_CATEGORIES = {
    "hackathon",
    "coding",
    "workshop",
    "webinar",
    "meetup",
    "conference",
    "gdg",
    "other",
}


class EventBase(BaseModel):
    title: str = Field(..., min_length=3, max_length=255)
    description: str = ""
    url: str = Field(..., min_length=4, max_length=600)
    source: str = "manual"
    category: str = "other"
    mode: Mode = "online"
    location: str = ""
    organizer: str = ""
    starts_at: datetime | None = None
    deadline: datetime | None = None
    is_featured: bool = False

    @field_validator("category")
    @classmethod
    def normalise_category(cls, value: str) -> str:
        value = (value or "other").strip().lower()
        return value if value in ALLOWED_CATEGORIES else "other"

    @field_validator("source")
    @classmethod
    def normalise_source(cls, value: str) -> str:
        return (value or "manual").strip().lower() or "manual"


class EventCreate(EventBase):
    """Payload for POST /api/events."""


class EventUpdate(BaseModel):
    """Payload for PATCH /api/events/{id} - every field is optional."""
    title: str | None = Field(None, min_length=3, max_length=255)
    description: str | None = None
    url: str | None = None
    source: str | None = None
    category: str | None = None
    mode: Mode | None = None
    location: str | None = None
    organizer: str | None = None
    starts_at: datetime | None = None
    deadline: datetime | None = None
    is_featured: bool | None = None


class EventOut(EventBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EventListOut(BaseModel):
    total: int
    count: int
    items: list[EventOut]


class ScrapeResult(BaseModel):
    scrapers_run: list[str]
    events_found: int
    events_added: int
    events_skipped: int
    notifications_sent: int
    errors: list[str]
    new_events: list[EventOut]


class StatsOut(BaseModel):
    total_events: int
    upcoming_events: int
    by_category: dict[str, int]
    by_source: dict[str, int]


class MessageOut(BaseModel):
    detail: str
    extra: dict | None = None