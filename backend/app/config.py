import os
from pathlib import Path


class Settings:
    """All settings come from environment variables (see .env.example)."""

    def __init__(self) -> None:
        self.DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./onboarding.db")
        self.SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-secret-change-me-before-production-0123456789")
        self.ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "480"))
        self.INVITE_DAYS = int(os.getenv("INVITE_DAYS", "7"))
        self.STORAGE_DIR = Path(os.getenv("STORAGE_DIR", "./storage")).resolve()
        self.MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "25"))
        self.APP_URL = os.getenv("APP_URL", "http://localhost:8000")
        self.CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
        # Email (optional). If SMTP_HOST is empty, emails are only written to the log.
        self.SMTP_HOST = os.getenv("SMTP_HOST", "")
        self.SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
        self.SMTP_USER = os.getenv("SMTP_USER", "")
        self.SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
        self.EMAIL_FROM = os.getenv("EMAIL_FROM", "no-reply@onboarding.local")
        # Development helper: return reset/verify tokens in API responses.
        self.DEBUG_RETURN_TOKENS = os.getenv("DEBUG_RETURN_TOKENS", "false").lower() == "true"
        # Background reminder loop. 0 = off.
        self.REMINDER_INTERVAL_MINUTES = int(os.getenv("REMINDER_INTERVAL_MINUTES", "60"))
        self.PLATFORM_ADMIN_EMAIL = os.getenv("PLATFORM_ADMIN_EMAIL", "")
        self.PLATFORM_ADMIN_PASSWORD = os.getenv("PLATFORM_ADMIN_PASSWORD", "")


settings = Settings()
