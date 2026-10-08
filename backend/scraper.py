"""
Scraping layer.

Design goals
------------
1. Every source implements the same `BaseScraper` interface, so adding Unstop,
   Devfolio, GDG or MLH later is just "write one class, register it".
2. Mock scrapers are ON by default so the prototype always has data to show,
   even with no internet / no API keys.
3. Live scrapers are real BeautifulSoup skeletons but DISABLED by default.
   Flip ENABLE_LIVE_SCRAPERS=true once you have checked the site's robots.txt
   and Terms of Service.

IMPORTANT for your report/viva:
Most modern event sites (Unstop, Devfolio, GDG) render their listings with
JavaScript. `requests`/`httpx` only download the initial HTML, so the CSS
selectors below will return nothing until you either (a) find their internal
JSON API in the browser Network tab, or (b) switch to Playwright/Selenium.
That is a documented, expected limitation of this prototype.
"""
from __future__ import annotations

import abc
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Event

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (compatible; EventAggregatorBot/0.1; "
    "+https://github.com/your-team/event-aggregator)"
)

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "fbclid", "gclid",
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def days_from_now(days: float) -> datetime:
    return utcnow() + timedelta(days=days)


def normalize_url(url: str) -> str:
    """
    Canonicalise a URL so the same event is never stored twice.

    - lowercases scheme + host
    - drops tracking query params
    - removes trailing slash
    """
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
    """Collapse whitespace and strip - scraped HTML is always messy."""
    if not value:
        return ""
    return " ".join(value.split()).strip()


# --------------------------------------------------------------------------
# Data transfer object between scrapers and the database
# --------------------------------------------------------------------------
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


# --------------------------------------------------------------------------
# Base scraper interface
# --------------------------------------------------------------------------
class BaseScraper(abc.ABC):
    """Every scraper must expose a name and an async `fetch()`."""

    source_name: str = "unknown"
    enabled_by_default: bool = True

    @abc.abstractmethod
    async def fetch(self) -> list[ScrapedEvent]:
        """Return a list of freshly scraped events."""
        raise NotImplementedError


# --------------------------------------------------------------------------
# MOCK SCRAPERS - always work, used for the demo
# --------------------------------------------------------------------------
MOCK_EVENT_POOL: list[dict] = [
    {
        "source": "unstop",
        "title": "Unstop Igniters: CodeSprint 4.0",
        "url": "https://unstop.com/competitions/codesprint-4",
        "category": "coding",
        "mode": "online",
        "organizer": "Unstop",
        "description": "A 3-hour competitive programming sprint with 6 DSA problems. Open to all UG students.",
        "start_in": 14,
        "deadline_in": 9,
    },
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
        "source": "unstop",
        "title": "Data Structures Bootcamp for Beginners",
        "url": "https://unstop.com/workshops/dsa-bootcamp",
        "category": "workshop",
        "mode": "online",
        "organizer": "Coding Ninjas",
        "description": "Four-day live bootcamp covering arrays, linked lists, stacks, queues and recursion.",
        "start_in": 5,
        "deadline_in": 3,
    },
    {
        "source": "gdg",
        "title": "GDG Cloud Study Jam: Build with Gemini API",
        "url": "https://gdg.community.dev/events/cloud-study-jam-gemini",
        "category": "gdg",
        "mode": "online",
        "organizer": "Google Developer Groups",
        "description": "Hands-on session on the Gemini API, prompt design and deploying to Cloud Run.",
        "start_in": 7,
        "deadline_in": 6,
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
    {
        "source": "devfolio",
        "title": "Hack The Mountains 5.0",
        "url": "https://devfolio.co/hackathons/hack-the-mountains",
        "category": "hackathon",
        "mode": "online",
        "organizer": "Devfolio",
        "description": "Beginner-friendly 36-hour online hackathon with mentorship and swag for all participants.",
        "start_in": 20,
        "deadline_in": 16,
    },
    {
        "source": "community",
        "title": "Intro to System Design - Free Webinar",
        "url": "https://example-community.dev/events/system-design-webinar",
        "category": "webinar",
        "mode": "online",
        "organizer": "Scaler Academy",
        "description": "Learn how to approach scalable system design interviews with real case studies.",
        "start_in": 3,
        "deadline_in": 2,
    },
]


class MockScraper(BaseScraper):
    """Returns a filtered slice of the canned pool, with fresh dates each run."""

    def __init__(self, source_name: str) -> None:
        self.source_name = source_name

    async def fetch(self) -> list[ScrapedEvent]:
        results: list[ScrapedEvent] = []
        for raw in MOCK_EVENT_POOL:
            if raw["source"] != self.source_name:
                continue
            results.append(
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
            )
        return results


class MockUnstopScraper(MockScraper):
    def __init__(self) -> None:
        super().__init__("unstop")


class MockGDGScraper(MockScraper):
    def __init__(self) -> None:
        super().__init__("gdg")


class MockDevfolioScraper(MockScraper):
    def __init__(self) -> None:
        super().__init__("devfolio")


