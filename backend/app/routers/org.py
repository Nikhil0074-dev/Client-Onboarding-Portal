from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import client_ip, get_client_for, get_current_user, require_admin, require_staff
from ..models import AuditLog, Client, Invitation, Project, Template, TemplateTask, User, utcnow
from ..schemas import (ClientIn, ClientUpdate, TeamMemberIn, TeamMemberUpdate, TemplateIn, TemplateUpdate)
from ..security import hash_password, new_invite_token
from ..serializers import audit_out, client_out, project_out, template_out, user_out
from ..services import audit, send_email

router = APIRouter(prefix="/api", tags=["organization"])


# ------------------------------------------------------------------ team

@router.get("/team")
def list_team(user: User = Depends(require_staff), db: Session = Depends(get_db)):
    rows = db.scalars(select(User).where(User.organization_id == user.organization_id,
                                         User.role.in_(("org_admin", "team_member"))).order_by(User.name))
    return [user_out(u) for u in rows]


@router.post("/team", status_code=201)
def add_team_member(body: TeamMemberIn, request: Request, admin: User = Depends(require_admin),
                    db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists")
    u = User(organization_id=admin.organization_id, name=body.name.strip(), email=email,
             password_hash=hash_password(body.password), role=body.role, status="active")
    db.add(u)
    db.flush()
    audit(db, admin, "TEAM_MEMBER_ADDED", "user", u.id, f"Added {u.name} as {u.role}", ip=client_ip(request))
    db.commit()
    return user_out(u)


@router.patch("/team/{user_id}")
def update_team_member(user_id: int, body: TeamMemberUpdate, request: Request,
                       admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u or u.organization_id != admin.organization_id or u.role == "client":
        raise HTTPException(404, "Team member not found")
    if u.id == admin.id and (body.status == "disabled" or (body.role and body.role != "org_admin")):
        raise HTTPException(400, "You cannot disable or demote yourself")
    before = {"role": u.role, "status": u.status}
    for field in ("name", "role", "status"):
        v = getattr(body, field)
        if v is not None:
            setattr(u, field, v)
    audit(db, admin, "TEAM_MEMBER_UPDATED", "user", u.id, f"Updated {u.name}", ip=client_ip(request),
          meta={"previous": before, "new": {"role": u.role, "status": u.status}})
    db.commit()
    return user_out(u)


# ------------------------------------------------------------------ clients

def _check_assignee(db: Session, user: User, assignee_id: Optional[int]) -> None:
    if assignee_id is None:
        return
    a = db.get(User, assignee_id)
    if not a or a.organization_id != user.organization_id or a.role not in ("org_admin", "team_member"):
        raise HTTPException(422, "Assigned user must be a member of your team")


@router.get("/clients")
def list_clients(q: Optional[str] = None, status: Optional[str] = None, assigned_user_id: Optional[int] = None,
                 limit: int = 100, offset: int = 0, user: User = Depends(require_staff),
                 db: Session = Depends(get_db)):
    stmt = select(Client).where(Client.organization_id == user.organization_id)
    if status:
        stmt = stmt.where(Client.status == status)
    else:
        stmt = stmt.where(Client.status != "Archived")
    if assigned_user_id:
        stmt = stmt.where(Client.assigned_user_id == assigned_user_id)
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(Client.name).like(like), func.lower(Client.company_name).like(like),
                              func.lower(Client.email).like(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = list(db.scalars(stmt.order_by(Client.id.desc()).limit(min(max(limit, 1), 200)).offset(max(offset, 0))))
    projects = db.scalars(select(Project).where(Project.client_id.in_([c.id for c in rows]),
                                                Project.status != "ARCHIVED")) if rows else []
    best: dict[int, Project] = {}
    for p in projects:
        if p.client_id not in best or p.id > best[p.client_id].id:
            best[p.client_id] = p
    items = []
    for c in rows:
        o = client_out(c)
        o["progress"] = best[c.id].progress if c.id in best else None
        items.append(o)
    return {"total": total, "items": items}


@router.post("/clients", status_code=201)
def create_client(body: ClientIn, request: Request, user: User = Depends(require_staff),
                  db: Session = Depends(get_db)):
    _check_assignee(db, user, body.assigned_user_id)
    email = body.email.lower()
    dup = db.scalar(select(Client.id).where(Client.organization_id == user.organization_id,
                                            func.lower(Client.email) == email, Client.status != "Archived"))
    if dup:
        raise HTTPException(409, "A client with this email already exists")
    data = body.model_dump()
    data["email"] = email
    data["assigned_user_id"] = body.assigned_user_id or user.id
    c = Client(organization_id=user.organization_id, **data)
    db.add(c)
    db.flush()
    audit(db, user, "CLIENT_CREATED", "client", c.id, f"{user.name} created client \"{c.name}\"", c.id,
          client_ip(request))
    db.commit()
    return client_out(c)


@router.get("/clients/{client_id}")
def get_client(client_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = get_client_for(db, user, client_id)
    out = client_out(c)
    projects = db.scalars(select(Project).where(Project.client_id == c.id).order_by(Project.id.desc()))
    out["projects"] = [project_out(p, user.role) for p in projects]
    if user.role != "client":
        cu = db.scalar(select(User).where(User.client_id == c.id, User.role == "client"))
        out["portal_user_status"] = cu.status if cu else None
    return out


@router.patch("/clients/{client_id}")
def update_client(client_id: int, body: ClientUpdate, request: Request, user: User = Depends(require_staff),
                  db: Session = Depends(get_db)):
    c = get_client_for(db, user, client_id)
    _check_assignee(db, user, body.assigned_user_id)
    before = client_out(c)
    for k, v in body.model_dump(exclude_unset=True).items():
        if k == "status" and v is None:
            continue
        setattr(c, k, v)
    audit(db, user, "CLIENT_UPDATED", "client", c.id, f"Updated client \"{c.name}\"", c.id, client_ip(request),
          meta={"previous": before, "new": client_out(c)})
    db.commit()
    return client_out(c)


@router.delete("/clients/{client_id}")
def archive_client(client_id: int, request: Request, admin: User = Depends(require_admin),
                   db: Session = Depends(get_db)):
    """Archives the client (keeps history) and disables the client's login."""
    c = get_client_for(db, admin, client_id)
    c.status = "Archived"
    for u in db.scalars(select(User).where(User.client_id == c.id)):
        u.status = "disabled"
    audit(db, admin, "CLIENT_ARCHIVED", "client", c.id, f"Archived client \"{c.name}\"", c.id, client_ip(request))
    db.commit()
    return {"message": "Client archived"}


@router.post("/clients/{client_id}/invite")
def invite_client(client_id: int, request: Request, user: User = Depends(require_staff),
                  db: Session = Depends(get_db)):
    c = get_client_for(db, user, client_id)
    if c.status == "Archived":
        raise HTTPException(409, "Client is archived")
    portal = db.scalar(select(User).where(User.client_id == c.id, User.role == "client"))
    if portal is None:
        if db.scalar(select(User.id).where(User.email == c.email.lower())):
            raise HTTPException(409, "This email already belongs to another account")
        portal = User(organization_id=c.organization_id, client_id=c.id, name=c.name, email=c.email.lower(),
                      password_hash=hash_password("x" + new_invite_token()[0]), role="client", status="invited")
        db.add(portal)
        db.flush()
    elif portal.status == "active":
        raise HTTPException(409, "The client has already accepted the invitation")
    else:
        portal.status = "invited"
    for old in db.scalars(select(Invitation).where(Invitation.user_id == portal.id, Invitation.used.is_(False))):
        old.used = True
    raw, hashed = new_invite_token()
    inv = Invitation(user_id=portal.id, token_hash=hashed, expires_at=utcnow() + timedelta(days=settings.INVITE_DAYS))
    db.add(inv)
    url = f"{settings.APP_URL}/#/accept-invite?token={raw}"
    send_email(c.email, "You have been invited to complete onboarding",
               f"Hello {c.name},\n\nYou have been invited to complete your onboarding.\nOpen: {url}\n")
    audit(db, user, "CLIENT_INVITED", "client", c.id, f"Invitation sent to {c.email}", c.id, client_ip(request))
    db.commit()
    return {"invite_url": url, "token": raw, "expires_at": inv.expires_at.isoformat()}


@router.get("/clients/{client_id}/timeline")
def client_timeline(client_id: int, limit: int = 100, user: User = Depends(require_staff),
                    db: Session = Depends(get_db)):
    c = get_client_for(db, user, client_id)
    rows = list(db.scalars(select(AuditLog).where(AuditLog.organization_id == user.organization_id,
                                                  AuditLog.client_id == c.id)
                           .order_by(AuditLog.id.desc()).limit(min(max(limit, 1), 500))))
    names = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_({r.user_id for r in rows if r.user_id})))}
    return [audit_out(r, names) for r in rows]


# ------------------------------------------------------------------ templates

def _fill_tasks(t: Template, tasks) -> None:
    t.tasks.clear()
    for i, x in enumerate(tasks):
        t.tasks.append(TemplateTask(position=i, **x.model_dump()))


def _get_template(db: Session, user: User, tid: int) -> Template:
    t = db.get(Template, tid)
    if not t or t.organization_id != user.organization_id:
        raise HTTPException(404, "Template not found")
    return t


@router.get("/templates")
def list_templates(user: User = Depends(require_staff), db: Session = Depends(get_db)):
    rows = db.scalars(select(Template).where(Template.organization_id == user.organization_id).order_by(Template.name))
    return [template_out(t, summary=True) for t in rows]


@router.post("/templates", status_code=201)
def create_template(body: TemplateIn, request: Request, admin: User = Depends(require_admin),
                    db: Session = Depends(get_db)):
    t = Template(organization_id=admin.organization_id, name=body.name.strip(), description=body.description,
                 created_by=admin.id)
    _fill_tasks(t, body.tasks)
    db.add(t)
    db.flush()
    audit(db, admin, "TEMPLATE_CREATED", "template", t.id, f"Created template \"{t.name}\"", ip=client_ip(request))
    db.commit()
    return template_out(t)


@router.get("/templates/{tid}")
def get_template(tid: int, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    return template_out(_get_template(db, user, tid))


@router.patch("/templates/{tid}")
def update_template(tid: int, body: TemplateUpdate, request: Request, admin: User = Depends(require_admin),
                    db: Session = Depends(get_db)):
    t = _get_template(db, admin, tid)
    if body.name is not None:
        t.name = body.name.strip()
    if body.description is not None:
        t.description = body.description
    if body.tasks is not None:
        _fill_tasks(t, body.tasks)
    audit(db, admin, "TEMPLATE_UPDATED", "template", t.id, f"Updated template \"{t.name}\"", ip=client_ip(request))
    db.commit()
    return template_out(t)


@router.delete("/templates/{tid}")
def delete_template(tid: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    t = _get_template(db, admin, tid)
    for p in db.scalars(select(Project).where(Project.template_id == t.id)):
        p.template_id = None
    audit(db, admin, "TEMPLATE_DELETED", "template", t.id, f"Deleted template \"{t.name}\"", ip=client_ip(request))
    db.delete(t)
    db.commit()
    return {"message": "Template deleted"}
