"""ORM model for an aggregated event."""
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite stores naive datetimes cleanly)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(600), unique=True, nullable=False, index=True)

    
    source: Mapped[str] = mapped_column(String(60), default="manual", index=True)

    
    category: Mapped[str] = mapped_column(String(40), default="other", index=True)

    
    mode: Mapped[str] = mapped_column(String(20), default="online", index=True)

    location: Mapped[str] = mapped_column(String(255), default="")
    organizer: Mapped[str] = mapped_column(String(150), default="")

    starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    def __repr__(self) -> str:  
        return f"<Event id={self.id} title={self.title!r} source={self.source!r}>"