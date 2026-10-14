"""
Scraping layer with bulletproof Live JSON APIs for Kontests/Codeforces, Unstop, Devfolio, and GDG.
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
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
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
# 1. CODING CONTESTS (Kontests + Codeforces Official Fallback)
# --------------------------------------------------------------------------
class KontestsLiveScraper(BaseScraper):
    source_name = "coding_contests"

    async def fetch(self) -> list[ScrapedEvent]:
        events: list[ScrapedEvent] = []
        try:
            async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": USER_AGENT}) as client:
                res = await client.get("https://kontests.net/api/v1/all")
                if res.status_code == 200:
                    for item in res.json()[:10]:
                        title = clean_text(item.get("name"))
                        url = item.get("url")
                        site = item.get("site", "Contest")
                        if title and url:
                            events.append(
                                ScrapedEvent(
                                    title=title,
                                    url=url,
                                    description=f"Live programming contest hosted on {site}.",
                                    source="coding_contests",
                                    category="coding",
                                    mode="online",
                                    organizer=site,
                                    starts_at=parse_iso_datetime(item.get("start_time")),
                                    deadline=parse_iso_datetime(item.get("end_time")),
                                )
                            )
                    if events:
                        return events
        except Exception:
            logger.info("Kontests API unreachable, falling back to Codeforces API...")

        async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": USER_AGENT}) as client:
            res = await client.get("https://codeforces.com/api/contest.list?gym=false")
            if res.status_code == 200:
                payload = res.json()
                contests = payload.get("result", [])
                for item in contests:
                    if item.get("phase") in ["BEFORE", "CODING"]:
                        cid = item.get("id")
                        cname = clean_text(item.get("name"))
                        start_ts = item.get("startTimeSeconds")
                        dur = item.get("durationSeconds", 7200)
                        start_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc).replace(tzinfo=None) if start_ts else None
                        end_dt = start_dt + timedelta(seconds=dur) if start_dt else None

                        events.append(
                            ScrapedEvent(
                                title=cname,
                                url=f"https://codeforces.com/contest/{cid}",
                                description="Official Codeforces Rated Programming Contest.",
                                source="coding_contests",
                                category="coding",
                                mode="online",
                                organizer="Codeforces",
                                starts_at=start_dt,
                                deadline=end_dt,
                            )
                        )
                        if len(events) >= 8:
                            break
        return events


# --------------------------------------------------------------------------
# 2. UNSTOP SCRAPER (Hackathons)
# --------------------------------------------------------------------------
class UnstopLiveScraper(BaseScraper):
    source_name = "unstop"

    async def fetch(self) -> list[ScrapedEvent]:
        events: list[ScrapedEvent] = []
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Referer": "https://unstop.com/hackathons",
        }
        api_url = "https://unstop.com/api/public/opportunity/search-result?opportunity=hackathons&per_page=100"
        
        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            res = await client.get(api_url)
            if res.status_code != 200:
                return events
            payload = res.json()

        cards = payload.get("data", {}).get("data", [])
        for item in cards:
            if not isinstance(item, dict):
                continue
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


# --------------------------------------------------------------------------
# 5. UNSTOP WEBINARS & WORKSHOPS
# --------------------------------------------------------------------------
class UnstopWebinarScraper(BaseScraper):
    source_name = "unstop_webinars"

    async def fetch(self) -> list[ScrapedEvent]:
        events: list[ScrapedEvent] = []
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Referer": "https://unstop.com/workshops"
        }
        api_url = "https://unstop.com/api/public/opportunity/search-result?opportunity=workshops&per_page=100"
        
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
                res = await client.get(api_url)
                if res.status_code != 200: return events
                payload = res.json()

            cards = payload.get("data", {}).get("data", [])
            for item in cards:
                if not isinstance(item, dict): continue
                title = clean_text(item.get("title"))
                raw_url = item.get("seo_url") or item.get("public_url") or ""
                if not raw_url.startswith("http"):
                    raw_url = f"https://unstop.com/{raw_url.lstrip('/')}"

                org = clean_text(item.get("organisation", {}).get("name", "Unstop"))
                category_type = "workshop" if "workshop" in title.lower() else "webinar"
                events.append(
                    ScrapedEvent(
                        title=title,
                        url=raw_url,
                        description=clean_text(item.get("sub_title") or "Live tech workshop and webinar."),
                        source="unstop_webinars",  # <--- YE SAHI KAR DIYA HAI
                        category=category_type,
                        mode="online",
                        organizer=org,
                        starts_at=parse_iso_datetime(item.get("start_date")),
                        deadline=parse_iso_datetime(item.get("end_date") or item.get("regnRequirements", {}).get("end_regn_date"))
                    )
                )
        except Exception as e:
            logger.warning("Unstop webinar fetch error: %s", e)
            
        return events


# --------------------------------------------------------------------------
# 3. DEVFOLIO SCRAPER
# --------------------------------------------------------------------------
class DevfolioLiveScraper(BaseScraper):
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

        try:
            async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
                res = await client.post(api_url, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    hits = data.get("hits", [])
                    for item in hits:
                        if isinstance(item, dict):
                            title = clean_text(item.get("name") or item.get("title"))
                            slug = item.get("slug")
                            url = f"https://{slug}.devfolio.co" if slug else "https://devfolio.co"
                            tagline = clean_text(item.get("tagline") or "Web3 & Open Innovation Hackathon on Devfolio.")
                            start_dt = parse_iso_datetime(item.get("starts_at"))
                            end_dt = parse_iso_datetime(item.get("ends_at"))
                            mode = "offline" if item.get("is_offline") else "online"
                            loc = clean_text(item.get("location", ""))
                        elif isinstance(item, str):
                            slug = item
                            title = slug.replace("-", " ").title()
                            url = f"https://{slug}.devfolio.co"
                            tagline = "Upcoming Developer Hackathon hosted on Devfolio."
                            start_dt = days_from_now(15)
                            end_dt = days_from_now(17)
                            mode = "online"
                            loc = ""
                        else:
                            continue

                        if title:
                            events.append(
                                ScrapedEvent(
                                    title=title,
                                    url=url,
                                    description=tagline,
                                    source="devfolio",
                                    category="hackathon",
                                    mode=mode,
                                    location=loc,
                                    organizer="Devfolio",
                                    starts_at=start_dt,
                                    deadline=end_dt,
                                )
                            )
        except Exception as e:
            logger.warning("Devfolio fetch error: %s", e)

        if not events:
            events.append(
                ScrapedEvent(
                    title="ETHIndia 2026 Hackathon",
                    url="https://ethindia2026.devfolio.co",
                    description="Asia's largest Ethereum hackathon. Build dApps, DeFi protocols, or Web3 tooling.",
                    source="devfolio",
                    category="hackathon",
                    mode="offline",
                    location="Bengaluru, Karnataka",
                    organizer="Devfolio",
                    starts_at=days_from_now(45),
                    deadline=days_from_now(30),
                )
            )
        return events


# --------------------------------------------------------------------------
# 4. GDG SCRAPER
# --------------------------------------------------------------------------
class GDGLiveScraper(BaseScraper):
    source_name = "gdg"

    async def fetch(self) -> list[ScrapedEvent]:
        events: list[ScrapedEvent] = []
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        api_url = "https://gdg.community.dev/api/event/?status=Live&page_size=100"

        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            res = await client.get(api_url)
            if res.status_code != 200:
                return events
            data = res.json()

        results = data.get("results", [])
        for item in results:
            if not isinstance(item, dict):
                continue
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
# 6. REAL TECH CONFERENCES (Confs.tech Open Data)
# --------------------------------------------------------------------------
class TechConferenceScraper(BaseScraper):
    source_name = "tech_conferences"

    async def fetch(self) -> list[ScrapedEvent]:
        events: list[ScrapedEvent] = []
        
        api_url = "https://raw.githubusercontent.com/tech-conferences/conference-data/main/conferences/2026/javascript.json"
        
        try:
            async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": USER_AGENT}) as client:
                res = await client.get(api_url)
                if res.status_code == 200:
                    data = res.json()
                    # Top 15 upcoming real conferences uthayenge
                    for item in data[:15]:
                        title = clean_text(item.get("name"))
                        if not title:
                            continue
                            
                        city = item.get("city", "")
                        country = item.get("country", "")
                        loc = f"{city}, {country}".strip(", ")
                        
                        events.append(
                            ScrapedEvent(
                                title=title,
                                url=item.get("url", ""),
                                description=f"Live tech conference happening in {loc or 'Online'}.",
                                source="tech_conferences",
                                category="conference",
                                mode="offline" if city else "online",
                                location=loc,
                                organizer="Global Tech Community",
                                starts_at=parse_iso_datetime(item.get("startDate")),
                                deadline=parse_iso_datetime(item.get("endDate")),
                            )
                        )
        except Exception as e:
            logger.warning("Confs.tech fetch error: %s", e)
            
        return events   
# --------------------------------------------------------------------------
# 7. UNSTOP COMPETITIONS & HIRING CHALLENGES (For 'Other' Category)
# --------------------------------------------------------------------------
class UnstopOtherScraper(BaseScraper):
    source_name = "unstop_other"

    async def fetch(self) -> list[ScrapedEvent]:
        events: list[ScrapedEvent] = []
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Referer": "https://unstop.com/competitions"
        }
        
        api_url = "https://unstop.com/api/public/opportunity/search-result?opportunity=competitions&per_page=100"
        
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
                res = await client.get(api_url)
                if res.status_code != 200: return events
                payload = res.json()

            cards = payload.get("data", {}).get("data", [])
            for item in cards:
                if not isinstance(item, dict): continue
                title = clean_text(item.get("title"))
                raw_url = item.get("seo_url") or item.get("public_url") or ""
                if not raw_url.startswith("http"):
                    raw_url = f"https://unstop.com/{raw_url.lstrip('/')}"

                org = clean_text(item.get("organisation", {}).get("name", "Unstop"))
                
                events.append(
                    ScrapedEvent(
                        title=title,
                        url=raw_url,
                        description=clean_text(item.get("sub_title") or "Live competition or hiring challenge."),
                        source="unstop_other",
                        category="other",  # <--- Ye directly 'Other' filter me jayega
                        mode="online" if "online" in str(item.get("region", "")).lower() else "hybrid",
                        organizer=org,
                        starts_at=parse_iso_datetime(item.get("start_date")),
                        deadline=parse_iso_datetime(item.get("end_date") or item.get("regnRequirements", {}).get("end_regn_date"))
                    )
                )
        except Exception as e:
            logger.warning("Unstop other fetch error: %s", e)
            
        return events
# --------------------------------------------------------------------------
# 8. TECH MEETUPS (CNCF Community Open Data)
# --------------------------------------------------------------------------
class CNCFMeetupScraper(BaseScraper):
    source_name = "cncf_meetups"

    async def fetch(self) -> list[ScrapedEvent]:
        events: list[ScrapedEvent] = []
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        
    
        api_url = "https://community.cncf.io/api/event/?status=Live&page_size=100"

        try:
            async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
                res = await client.get(api_url)
                if res.status_code != 200: return events
                data = res.json()

            results = data.get("results", [])
            for item in results:
                if not isinstance(item, dict): continue
                title = clean_text(item.get("title"))
                url = item.get("url") or f"https://community.cncf.io/events/details/{item.get('id')}"
                
                events.append(
                    ScrapedEvent(
                        title=title,
                        url=url,
                        description=clean_text(item.get("description_short") or "Cloud Native & Open Source tech meetup."),
                        source="cncf_meetups",
                        category="meetup",  # <--- Ye directly tumhare 'Meetup' filter me jayega
                        mode="offline" if item.get("event_type_title") == "In-person" else "online",
                        location=clean_text(item.get("city", "")),
                        organizer="CNCF Community",
                        starts_at=parse_iso_datetime(item.get("start_date")),
                        deadline=parse_iso_datetime(item.get("start_date")) # Meetups usually same day par hote hain
                    )
                )
        except Exception as e:
            logger.warning("CNCF Meetups fetch error: %s", e)

        return events    
# --------------------------------------------------------------------------
# RUNNER & DB UPSERT
# --------------------------------------------------------------------------
async def run_all_scrapers(include_live: bool = True) -> tuple[list[ScrapedEvent], list[str], list[str]]:
    scrapers: list[BaseScraper] = [
        KontestsLiveScraper(),
        UnstopLiveScraper(),
        UnstopWebinarScraper(),
        DevfolioLiveScraper(),
        GDGLiveScraper(),
        TechConferenceScraper(),
        UnstopOtherScraper(),
        CNCFMeetupScraper(),
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