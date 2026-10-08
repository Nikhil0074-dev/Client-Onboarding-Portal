from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (Approval, AuditLog, Client, Comment, Document, Notification, Project, Task,
                     TaskSubmission, Template, User)
from .services import stats_for, today


def iso(v):
    return v.isoformat() if v else None


def user_out(u: User) -> dict:
    return {"id": u.id, "name": u.name, "email": u.email, "role": u.role, "status": u.status,
            "organization_id": u.organization_id, "client_id": u.client_id,
            "email_verified": u.email_verified, "created_at": iso(u.created_at)}


def client_out(c: Client) -> dict:
    return {"id": c.id, "name": c.name, "company_name": c.company_name, "email": c.email, "phone": c.phone,
            "industry": c.industry, "website": c.website, "address": c.address, "status": c.status,
            "assigned_user_id": c.assigned_user_id, "created_at": iso(c.created_at)}


def template_out(t: Template, summary: bool = False) -> dict:
    out = {"id": t.id, "name": t.name, "description": t.description, "created_at": iso(t.created_at),
           "task_count": len(t.tasks)}
    if not summary:
        out["tasks"] = [{"id": x.id, "position": x.position, "title": x.title, "description": x.description,
                         "type": x.type, "audience": x.audience, "priority": x.priority,
                         "is_required": x.is_required, "due_in_days": x.due_in_days, "config": x.config}
                        for x in t.tasks]
    return out


def is_overdue(t: Task) -> bool:
    return bool(t.due_date and t.due_date < today() and t.status != "APPROVED")


def task_out(t: Task) -> dict:
    return {"id": t.id, "project_id": t.project_id, "position": t.position, "title": t.title,
            "description": t.description, "type": t.type, "audience": t.audience, "status": t.status,
            "priority": t.priority, "due_date": iso(t.due_date), "is_required": t.is_required,
            "assigned_to": t.assigned_to, "config": t.config or {}, "response": t.response,
            "overdue": is_overdue(t)}


def project_out(p: Project, role: str, with_tasks: bool = False) -> dict:
    tasks = [t for t in p.tasks if role != "client" or t.audience == "client"]
    st = stats_for(tasks)
    out = {"id": p.id, "client_id": p.client_id, "template_id": p.template_id, "name": p.name,
           "status": p.status, "start_date": iso(p.start_date), "due_date": iso(p.due_date),
           "completed_at": iso(p.completed_at), "created_at": iso(p.created_at), **st}
    if with_tasks:
        out["tasks"] = [task_out(t) for t in tasks]
    return out


def document_out(d: Document, task: Task | None = None) -> dict:
    return {"id": d.id, "client_id": d.client_id, "task_id": d.task_id, "file_name": d.file_name,
            "file_type": d.file_type, "file_size": d.file_size, "version": d.version,
            "uploaded_by": d.uploaded_by, "created_at": iso(d.created_at),
            "approval_status": task.status if task else None}


def approval_out(a: Approval, task: Task | None = None) -> dict:
    return {"id": a.id, "task_id": a.task_id, "task_title": task.title if task else None,
            "requested_by": a.requested_by, "reviewed_by": a.reviewed_by, "status": a.status,
            "comment": a.comment, "deadline": iso(a.deadline), "created_at": iso(a.created_at),
            "reviewed_at": iso(a.reviewed_at)}


def comments_out(db: Session, task: Task, role: str) -> list[dict]:
    q = select(Comment, User.name, User.role).join(User, User.id == Comment.user_id).where(Comment.task_id == task.id)
    if role == "client":
        q = q.where(Comment.is_internal.is_(False))
    rows = db.execute(q.order_by(Comment.id)).all()
    return [{"id": c.id, "body": c.body, "is_internal": c.is_internal, "user_id": c.user_id,
             "user_name": name, "user_role": r, "created_at": iso(c.created_at)} for c, name, r in rows]


def task_detail(db: Session, task: Task, role: str) -> dict:
    docs = db.scalars(select(Document).where(Document.task_id == task.id).order_by(Document.file_name, Document.version))
    appr = db.scalars(select(Approval).where(Approval.task_id == task.id).order_by(Approval.id))
    subs = db.scalars(select(TaskSubmission).where(TaskSubmission.task_id == task.id).order_by(TaskSubmission.id))
    out = task_out(task)
    out["documents"] = [document_out(d, task) for d in docs]
    out["approvals"] = [approval_out(a, task) for a in appr]
    out["submissions"] = [{"id": s.id, "content": s.content, "status": s.status, "submitted_at": iso(s.submitted_at),
                           "reviewed_at": iso(s.reviewed_at)} for s in subs]
    out["comments"] = comments_out(db, task, role)
    return out


def notification_out(n: Notification) -> dict:
    return {"id": n.id, "type": n.type, "title": n.title, "message": n.message, "read": n.read,
            "created_at": iso(n.created_at)}


def audit_out(a: AuditLog, names: dict) -> dict:
    return {"id": a.id, "user_id": a.user_id, "user_name": names.get(a.user_id, "System"), "action": a.action,
            "resource_type": a.resource_type, "resource_id": a.resource_id, "summary": a.summary,
            "client_id": a.client_id, "ip_address": a.ip_address, "meta": a.meta, "created_at": iso(a.created_at)}