# --------------------------------------------------------------------------
# LIVE SCRAPERS - real BeautifulSoup skeletons, disabled by default
# --------------------------------------------------------------------------
class BeautifulSoupScraper(BaseScraper):
    """
    Template for any HTML-based source.

    Subclasses only need to define:
        source_name, list_url, and parse(html) -> list[ScrapedEvent]
    """

    list_url: str = ""
    enabled_by_default = False

    async def fetch(self) -> list[ScrapedEvent]:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-IN,en;q=0.9",
        }
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as client:
            response = await client.get(self.list_url)
            response.raise_for_status()
        return self.parse(response.text)

    def parse(self, html: str) -> list[ScrapedEvent]:  # pragma: no cover
        raise NotImplementedError


class UnstopLiveScraper(BeautifulSoupScraper):
    """
    Skeleton for https://unstop.com/hackathons

    >>> SELECTORS BELOW ARE PLACEHOLDERS <<<
    Open the page, right-click an event card -> Inspect, and replace them.
    If the cards are rendered by JavaScript you will get an empty list -
    that is your signal to use Playwright instead.
    """

    source_name = "unstop"
    list_url = "https://unstop.com/hackathons"
    enabled_by_default = False

    def parse(self, html: str) -> list[ScrapedEvent]:
        soup = BeautifulSoup(html, "lxml")  # "html.parser" if lxml is not installed
        events: list[ScrapedEvent] = []

        # TODO: replace with the real card selector, e.g. "div.opportunity-card"
        for card in soup.select("div.event-card, article.event"):
            try:
                title_tag = card.select_one("h2, h3, .title")
                link_tag = card.select_one("a[href]")

                if not title_tag or not link_tag:
                    continue

                href = link_tag.get("href", "")
                if href.startswith("/"):
                    href = "https://unstop.com" + href

                events.append(
                    ScrapedEvent(
                        title=clean_text(title_tag.get_text()),
                        url=href,
                        description=clean_text(
                            (card.select_one(".description, p") or title_tag).get_text()
                        ),
                        source=self.source_name,
                        category="hackathon",
                        mode="online",
                        organizer="Unstop",
                        starts_at=None,   # parse from the card's date text
                        deadline=None,
                    )
                )
            except Exception as exc:  # one bad card must not kill the whole run
                logger.warning("Skipping malformed card on %s: %s", self.list_url, exc)

        return events


class GDGLiveScraper(BeautifulSoupScraper):
    """Same idea for a GDG chapter page."""

    source_name = "gdg"
    list_url = "https://gdg.community.dev/gdg-bangalore/"
    enabled_by_default = False

    def parse(self, html: str) -> list[ScrapedEvent]:
        soup = BeautifulSoup(html, "lxml")
        events: list[ScrapedEvent] = []

        # TODO: replace with the real selector
        for card in soup.select("div.event-card, li.event"):
            title_tag = card.select_one("h3, .event-title")
            link_tag = card.select_one("a[href]")
            if not title_tag or not link_tag:
                continue

            href = link_tag.get("href", "")
            if href.startswith("/"):
                href = "https://gdg.community.dev" + href

            events.append(
                ScrapedEvent(
                    title=clean_text(title_tag.get_text()),
                    url=href,
                    description=clean_text((card.select_one("p") or title_tag).get_text()),
                    source=self.source_name,
                    category="gdg",
                    mode="online",
                    organizer="Google Developer Groups",
                )
            )

        return events


# --------------------------------------------------------------------------
# Registry + orchestration
# --------------------------------------------------------------------------
def build_scraper_registry() -> list[BaseScraper]:
    from .config import settings

    scrapers: list[BaseScraper] = [
        MockUnstopScraper(),
        MockGDGScraper(),
        MockDevfolioScraper(),
    ]
    if settings.ENABLE_LIVE_SCRAPERS:
        scrapers += [UnstopLiveScraper(), GDGLiveScraper()]
    return scrapers


async def run_all_scrapers(
    include_live: bool = False,
) -> tuple[list[ScrapedEvent], list[str], list[str]]:
    """
    Run every registered scraper.

    Returns (events, errors, scraper_names).
    A failing scraper is recorded but never stops the others.
    """
    from .config import settings

    scrapers = [MockUnstopScraper(), MockGDGScraper(), MockDevfolioScraper()]
    if include_live or settings.ENABLE_LIVE_SCRAPERS:
        scrapers += [UnstopLiveScraper(), GDGLiveScraper()]

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

    return collected, errors, names


def build_simulated_event() -> ScrapedEvent:
    """Injects one guaranteed-new event so you can demo the Discord ping."""
    token = uuid.uuid4().hex[:8]
    return ScrapedEvent(
        title=f"Live Demo Event #{token}",
        url=f"https://example.com/events/live-demo-{token}",
        description="Synthetic event generated by the /api/scrape?simulate_new=true demo flag.",
        source="manual",
        category="hackathon",
        mode="online",
        organizer="Event Aggregator Demo",
        starts_at=days_from_now(10),
        deadline=days_from_now(7),
    )


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------
def upsert_events(db: Session, scraped: list[ScrapedEvent]) -> tuple[list[Event], int]:
    """
    Insert only genuinely new events (deduplicated by canonical URL).

    Returns (newly_inserted_events, number_skipped_as_duplicates).
    """
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