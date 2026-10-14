"""FastAPI application entrypoint."""
import logging
from contextlib import asynccontextmanager
from typing import Literal



from pydantic import BaseModel
import requests

from groq import Groq
from pydantic import BaseModel
import os
from dotenv import load_dotenv
load_dotenv()
# Groq Setup - Simple aur 100% working
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

class ChatRequest(BaseModel):
    text: str


from sentence_transformers import SentenceTransformer
import numpy as np

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from . import models, schemas
from .config import BASE_DIR, settings
from .database import Base, SessionLocal, engine, get_db
from .notifier import notify_new_events, send_test_notification
from .scraper import (
    build_simulated_event,
    normalize_url,
    run_all_scrapers,
    upsert_events,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("event-aggregator")


# --------------------------------------------------------------------------
# Startup / shutdown
# --------------------------------------------------------------------------
async def seed_if_empty() -> None:
    """Populate the database with mock events the very first time we boot."""
    with SessionLocal() as db:
        existing = db.scalar(select(func.count()).select_from(models.Event)) or 0

    if existing > 0:
        logger.info("Database already has %d event(s) - skipping seed.", existing)
        return

    scraped, errors, _ = await run_all_scrapers(include_live=False)
    with SessionLocal() as db:
        new_events, _ = upsert_events(db, scraped)

    logger.info("Seeded %d event(s). Errors: %s", len(new_events), errors or "none")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    logger.info("Tables ready.")

    if settings.SEED_ON_STARTUP:
        await seed_if_empty()

    if not settings.DISCORD_WEBHOOK_URL:
        logger.warning("DISCORD_WEBHOOK_URL is empty - notifications are disabled.")

    yield

    logger.info("Shutting down Event Aggregator API.")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    description=settings.DESCRIPTION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=False,  # must stay False while allow_origins is "*"
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------
@app.get("/api/health", tags=["meta"])
def health() -> dict:
    return {
        "status": "ok",
        "version": settings.VERSION,
        "discord_configured": bool(settings.DISCORD_WEBHOOK_URL),
        "live_scrapers_enabled": settings.ENABLE_LIVE_SCRAPERS,
    }


# --------------------------------------------------------------------------
# Events CRUD
# --------------------------------------------------------------------------
@app.get("/api/events", response_model=schemas.EventListOut, tags=["events"])
def list_events(
    q: str | None = Query(None, description="Free-text search in title / description / organizer"),
    category: str | None = Query(None),
    mode: str | None = Query(None),
    source: str | None = Query(None),
    upcoming_only: bool = Query(False, description="Only events whose deadline has not passed"),
    featured_only: bool = Query(False),
    sort: Literal["recent", "deadline", "title"] = Query("recent"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    conditions = []

    if q:
        pattern = f"%{q.strip()}%"
        conditions.append(
            or_(
                models.Event.title.ilike(pattern),
                models.Event.description.ilike(pattern),
                models.Event.organizer.ilike(pattern),
            )
        )
    if category:
        conditions.append(models.Event.category == category.strip().lower())
    if mode:
        conditions.append(models.Event.mode == mode.strip().lower())
    if source:
        conditions.append(models.Event.source == source.strip().lower())
    if featured_only:
        conditions.append(models.Event.is_featured.is_(True))

    if upcoming_only:
        now = models.utcnow()
        conditions.append(
            or_(
                models.Event.deadline >= now,
                and_(models.Event.deadline.is_(None), models.Event.starts_at >= now),
            )
        )

    base_stmt = select(models.Event).where(*conditions)

    total = db.scalar(select(func.count()).select_from(base_stmt.subquery())) or 0

    if sort == "deadline":
        order = models.Event.deadline.asc().nulls_last()
    elif sort == "title":
        order = models.Event.title.asc()
    else:
        order = models.Event.created_at.desc()

    items = db.scalars(base_stmt.order_by(order).limit(limit).offset(offset)).all()

    return schemas.EventListOut(total=total, count=len(items), items=items)


@app.get("/api/events/{event_id}", response_model=schemas.EventOut, tags=["events"])
def get_event(event_id: int, db: Session = Depends(get_db)):
    event = db.get(models.Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found.")
    return event


@app.post(
    "/api/events",
    response_model=schemas.EventOut,
    status_code=status.HTTP_201_CREATED,
    tags=["events"],
)
def create_event(
    payload: schemas.EventCreate,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    url = normalize_url(payload.url)
    if not url:
        raise HTTPException(status_code=422, detail="A valid 'url' is required.")

    if db.scalar(select(models.Event).where(models.Event.url == url)):
        raise HTTPException(status_code=409, detail="An event with this URL already exists.")

    data = payload.model_dump()
    data["url"] = url

    event = models.Event(**data)
    db.add(event)
    db.commit()
    db.refresh(event)

    event_out = schemas.EventOut.model_validate(event)

    # Fire-and-forget: the HTTP response does not wait for Discord.
    background.add_task(notify_new_events, [event_out])

    return event_out


@app.patch("/api/events/{event_id}", response_model=schemas.EventOut, tags=["events"])
def update_event(event_id: int, payload: schemas.EventUpdate, db: Session = Depends(get_db)):
    event = db.get(models.Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found.")

    updates = payload.model_dump(exclude_unset=True)

    if "url" in updates:
        new_url = normalize_url(updates["url"])
        if not new_url:
            raise HTTPException(status_code=422, detail="A valid 'url' is required.")
        clash = db.scalar(
            select(models.Event).where(models.Event.url == new_url, models.Event.id != event_id)
        )
        if clash:
            raise HTTPException(status_code=409, detail="Another event already uses this URL.")
        updates["url"] = new_url

    if "category" in updates and updates["category"]:
        updates["category"] = schemas.EventBase.normalise_category(updates["category"])

    for field_name, value in updates.items():
        setattr(event, field_name, value)

    db.commit()
    db.refresh(event)
    return event


@app.delete("/api/events/{event_id}", response_model=schemas.MessageOut, tags=["events"])
def delete_event(event_id: int, db: Session = Depends(get_db)):
    event = db.get(models.Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found.")

    db.delete(event)
    db.commit()
    return schemas.MessageOut(detail=f"Event {event_id} deleted.")


# --------------------------------------------------------------------------
# Scraping
# --------------------------------------------------------------------------
@app.post("/api/scrape", response_model=schemas.ScrapeResult, tags=["scraping"])
async def scrape_events(
    include_live: bool = Query(False, description="Also run the real BeautifulSoup scrapers"),
    simulate_new: bool = Query(False, description="Inject one guaranteed-new event (demo mode)"),
    notify: bool = Query(True, description="Send Discord notifications for new events"),
    db: Session = Depends(get_db),
):
    """
    Run every scraper, insert only new events, and ping Discord for each one.

    NOTE: this endpoint is `async` but uses a synchronous DB session, which
    briefly blocks the event loop. That is fine for SQLite + a mini-project
    workload; move to `asyncpg` + `AsyncSession` if you ever scale up.
    """
    scraped, errors, scraper_names = await run_all_scrapers(include_live=include_live)

    if simulate_new:
        scraped.append(build_simulated_event())

    new_events, skipped = upsert_events(db, scraped)
    new_out = [schemas.EventOut.model_validate(event) for event in new_events]

    notifications_sent = 0
    if notify and new_out:
        notifications_sent = await notify_new_events(new_out)

    logger.info(
        "Scrape finished: found=%d added=%d skipped=%d notified=%d",
        len(scraped), len(new_out), skipped, notifications_sent,
    )

    return schemas.ScrapeResult(
        scrapers_run=scraper_names,
        events_found=len(scraped),
        events_added=len(new_out),
        events_skipped=skipped,
        notifications_sent=notifications_sent,
        errors=errors,
        new_events=new_out,
    )


# --------------------------------------------------------------------------
# Stats
# --------------------------------------------------------------------------
@app.get("/api/stats", response_model=schemas.StatsOut, tags=["meta"])
def get_stats(db: Session = Depends(get_db)):
    now = models.utcnow()

    total = db.scalar(select(func.count()).select_from(models.Event)) or 0

    upcoming = db.scalar(
        select(func.count())
        .select_from(models.Event)
        .where(
            or_(
                models.Event.deadline >= now,
                and_(models.Event.deadline.is_(None), models.Event.starts_at >= now),
            )
        )
    ) or 0

    by_category = {
        row[0]: row[1]
        for row in db.execute(
            select(models.Event.category, func.count()).group_by(models.Event.category)
        ).all()
    }

    by_source = {
        row[0]: row[1]
        for row in db.execute(
            select(models.Event.source, func.count()).group_by(models.Event.source)
        ).all()
    }

    return schemas.StatsOut(
        total_events=total,
        upcoming_events=upcoming,
        by_category=by_category,
        by_source=by_source,
    )


# --------------------------------------------------------------------------
# Notifications
# --------------------------------------------------------------------------
@app.post("/api/notify/test", response_model=schemas.MessageOut, tags=["notifications"])
async def notify_test():
    """Ping your Discord webhook so you can verify the setup."""
    result = await send_test_notification()
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["detail"])
    return schemas.MessageOut(detail=result["detail"])

@app.get("/api/ai-search")
def semantic_search(query: str, db: Session = Depends(get_db)):
    events = db.execute(select(models.Event)).scalars().all()
    if not events or not query.strip():
        return []
    
    event_texts = [f"{e.title} {e.category} {e.description} {e.mode}" for e in events]
    query_vec = ai_model.encode([query])[0]
    event_vecs = ai_model.encode(event_texts)
    
    results = []
    for i, ev_vec in enumerate(event_vecs):
        score = np.dot(query_vec, ev_vec) / (np.linalg.norm(query_vec) * np.linalg.norm(ev_vec))
        if score > 0.15:
            event_dict = {
                "id": events[i].id,
                "title": events[i].title,
                "url": events[i].url,
                "description": events[i].description,
                "source": events[i].source,
                "category": events[i].category,
                "mode": events[i].mode,
                "location": events[i].location,
                "organizer": events[i].organizer,
                "starts_at": events[i].starts_at,
                "deadline": events[i].deadline,
                "ai_score": round(float(score * 100), 1)
            }
            results.append(event_dict)
            
    results.sort(key=lambda x: x["ai_score"], reverse=True)
    return results[:10]

@app.post("/api/chat")
def chat_bot(req: ChatRequest, db: Session = Depends(get_db)):
    user_query = req.text
    
    events = db.execute(select(models.Event).order_by(models.Event.starts_at).limit(100)).scalars().all()
    # Fix 1: Agar date na ho toh 'None' ki jagah 'TBA' (To Be Announced) dikhaye
    events_list = "\n".join([
        f"- {e.title} ({e.category}) at {e.location or 'Online'} on {e.starts_at or 'TBA'}." 
        for e in events
    ])
    
    prompt = f"""You are a smart, cool AI Assistant for a Tech Event platform. 
    Here is the list of upcoming events in our database:
    {events_list}
    
    User message: "{user_query}"
    
    CRITICAL RULES:
    1. CRITICAL RULES:
    1. IF THE USER GREETS YOU (e.g., "hi", "hello", "hey"): Just reply with EXACTLY "Hello! I am your AI Event Assistant. What kind of tech events or meetups are you looking to attend today?" DO NOT list any events.
    2. ONLY suggest events from the database that match what the user specifically asked for.
    3. IF NO MATCH IS FOUND: Politely say "Sorry, I don't have any events matching that right now."
    4. Format the events nicely with bullet points and emojis (📅, 📍). Keep it concise!
    """
    
    try:
        # Saare models ki list nikalo
        all_models = [m.id for m in client.models.list().data]
        
        # Kachra models (guard, whisper) hata do
        chat_models = [m for m in all_models if "guard" not in m.lower() and "whisper" not in m.lower()]
        
        # Sabse powerful fallback priority list
        working_model = chat_models[0] # Default fallback
        for m in ["llama-3.1-70b-versatile", "llama-3.2-90b-text-preview", "llama3-70b-8192", "gemma2-9b-it"]:
            if m in chat_models:
                working_model = m
                break
                
        logger.info(f"Final working model: {working_model}")
        
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=working_model, 
            temperature=0.3
        )
        return {"reply": chat_completion.choices[0].message.content}
        
    except Exception as e:
        logger.error(f"Chatbot error: {e}")
        return {"reply": "Sorry, my AI brain is sleeping right now. Try again later!"}
# --------------------------------------------------------------------------
# Static frontend (must be mounted LAST so /api/* wins)
# --------------------------------------------------------------------------
FRONTEND_DIR = BASE_DIR / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    logger.info("Serving frontend from %s", FRONTEND_DIR)
else:
    logger.warning("frontend/ directory not found - only the API will be served.")


# Yahan se AI search aur Chatbot ka code shuru hota hai
logger.info("Loading AI Model for Semantic Search...")
ai_model = SentenceTransformer('all-MiniLM-L6-v2')


# Ye block hamesha file ke last me hi rehna chahiye
if __name__ == "__main__":  # pragma: no cover
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)