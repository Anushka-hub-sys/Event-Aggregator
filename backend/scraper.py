"""
Scraping layer with Live JSON APIs for Kontests, Unstop, Devfolio, and GDG.
"""
from __future__ import annotations

import abc
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Event

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "fbclid", "gclid",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def days_from_now(days: float) -> datetime:
    return utcnow() + timedelta(days=days)


def parse_iso_datetime(dt_str: str | None) -> datetime | None:
    if not dt_str:
        return None
    try:
        dt_str = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(dt_str)
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    clean_query = urlencode(
        [(k, v) for k, v in parse_qsl(parsed.query) if k.lower() not in TRACKING_PARAMS]
    )
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(
        (parsed.scheme.lower(), parsed.netloc.lower(), path, "", clean_query, "")
    )


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split()).strip()


@dataclass
class ScrapedEvent:
    title: str
    url: str
    description: str = ""
    source: str = "unknown"
    category: str = "other"
    mode: str = "online"
    location: str = ""
    organizer: str = ""
    starts_at: datetime | None = None
    deadline: datetime | None = None


class BaseScraper(abc.ABC):
    source_name: str = "unknown"

    @abc.abstractmethod
    async def fetch(self) -> list[ScrapedEvent]:
        raise NotImplementedError


# --------------------------------------------------------------------------
# LIVE SCRAPERS (Direct JSON Endpoints)
# --------------------------------------------------------------------------
class KontestsLiveScraper(BaseScraper):
    """Fetches upcoming coding contests (LeetCode, Codeforces, CodeChef, etc.)."""
    source_name = "coding_contests"

    async def fetch(self) -> list[ScrapedEvent]:
        events: list[ScrapedEvent] = []
        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": USER_AGENT}) as client:
            res = await client.get("https://kontests.net/api/v1/all")
            if res.status_code != 200:
                return events
            data = res.json()

        for item in data[:15]:
            title = clean_text(item.get("name"))
            url = item.get("url")
            site = item.get("site", "Contest")
            if not title or not url:
                continue

            start_dt = parse_iso_datetime(item.get("start_time"))
            end_dt = parse_iso_datetime(item.get("end_time"))

            events.append(
                ScrapedEvent(
                    title=title,
                    url=url,
                    description=f"Live programming contest hosted on {site}.",
                    source="kontests",
                    category="coding",
                    mode="online",
                    organizer=site,
                    starts_at=start_dt,
                    deadline=end_dt,
                )
            )
        return events


class UnstopLiveScraper(BaseScraper):
    """Direct JSON API scraper for Unstop Hackathons."""
    source_name = "unstop"

    async def fetch(self) -> list[ScrapedEvent]:
        events: list[ScrapedEvent] = []
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Referer": "https://unstop.com/hackathons",
        }
        api_url = "https://unstop.com/api/public/opportunity/search-result?opportunity=hackathons&per_page=12"
        
        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            res = await client.get(api_url)
            if res.status_code != 200:
                return events
            payload = res.json()

        cards = payload.get("data", {}).get("data", [])
        for item in cards:
            title = clean_text(item.get("title"))
            raw_url = item.get("seo_url") or item.get("public_url") or ""
            if not raw_url.startswith("http"):
                raw_url = f"https://unstop.com/{raw_url.lstrip('/')}"

            org = clean_text(item.get("organisation", {}).get("name", "Unstop"))
            start_date = parse_iso_datetime(item.get("start_date"))
            end_date = parse_iso_datetime(item.get("end_date") or item.get("regnRequirements", {}).get("end_regn_date"))

            events.append(
                ScrapedEvent(
                    title=title,
                    url=raw_url,
                    description=clean_text(item.get("sub_title") or f"Hackathon organized by {org} on Unstop."),
                    source="unstop",
                    category="hackathon",
                    mode="online" if "online" in str(item.get("region", "")).lower() else "hybrid",
                    location=clean_text(item.get("city", "")),
                    organizer=org,
                    starts_at=start_date,
                    deadline=end_date,
                )
            )
        return events


class DevfolioLiveScraper(BaseScraper):
    """Direct API for Devfolio Hackathons."""
    source_name = "devfolio"

    async def fetch(self) -> list[ScrapedEvent]:
        events: list[ScrapedEvent] = []
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Origin": "https://devfolio.co",
        }
        api_url = "https://api.devfolio.co/api/search/hackathons"
        payload = {"filter": "upcoming", "page": 1, "size": 10}

        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            res = await client.post(api_url, json=payload)
            if res.status_code != 200:
                return events
            data = res.json()

        hits = data.get("hits", [])
        for item in hits:
            title = clean_text(item.get("name"))
            slug = item.get("slug")
            url = f"https://{slug}.devfolio.co" if slug else "https://devfolio.co"
            
            start_dt = parse_iso_datetime(item.get("starts_at"))
            end_dt = parse_iso_datetime(item.get("ends_at"))

            events.append(
                ScrapedEvent(
                    title=title,
                    url=url,
                    description=clean_text(item.get("tagline") or "Web3 and Open Innovation Hackathon on Devfolio."),
                    source="devfolio",
                    category="hackathon",
                    mode="offline" if item.get("is_offline") else "online",
                    location=clean_text(item.get("location", "")),
                    organizer="Devfolio",
                    starts_at=start_dt,
                    deadline=end_dt,
                )
            )
        return events


