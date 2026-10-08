import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from . import models  # noqa: F401  (registers tables)
from .config import settings
from .database import Base, SessionLocal, engine
from .routers import auth, documents, misc, onboarding, org
from .security import hash_password
from .services import run_reminders

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("onboarding")


def _seed_platform_admin() -> None:
    if not (settings.PLATFORM_ADMIN_EMAIL and settings.PLATFORM_ADMIN_PASSWORD):
        return
    with SessionLocal() as db:
        email = settings.PLATFORM_ADMIN_EMAIL.lower()
        if not db.scalar(select(models.User.id).where(models.User.email == email)):
            db.add(models.User(name="Platform Admin", email=email, role="platform_admin", status="active",
                               email_verified=True, password_hash=hash_password(settings.PLATFORM_ADMIN_PASSWORD)))
            db.commit()


def _reminder_loop(stop: threading.Event) -> None:
    while not stop.wait(settings.REMINDER_INTERVAL_MINUTES * 60):
        try:
            with SessionLocal() as db:
                log.info("Reminder run: %s", run_reminders(db))
        except Exception:  # noqa: BLE001
            log.exception("Reminder run failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    _seed_platform_admin()
    stop = threading.Event()
    if settings.REMINDER_INTERVAL_MINUTES > 0:
        threading.Thread(target=_reminder_loop, args=(stop,), daemon=True).start()
    yield
    stop.set()


app = FastAPI(title="Client Onboarding Portal", version="1.0.0", lifespan=lifespan)

if settings.CORS_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS, allow_credentials=False,
                       allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if not request.url.path.startswith("/docs") and not request.url.path.startswith("/openapi"):
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
    return response


for r in (auth.router, org.router, onboarding.router, documents.router, misc.router):
    app.include_router(r)


@app.get("/api/health", tags=["meta"])
def health():
    return {"status": "ok"}


_default_front = Path(__file__).resolve().parents[2] / "frontend"
FRONTEND_DIR = Path(os.getenv("FRONTEND_DIR", str(_default_front)))
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
