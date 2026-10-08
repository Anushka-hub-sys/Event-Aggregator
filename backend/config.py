"""
Central configuration. Everything is read from environment variables so that
no secrets ever end up hard-coded in the repository.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    APP_NAME = "Event Aggregator API"
    VERSION = "0.1.0"
    DESCRIPTION = "Centralises hackathons, GDG events, coding contests and Unstop challenges."

    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'events.db'}")

    DISCORD_WEBHOOK_URL: str = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

    # Add this line for your AI features:
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "").strip()

    CORS_ORIGINS: list[str] = [
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "*").split(",")
        if origin.strip()
    ]

    SEED_ON_STARTUP: bool = _as_bool(os.getenv("SEED_ON_STARTUP"), True)
    ENABLE_LIVE_SCRAPERS: bool = _as_bool(os.getenv("ENABLE_LIVE_SCRAPERS"), False)


settings = Settings()