class GDGLiveScraper(BaseScraper):
    """GDG Bevy platform public API for community events."""
    source_name = "gdg"

    async def fetch(self) -> list[ScrapedEvent]:
        events: list[ScrapedEvent] = []
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        api_url = "https://gdg.community.dev/api/event/?status=Live&page_size=10"

        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            res = await client.get(api_url)
            if res.status_code != 200:
                return events
            data = res.json()

        results = data.get("results", [])
        for item in results:
            title = clean_text(item.get("title"))
            url = item.get("url") or f"https://gdg.community.dev/events/details/{item.get('id')}"
            start_dt = parse_iso_datetime(item.get("start_date"))

            events.append(
                ScrapedEvent(
                    title=title,
                    url=url,
                    description=clean_text(item.get("description_short") or "Google Developer Group meetup / workshop."),
                    source="gdg",
                    category="gdg",
                    mode="offline" if item.get("event_type_title") == "In-person" else "online",
                    location=clean_text(item.get("city", "")),
                    organizer="Google Developer Groups",
                    starts_at=start_dt,
                    deadline=start_dt,
                )
            )
        return events


# --------------------------------------------------------------------------
# MOCK FALLBACK (Har halat me cards show karne ke liye)
# --------------------------------------------------------------------------
MOCK_EVENT_POOL: list[dict] = [
    {
        "source": "unstop",
        "title": "Smart India Hackathon - Internal Round",
        "url": "https://unstop.com/hackathons/sih-internal-round",
        "category": "hackathon",
        "mode": "hybrid",
        "location": "Bengaluru, Karnataka",
        "organizer": "MoE Innovation Cell",
        "description": "36-hour team hackathon on real problem statements from ministries. Teams of 6.",
        "start_in": 30,
        "deadline_in": 18,
    },
    {
        "source": "gdg",
        "title": "DevFest 2026 - Bengaluru",
        "url": "https://gdg.community.dev/events/devfest-bengaluru",
        "category": "conference",
        "mode": "offline",
        "location": "Bengaluru, Karnataka",
        "organizer": "GDG Bengaluru",
        "description": "The largest Google developer conference of the year: Android, Flutter, AI/ML and Cloud tracks.",
        "start_in": 45,
        "deadline_in": 40,
    },
    {
        "source": "devfolio",
        "title": "ETHIndia 2026 Hackathon",
        "url": "https://devfolio.co/hackathons/ethindia-2026",
        "category": "hackathon",
        "mode": "offline",
        "location": "Bengaluru, Karnataka",
        "organizer": "Devfolio",
        "description": "Asia's largest Ethereum hackathon. Build dApps, DeFi protocols or tooling.",
        "start_in": 60,
        "deadline_in": 50,
    },
]


def get_mock_events() -> list[ScrapedEvent]:
    return [
        ScrapedEvent(
            title=raw["title"],
            url=raw["url"],
            description=raw["description"],
            source=raw["source"],
            category=raw["category"],
            mode=raw["mode"],
            location=raw.get("location", ""),
            organizer=raw.get("organizer", ""),
            starts_at=days_from_now(raw["start_in"]),
            deadline=days_from_now(raw["deadline_in"]),
        )
        for raw in MOCK_EVENT_POOL
    ]


async def run_all_scrapers(
    include_live: bool = True,
) -> tuple[list[ScrapedEvent], list[str], list[str]]:
    """Runs all live JSON scrapers. Falls back to mock if empty."""
    scrapers: list[BaseScraper] = [
        KontestsLiveScraper(),
        UnstopLiveScraper(),
        DevfolioLiveScraper(),
        GDGLiveScraper(),
    ]

    collected: list[ScrapedEvent] = []
    errors: list[str] = []
    names: list[str] = []

    for scraper in scrapers:
        names.append(scraper.source_name)
        try:
            found = await scraper.fetch()
            collected.extend(found)
            logger.info("%s -> %d event(s)", scraper.source_name, len(found))
        except Exception as exc:
            message = f"{scraper.source_name}: {type(exc).__name__}: {exc}"
            logger.error("Scraper failed - %s", message)
            errors.append(message)

    # Fallback to canned pool if live internet block hone par bilkul empty ho
    if not collected:
        logger.warning("Live scraping returned 0 items; loading mock backup.")
        collected.extend(get_mock_events())

    return collected, errors, names


def build_simulated_event() -> ScrapedEvent:
    token = uuid.uuid4().hex[:8]
    return ScrapedEvent(
        title=f"Live Demo Event #{token}",
        url=f"https://example.com/events/live-demo-{token}",
        description="Synthetic event generated by demo trigger.",
        source="manual",
        category="hackathon",
        mode="online",
        organizer="Event Aggregator Demo",
        starts_at=days_from_now(10),
        deadline=days_from_now(7),
    )


def upsert_events(db: Session, scraped: list[ScrapedEvent]) -> tuple[list[Event], int]:
    new_events: list[Event] = []
    skipped = 0

    for item in scraped:
        url = normalize_url(item.url)
        if not url or not item.title.strip():
            skipped += 1
            continue

        already_exists = db.scalar(select(Event).where(Event.url == url))
        if already_exists:
            skipped += 1
            continue

        event = Event(
            title=item.title.strip()[:255],
            description=item.description,
            url=url,
            source=item.source,
            category=item.category,
            mode=item.mode,
            location=item.location,
            organizer=item.organizer,
            starts_at=item.starts_at,
            deadline=item.deadline,
        )
        db.add(event)
        new_events.append(event)

    db.commit()
    for event in new_events:
        db.refresh(event)

    return new_events, skipped