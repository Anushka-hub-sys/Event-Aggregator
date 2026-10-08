"""
Discord webhook integration.

Create a webhook:  Server Settings -> Integrations -> Webhooks -> New Webhook
Then paste the URL into your .env as DISCORD_WEBHOOK_URL.
"""
import asyncio
import logging

import httpx

from .config import settings
from .schemas import EventOut

logger = logging.getLogger(__name__)

# Discord embed accent colours (decimal, not hex!)
CATEGORY_COLORS: dict[str, int] = {
    "hackathon": 0x8B5CF6,   # violet
    "coding": 0x3B82F6,      # blue
    "workshop": 0x10B981,    # green
    "webinar": 0xF59E0B,     # amber
    "meetup": 0xEC4899,      # pink
    "conference": 0x06B6D4,  # cyan
    "gdg": 0xEF4444,         # Google red
    "other": 0x6B7280,       # grey
}
DEFAULT_COLOR = 0x6B7280


def _fmt(dt) -> str | None:
    return dt.isoformat() if dt else None


def build_event_embed(event: EventOut) -> dict:
    """Turn one event into a Discord embed object."""
    fields = [
        {"name": "Category", "value": event.category.title(), "inline": True},
        {"name": "Mode", "value": event.mode.title(), "inline": True},
    ]
    if event.organizer:
        fields.append({"name": "Organizer", "value": event.organizer, "inline": True})
    if event.location:
        fields.append({"name": "Location", "value": event.location, "inline": True})
    if event.starts_at:
        fields.append({"name": "Starts", "value": event.starts_at.strftime("%d %b %Y"), "inline": True})
    if event.deadline:
        fields.append({"name": "Deadline", "value": event.deadline.strftime("%d %b %Y"), "inline": True})

    embed = {
        "title": event.title[:256],
        "url": event.url,
        "description": (event.description or "No description provided.")[:400],
        "color": CATEGORY_COLORS.get(event.category, DEFAULT_COLOR),
        "fields": fields[:25],  # Discord hard limit
        "footer": {"text": f"Source: {event.source} | Event Aggregator"},
    }
    return embed


def build_payload(event: EventOut) -> dict:
    return {
        "username": "Event Aggregator",
        "content": f"🚀 **New event added:** {event.title}",
        "embeds": [build_event_embed(event)],
    }


async def notify_new_events(events: list[EventOut], webhook_url: str | None = None) -> int:
    """
    Send one Discord message per event.

    Returns the number of messages successfully delivered.
    Never raises - a broken webhook must not break the API request.
    """
    url = (webhook_url or settings.DISCORD_WEBHOOK_URL or "").strip()

    if not events:
        return 0

    if not url:
        logger.info(
            "DISCORD_WEBHOOK_URL is not configured - skipping %d notification(s).",
            len(events),
        )
        return 0

    sent = 0
    async with httpx.AsyncClient(timeout=10.0) as client:
        for event in events:
            try:
                response = await client.post(url, json=build_payload(event))
                response.raise_for_status()
                sent += 1
                logger.info("Discord notification sent for %r", event.title)
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "Discord rejected the notification (%s): %s",
                    exc.response.status_code,
                    exc.response.text[:200],
                )
            except httpx.HTTPError as exc:
                logger.error("Discord notification failed: %s", exc)

            # Discord rate-limits webhooks (~5 requests / 5 s per webhook).
            await asyncio.sleep(0.6)

    return sent


async def send_test_notification() -> dict:
    """Used by POST /api/notify/test so you can verify your webhook quickly."""
    url = (settings.DISCORD_WEBHOOK_URL or "").strip()
    if not url:
        return {"ok": False, "detail": "DISCORD_WEBHOOK_URL is not set in your .env file."}

    payload = {
        "username": "Event Aggregator",
        "content": "✅ Webhook connected successfully. Your Event Aggregator can post here.",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
        return {"ok": True, "detail": "Test notification delivered."}
    except httpx.HTTPStatusError as exc:
        return {
            "ok": False,
            "detail": f"Discord returned {exc.response.status_code}: {exc.response.text[:200]}",
        }
    except httpx.HTTPError as exc:
        return {"ok": False, "detail": f"Network error: {exc}"}