from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import (client_ip, get_client_for, get_current_user, get_project_for, get_task_for,
                    require_staff)
from ..models import Approval, Comment, Project, Task, Template, User
from ..schemas import (ApprovalRequestIn, CommentIn, DecisionIn, ProjectIn, ProjectUpdate, SubmitIn,
                       TaskIn, TaskUpdate)
from ..serializers import approval_out, comments_out, project_out, task_detail, task_out
from ..services import (audit, client_user, create_project, do_decision, do_submit, mark_under_review,
                        notify, recompute, staff_recipients, today)

router = APIRouter(prefix="/api", tags=["onboarding"])


# ------------------------------------------------------------------ projects

@router.get("/onboarding")
def list_projects(client_id: Optional[int] = None, status: Optional[str] = None,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stmt = select(Project).where(Project.organization_id == user.organization_id)
    if user.role == "client":
        stmt = stmt.where(Project.client_id == user.client_id)
    elif client_id:
        stmt = stmt.where(Project.client_id == client_id)
    if status:
        stmt = stmt.where(Project.status == status)
    return [project_out(p, user.role) for p in db.scalars(stmt.order_by(Project.id.desc()))]


@router.post("/onboarding", status_code=201)
def start_onboarding(body: ProjectIn, request: Request, user: User = Depends(require_staff),
                     db: Session = Depends(get_db)):
    client = get_client_for(db, user, body.client_id)
    if client.status == "Archived":
        raise HTTPException(409, "Client is archived")
    template = None
    if body.template_id is not None:
        template = db.get(Template, body.template_id)
        if not template or template.organization_id != user.organization_id:
            raise HTTPException(404, "Template not found")
    p = create_project(db, user, client, template, body.name, body.due_date, client_ip(request))
    db.commit()
    return project_out(p, user.role, with_tasks=True)


@router.get("/onboarding/{pid}")
def get_project(pid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return project_out(get_project_for(db, user, pid), user.role, with_tasks=True)


@router.patch("/onboarding/{pid}")
def update_project(pid: int, body: ProjectUpdate, request: Request, user: User = Depends(require_staff),
                   db: Session = Depends(get_db)):
    p = get_project_for(db, user, pid)
    data = body.model_dump(exclude_unset=True)
    if data.get("status") == "ACTIVE" and p.status == "COMPLETED":
        data.pop("status")
    for k, v in data.items():
        if k == "name" and v is None:
            continue
        setattr(p, k, v)
    audit(db, user, "ONBOARDING_UPDATED", "project", p.id, f"Updated '{p.name}'", p.client_id, client_ip(request))
    recompute(db, p)
    db.commit()
    return project_out(p, user.role, with_tasks=True)


@router.get("/onboarding/{pid}/tasks")
def project_tasks(pid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return project_out(get_project_for(db, user, pid), user.role, with_tasks=True)["tasks"]


@router.post("/onboarding/{pid}/tasks", status_code=201)
def add_task(pid: int, body: TaskIn, request: Request, user: User = Depends(require_staff),
             db: Session = Depends(get_db)):
    p = get_project_for(db, user, pid)
    pos = (db.scalar(select(func.max(Task.position)).where(Task.project_id == p.id)) or 0) + 1
    t = Task(project_id=p.id, organization_id=p.organization_id, position=pos, **body.model_dump())
    db.add(t)
    db.flush()
    audit(db, user, "TASK_CREATED", "task", t.id, f"Added task '{t.title}'", p.client_id, client_ip(request))
    if t.audience == "client":
        notify(db, getattr(client_user(db, p.client_id), "id", None), "new_task",
               "New onboarding task", f"A new onboarding task has been assigned to you: {t.title}", email=True)
    recompute(db, p)
    db.commit()
    return task_out(t)


# ------------------------------------------------------------------ tasks

@router.get("/tasks")
def search_tasks(q: Optional[str] = None, status: Optional[str] = None, priority: Optional[str] = None,
                 assigned_to: Optional[int] = None, project_id: Optional[int] = None,
                 overdue: Optional[bool] = None, limit: int = 100, offset: int = 0,
                 user: User = Depends(require_staff), db: Session = Depends(get_db)):
    stmt = select(Task).join(Project, Project.id == Task.project_id).where(
        Task.organization_id == user.organization_id)
    if q:
        stmt = stmt.where(func.lower(Task.title).like(f"%{q.strip().lower()}%"))
    if status:
        stmt = stmt.where(Task.status == status)
    if priority:
        stmt = stmt.where(Task.priority == priority)
    if assigned_to:
        stmt = stmt.where(Task.assigned_to == assigned_to)
    if project_id:
        stmt = stmt.where(Task.project_id == project_id)
    if overdue:
        stmt = stmt.where(Task.due_date < today(), Task.status != "APPROVED")
    rows = db.scalars(stmt.order_by(Task.id.desc()).limit(min(max(limit, 1), 200)).offset(max(offset, 0)))
    return [task_out(t) for t in rows]


@router.get("/tasks/{tid}")
def get_task(tid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return task_detail(db, get_task_for(db, user, tid), user.role)


@router.patch("/tasks/{tid}")
def update_task(tid: int, body: TaskUpdate, request: Request, user: User = Depends(require_staff),
                db: Session = Depends(get_db)):
    t = get_task_for(db, user, tid)
    data = body.model_dump(exclude_unset=True)
    if data.get("assigned_to") is not None:
        a = db.get(User, data["assigned_to"])
        if not a or a.organization_id != user.organization_id or a.role == "client":
            raise HTTPException(422, "Assignee must be a member of your team")
    before = {k: str(getattr(t, k)) for k in data}
    for k, v in data.items():
        if k in ("title", "priority", "is_required") and v is None:
            continue
        setattr(t, k, v)
    p = db.get(Project, t.project_id)
    audit(db, user, "TASK_UPDATED", "task", t.id, f"Updated task '{t.title}'", p.client_id, client_ip(request),
          meta={"previous": before, "new": {k: str(getattr(t, k)) for k in data}})
    recompute(db, p)
    db.commit()
    return task_out(t)


@router.post("/tasks/{tid}/start")
def start_task(tid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    t = get_task_for(db, user, tid)
    if t.status == "NOT_STARTED":
        t.status = "IN_PROGRESS"
        recompute(db, db.get(Project, t.project_id))
        db.commit()
    return task_out(t)


@router.post("/tasks/{tid}/submit")
def submit_task(tid: int, body: SubmitIn, request: Request, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    t = get_task_for(db, user, tid)
    do_submit(db, user, t, body.content, body.comment, body.deadline, client_ip(request))
    db.commit()
    return task_out(t)


@router.post("/tasks/{tid}/review")
def review_task(tid: int, request: Request, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    t = get_task_for(db, user, tid)
    mark_under_review(db, user, t, client_ip(request))
    db.commit()
    return task_out(t)


@router.post("/tasks/{tid}/request-changes")
def request_changes(tid: int, body: DecisionIn, request: Request, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    t = get_task_for(db, user, tid)
    do_decision(db, user, t, "CHANGES_REQUESTED", body.comment, client_ip(request))
    db.commit()
    return task_out(t)


@router.post("/tasks/{tid}/approve")
def approve_task(tid: int, body: DecisionIn, request: Request, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    t = get_task_for(db, user, tid)
    do_decision(db, user, t, "APPROVED", body.comment, client_ip(request))
    db.commit()
    return task_out(t)


# ------------------------------------------------------------------ comments

@router.get("/tasks/{tid}/comments")
def list_comments(tid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return comments_out(db, get_task_for(db, user, tid), user.role)


@router.post("/tasks/{tid}/comments", status_code=201)
def add_comment(tid: int, body: CommentIn, request: Request, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    t = get_task_for(db, user, tid)
    if body.is_internal and user.role == "client":
        raise HTTPException(403, "Clients cannot write internal notes")
    c = Comment(task_id=t.id, user_id=user.id, body=body.body.strip(), is_internal=body.is_internal)
    db.add(c)
    p = db.get(Project, t.project_id)
    if not body.is_internal:
        if user.role == "client":
            for uid in staff_recipients(db, get_client_for(db, user, p.client_id)):
                notify(db, uid, "comment", f"New comment on {t.title}", body.body[:200])
        else:
            notify(db, getattr(client_user(db, p.client_id), "id", None), "comment",
                   f"New comment on {t.title}", body.body[:200])
    audit(db, user, "COMMENT_INTERNAL" if body.is_internal else "COMMENT_ADDED", "task", t.id,
          f"{user.name} commented on '{t.title}'", p.client_id, client_ip(request))
    db.commit()
    return {"id": c.id, "body": c.body, "is_internal": c.is_internal, "user_id": user.id,
            "user_name": user.name, "user_role": user.role, "created_at": c.created_at.isoformat()}


# ------------------------------------------------------------------ approvals

@router.get("/approvals")
def list_approvals(status: Optional[str] = None, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    stmt = (select(Approval, Task).join(Task, Task.id == Approval.task_id)
            .join(Project, Project.id == Task.project_id)
            .where(Approval.organization_id == user.organization_id))
    if user.role == "client":
        stmt = stmt.where(Project.client_id == user.client_id, Task.audience == "client")
    if status:
        stmt = stmt.where(Approval.status == status)
    rows = db.execute(stmt.order_by(Approval.id.desc()).limit(300)).all()
    return [approval_out(a, t) for a, t in rows]


@router.post("/approvals", status_code=201)
def request_approval(body: ApprovalRequestIn, request: Request, user: User = Depends(require_staff),
                     db: Session = Depends(get_db)):
    t = get_task_for(db, user, body.task_id)
    if t.type != "APPROVAL":
        raise HTTPException(422, "Only tasks of type APPROVAL can be sent for client approval")
    do_submit(db, user, t, {}, body.comment, body.deadline, client_ip(request))
    db.flush()
    a = db.scalar(select(Approval).where(Approval.task_id == t.id).order_by(Approval.id.desc()))
    db.commit()
    return approval_out(a, t)


def _decide(aid: int, decision: str, body: DecisionIn, request: Request, user: User, db: Session):
    a = db.get(Approval, aid)
    if not a or a.organization_id != user.organization_id:
        raise HTTPException(404, "Approval not found")
    t = get_task_for(db, user, a.task_id)
    if a.status != "PENDING":
        raise HTTPException(409, "This approval was already decided")
    do_decision(db, user, t, decision, body.comment, client_ip(request))
    db.commit()
    db.refresh(a)
    return approval_out(a, t)


@router.post("/approvals/{aid}/approve")
def approve(aid: int, body: DecisionIn, request: Request, user: User = Depends(get_current_user),
            db: Session = Depends(get_db)):
    return _decide(aid, "APPROVED", body, request, user, db)


@router.post("/approvals/{aid}/request-changes")
def approval_changes(aid: int, body: DecisionIn, request: Request, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    return _decide(aid, "CHANGES_REQUESTED", body, request, user, db)
