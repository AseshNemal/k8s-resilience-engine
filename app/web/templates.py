"""Shared Jinja2Templates instance for app/web/* page routers.

Kept separate from app/main.py so web routers can import it without a
circular import (main.py includes the web routers).
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates


BASE_DIR = Path(__file__).resolve().parent.parent

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
