from collections import Counter, defaultdict
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_admin, require_platform_admin, require_staff
from ..models import (Approval, AuditLog, Client, Document, Notification, Organization, Project, Task,
                      Template, User)
from ..serializers import (approval_out, audit_out, client_out, document_out, iso, notification_out,
                           project_out, task_out)
from ..services import REVIEWABLE, run_reminders, today

router = APIRouter(prefix="/api", tags=["misc"])


# ------------------------------------------------------------------ notifications

@router.get("/notifications")
def list_notifications(unread: bool = False, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread:
        stmt = stmt.where(Notification.read.is_(False))
    rows = db.scalars(stmt.order_by(Notification.id.desc()).limit(100))
    unread_count = db.scalar(select(func.count()).select_from(Notification).where(
        Notification.user_id == user.id, Notification.read.is_(False)))
    return {"unread": unread_count, "items": [notification_out(n) for n in rows]}


@router.post("/notifications/read-all")
def read_all(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    for n in db.scalars(select(Notification).where(Notification.user_id == user.id, Notification.read.is_(False))):
        n.read = True
    db.commit()
    return {"message": "All notifications marked as read"}


@router.post("/notifications/{nid}/read")
def read_one(nid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    n = db.get(Notification, nid)
    if not n or n.user_id != user.id:
        raise HTTPException(404, "Notification not found")
    n.read = True
    db.commit()
    return notification_out(n)


# ------------------------------------------------------------------ dashboard

@router.get("/dashboard")
def dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    org = user.organization_id
    if user.role == "client":
        projects = db.scalars(select(Project).where(Project.client_id == user.client_id,
                                                    Project.status != "ARCHIVED").order_by(Project.id.desc()))
        client = db.get(Client, user.client_id)
        return {"client": {"id": client.id, "name": client.name, "company_name": client.company_name},
                "projects": [project_out(p, "client", with_tasks=True) for p in projects]}

    clients = list(db.scalars(select(Client).where(Client.organization_id == org, Client.status != "Archived")))
    projects = list(db.scalars(select(Project).where(Project.organization_id == org, Project.status != "ARCHIVED")))
    active = [p for p in projects if p.status == "ACTIVE"]
    tasks = list(db.scalars(select(Task).where(Task.organization_id == org,
                                               Task.project_id.in_([p.id for p in active])))) if active else []
    t = today()
    pending_review = [x for x in tasks if x.audience == "client" and x.type != "APPROVAL" and x.status in REVIEWABLE]
    open_tasks = [x for x in tasks if x.status != "APPROVED"]
    overdue = [x for x in open_tasks if x.due_date and x.due_date < t]
    upcoming = sorted([x for x in open_tasks if x.due_date and t <= x.due_date <= t + timedelta(days=7)],
                      key=lambda x: x.due_date)
    pend_appr = list(db.execute(select(Approval, Task).join(Task, Task.id == Approval.task_id)
                                .where(Approval.organization_id == org, Approval.status == "PENDING")
                                .order_by(Approval.id.desc()).limit(10)).all())
    uploads = list(db.execute(select(Document, Task).join(Task, Task.id == Document.task_id)
                              .where(Document.organization_id == org).order_by(Document.id.desc()).limit(8)).all())
    activity = list(db.scalars(select(AuditLog).where(AuditLog.organization_id == org)
                               .order_by(AuditLog.id.desc()).limit(15)))
    names = {u.id: u.name for u in db.scalars(select(User).where(User.organization_id == org))}
    total_p = len(projects)
    done_p = sum(1 for p in projects if p.status == "COMPLETED")
    return {
        "stats": {
            "total_clients": len(clients), "active_onboarding": len(active),
            "awaiting_clients": sum(1 for c in clients if c.status == "Waiting for Client"),
            "pending_reviews": len(pending_review), "completed": done_p,
            "overdue_tasks": len(overdue),
            "completion_rate": round(done_p / total_p * 100) if total_p else 0,
        },
        "recent_clients": [client_out(c) for c in sorted(clients, key=lambda c: -c.id)[:5]],
        "recent_onboarding": [{**project_out(p, "staff"), "client_name": next((c.name for c in clients if c.id == p.client_id), "")}
                              for p in sorted(projects, key=lambda p: -p.id)[:6]],
        "pending_reviews": [task_out(x) for x in pending_review[:10]],
        "overdue_tasks": [task_out(x) for x in overdue[:10]],
        "upcoming_deadlines": [task_out(x) for x in upcoming[:10]],
        "pending_approvals": [approval_out(a, tk) for a, tk in pend_appr],
        "recent_uploads": [document_out(d, tk) for d, tk in uploads],
        "recent_activity": [audit_out(a, names) for a in activity],
    }


@router.get("/analytics")
def analytics(user: User = Depends(require_staff), db: Session = Depends(get_db)):
    org = user.organization_id
    projects = list(db.scalars(select(Project).where(Project.organization_id == org, Project.status != "ARCHIVED")))
    done = [p for p in projects if p.status == "COMPLETED" and p.completed_at]
    avg_days = (sum((p.completed_at - p.created_at).total_seconds() for p in done) / len(done) / 86400) if done else None
    tasks = list(db.scalars(select(Task).where(Task.organization_id == org)))
    decided = list(db.scalars(select(Approval).where(Approval.organization_id == org, Approval.reviewed_at.is_not(None))))
    avg_appr = (sum((a.reviewed_at - a.created_at).total_seconds() for a in decided) / len(decided) / 3600) if decided else None
    by_task = {t.id: t for t in tasks}
    changes = Counter(by_task[a.task_id].title for a in decided if a.status == "CHANGES_REQUESTED" and a.task_id in by_task)
    delayed = Counter(t.title for t in tasks if t.due_date and t.due_date < today() and t.status != "APPROVED")
    templates = {t.id: t.name for t in db.scalars(select(Template).where(Template.organization_id == org))}
    per_tpl: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for p in projects:
        key = templates.get(p.template_id, "No template")
        per_tpl[key][0] += 1
        per_tpl[key][1] += 1 if p.status == "COMPLETED" else 0
    return {
        "average_onboarding_days": round(avg_days, 2) if avg_days is not None else None,
        "completion_rate": round(len(done) / len(projects) * 100) if projects else 0,
        "task_completion_rate": round(sum(1 for t in tasks if t.status == "APPROVED") / len(tasks) * 100) if tasks else 0,
        "average_approval_hours": round(avg_appr, 2) if avg_appr is not None else None,
        "changes_requested_total": sum(changes.values()),
        "most_delayed_tasks": [{"title": k, "count": v} for k, v in delayed.most_common(5)],
        "bottlenecks": [{"title": k, "changes_requested": v} for k, v in changes.most_common(5)],
        "by_template": [{"template": k, "total": v[0], "completed": v[1]} for k, v in per_tpl.items()],
    }


@router.get("/audit-logs")
def audit_logs(limit: int = 100, offset: int = 0, action: Optional[str] = None,
               admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    stmt = select(AuditLog).where(AuditLog.organization_id == admin.organization_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    rows = list(db.scalars(stmt.order_by(AuditLog.id.desc()).limit(min(max(limit, 1), 500)).offset(max(offset, 0))))
    names = {u.id: u.name for u in db.scalars(select(User).where(User.organization_id == admin.organization_id))}
    return [audit_out(r, names) for r in rows]


@router.post("/reminders/run")
def reminders(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return run_reminders(db, admin.organization_id)


# ------------------------------------------------------------------ platform admin (SaaS operator)

@router.get("/platform/organizations")
def platform_orgs(_: User = Depends(require_platform_admin), db: Session = Depends(get_db)):
    out = []
    for o in db.scalars(select(Organization).order_by(Organization.id)):
        out.append({"id": o.id, "name": o.name, "slug": o.slug, "plan": o.plan, "created_at": iso(o.created_at),
                    "users": db.scalar(select(func.count()).select_from(User).where(User.organization_id == o.id)),
                    "clients": db.scalar(select(func.count()).select_from(Client).where(Client.organization_id == o.id))})
    return out


@router.get("/platform/stats")
def platform_stats(_: User = Depends(require_platform_admin), db: Session = Depends(get_db)):
    c = lambda m: db.scalar(select(func.count()).select_from(m))  # noqa: E731
    return {"organizations": c(Organization), "users": c(User), "clients": c(Client),
            "projects": c(Project), "documents": c(Document)}
