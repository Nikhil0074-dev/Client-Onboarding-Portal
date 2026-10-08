import hashlib
import secrets
from datetime import timedelta

import bcrypt
import jwt
from fastapi import HTTPException

from .config import settings
from .models import utcnow

ALGO = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], hashed.encode())
    except ValueError:
        return False


def password_fingerprint(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def create_token(sub: int, purpose: str = "access", minutes: int | None = None, **extra) -> str:
    minutes = minutes if minutes is not None else settings.ACCESS_TOKEN_MINUTES
    payload = {"sub": str(sub), "purpose": purpose, "exp": utcnow() + timedelta(minutes=minutes), **extra}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGO)


def decode_token(token: str, purpose: str = "access") -> dict:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGO])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if payload.get("purpose") != purpose:
        raise HTTPException(status_code=401, detail="Invalid token type")
    return payload


def new_invite_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
