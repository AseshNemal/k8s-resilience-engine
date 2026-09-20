import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_URL = "sqlite:///./data/resilience.db"


@dataclass(frozen=True)
class Settings:
    database_url: str


def get_settings() -> Settings:
    """Load .env (if present) and resolve the database connection string.

    SQLite is the default so the app runs with no external services;
    PostgreSQL is used only when DATABASE_URL is explicitly set to one.
    """

    load_dotenv(PROJECT_ROOT / ".env")
    return Settings(database_url=os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))
