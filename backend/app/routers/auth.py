from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ratelimit
from ..config import settings
from ..database import get_db
from ..deps import client_ip, get_current_user
from ..models import Invitation, Organization, User, utcnow
from ..schemas import AcceptInviteIn, ForgotIn, LoginIn, RegisterIn, ResetIn, TokenIn
from ..security import (create_token, decode_token, hash_password, hash_token, password_fingerprint,
                        verify_password)
from ..serializers import user_out
from ..services import audit, seed_templates, send_email, unique_slug

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _session(user: User) -> dict:
    return {"access_token": create_token(user.id), "token_type": "bearer", "user": user_out(user)}


@router.post("/register", status_code=201)
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    ratelimit.rate_limit(f"register:{ip}", 30, 3600)
    email = body.email.lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists")
    org = Organization(name=body.organization_name.strip(), slug=unique_slug(db, body.organization_name))
    db.add(org)
    db.flush()
    user = User(organization_id=org.id, name=body.name.strip(), email=email,
                password_hash=hash_password(body.password), role="org_admin", status="active")
    db.add(user)
    db.flush()
    seed_templates(db, org, user)
    audit(db, user, "ORGANIZATION_CREATED", "organization", org.id, f"Created organization {org.name}", ip=ip)
    vtoken = create_token(user.id, "verify", minutes=60 * 24 * 3)
    send_email(email, "Verify your email", f"Open {settings.APP_URL}/#/verify-email?token={vtoken}")
    db.commit()
    out = _session(user)
    if settings.DEBUG_RETURN_TOKENS:
        out["verification_token"] = vtoken
    return out


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    key = f"login:{ip}:{body.email.lower()}"
    ratelimit.rate_limit(key, 10, 300)
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password")
    if user.status == "invited":
        raise HTTPException(403, "Please accept your invitation first")
    if user.status != "active":
        raise HTTPException(403, "This account is disabled")
    ratelimit.reset(key)
    audit(db, user, "LOGIN", "user", user.id, f"{user.name} logged in", ip=ip)
    db.commit()
    return _session(user)


@router.post("/logout")
def logout(user: User = Depends(get_current_user)):
    # Tokens are stateless; the browser discards its token. Tokens expire on their own.
    return {"message": "Logged out"}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return user_out(user)


@router.post("/verify-email")
def verify_email(body: TokenIn, db: Session = Depends(get_db)):
    payload = decode_token(body.token, "verify")
    user = db.get(User, int(payload["sub"]))
    if not user:
        raise HTTPException(400, "Invalid token")
    user.email_verified = True
    db.commit()
    return {"message": "Email verified"}


@router.post("/forgot-password")
def forgot_password(body: ForgotIn, request: Request, db: Session = Depends(get_db)):
    ratelimit.rate_limit(f"forgot:{client_ip(request)}", 10, 3600)
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    out = {"message": "If that email exists, a reset link has been sent."}
    if user and user.status == "active":
        token = create_token(user.id, "reset", minutes=30, ph=password_fingerprint(user.password_hash))
        send_email(user.email, "Reset your password", f"Open {settings.APP_URL}/#/reset-password?token={token}")
        if settings.DEBUG_RETURN_TOKENS:
            out["reset_token"] = token
    return out


@router.post("/reset-password")
def reset_password(body: ResetIn, db: Session = Depends(get_db)):
    payload = decode_token(body.token, "reset")
    user = db.get(User, int(payload["sub"]))
    if not user or user.status != "active" or payload.get("ph") != password_fingerprint(user.password_hash):
        raise HTTPException(400, "This reset link is invalid or was already used")
    user.password_hash = hash_password(body.new_password)
    audit(db, user, "PASSWORD_RESET", "user", user.id, "Password was reset")
    db.commit()
    return {"message": "Password updated"}


@router.post("/accept-invite")
def accept_invite(body: AcceptInviteIn, request: Request, db: Session = Depends(get_db)):
    inv = db.scalar(select(Invitation).where(Invitation.token_hash == hash_token(body.token)))
    if not inv or inv.used or inv.expires_at < utcnow():
        raise HTTPException(400, "This invitation is invalid or has expired")
    user = db.get(User, inv.user_id)
    if not user or user.status == "disabled":
        raise HTTPException(400, "This invitation is invalid or has expired")
    user.password_hash = hash_password(body.password)
    user.status, user.email_verified = "active", True
    inv.used = True
    audit(db, user, "INVITE_ACCEPTED", "user", user.id, f"{user.name} accepted the invitation",
          client_id=user.client_id, ip=client_ip(request))
    db.commit()
    return _session(user)
