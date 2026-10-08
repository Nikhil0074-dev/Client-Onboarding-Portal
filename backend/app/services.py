"""Business logic: audit trail, notifications, email, templates, workflow, progress, reminders."""
import logging
import re
import smtplib
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import (
    Approval, AuditLog, Client, Comment, Document, Notification, Organization, Project,
    Task, TaskSubmission, Template, TemplateTask, User, utcnow,
)

log = logging.getLogger("onboarding")
STAFF = ("org_admin", "team_member")
REVIEWABLE = ("SUBMITTED", "RESUBMITTED", "UNDER_REVIEW")
WAITING_ON_CLIENT = ("NOT_STARTED", "IN_PROGRESS", "CHANGES_REQUESTED")


def today() -> date:
    return utcnow().date()


# ---------------------------------------------------------------------------- email / notify / audit

def send_email(to: str, subject: str, body: str) -> None:
    """Send an email. Never raises: a broken mail server must not break the API."""
    log.info("EMAIL to=%s subject=%s", to, subject)
    if not settings.SMTP_HOST:
        return
    try:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = settings.EMAIL_FROM, to, subject
        msg.set_content(body)
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as s:
            s.starttls()
            if settings.SMTP_USER:
                s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            s.send_message(msg)
    except Exception:  # noqa: BLE001
        log.exception("Could not send email to %s", to)


def notify(db: Session, user_id: Optional[int], type_: str, title: str, message: str = "", email: bool = False) -> None:
    if not user_id:
        return
    db.add(Notification(user_id=user_id, type=type_, title=title, message=message))
    if email:
        user = db.get(User, user_id)
        if user:
            send_email(user.email, title, message or title)


def audit(db: Session, user: Optional[User], action: str, rtype: str, rid: Optional[int] = None,
          summary: str = "", client_id: Optional[int] = None, ip: str = "", meta: Optional[dict] = None,
          org_id: Optional[int] = None) -> None:
    db.add(AuditLog(
        organization_id=org_id if org_id is not None else (user.organization_id if user else None),
        client_id=client_id, user_id=user.id if user else None, action=action, resource_type=rtype,
        resource_id=rid, summary=summary, ip_address=ip, meta=meta or {},
    ))


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "org"


def unique_slug(db: Session, name: str) -> str:
    base, slug, n = slugify(name), slugify(name), 1
    while db.scalar(select(Organization.id).where(Organization.slug == slug)):
        n += 1
        slug = f"{base}-{n}"
    return slug


def client_user(db: Session, client_id: int) -> Optional[User]:
    return db.scalar(select(User).where(User.client_id == client_id, User.role == "client",
                                        User.status.in_(("active", "invited"))))


def staff_recipients(db: Session, client: Client) -> list[int]:
    if client.assigned_user_id:
        u = db.get(User, client.assigned_user_id)
        if u and u.status == "active":
            return [u.id]
    return list(db.scalars(select(User.id).where(User.organization_id == client.organization_id,
                                                  User.role == "org_admin", User.status == "active")))


# ---------------------------------------------------------------------------- default templates

_COMPANY_FORM = {"fields": [
    {"name": "company_name", "label": "Company Name", "type": "text", "required": True},
    {"name": "website", "label": "Website", "type": "url", "required": False},
    {"name": "industry", "label": "Industry", "type": "text", "required": False},
    {"name": "target_audience", "label": "Target Audience", "type": "text", "required": False},
    {"name": "description", "label": "Business Description", "type": "textarea", "required": True},
    {"name": "primary_contact", "label": "Primary Contact", "type": "text", "required": True},
]}
_REQ_FORM = {"fields": [
    {"name": "goals", "label": "What do you want to achieve?", "type": "textarea", "required": True},
    {"name": "deadline", "label": "Ideal launch date", "type": "date", "required": False},
    {"name": "has_website", "label": "Do you already have a website?", "type": "radio",
     "options": ["Yes", "No"], "required": True},
]}

# (title, type, description, config)
DEFAULT_TEMPLATES = [
    ("Web Development", "Everything needed to start a website project.", [
        ("Company Information", "FORM", "Tell us about your company.", _COMPANY_FORM),
        ("Project Requirements", "QUESTIONNAIRE", "Describe what you need.", _REQ_FORM),
        ("Brand Guidelines", "FILE_UPLOAD", "Upload your brand guidelines (PDF).", {}),
        ("Logo Upload", "FILE_UPLOAD", "Upload your logo (SVG or PNG).", {}),
        ("Website Content", "INFORMATION", "Paste or describe your page content.", {}),
        ("Domain Information", "INFORMATION", "Where is your domain registered?", {}),
        ("Hosting Information", "INFORMATION", "Where is your site hosted?", {}),
        ("Design Approval", "APPROVAL", "Please review and approve the homepage design.", {}),
        ("Final Confirmation", "CLIENT_CONFIRMATION", "Confirm that everything is correct.", {}),
    ]),
    ("Digital Marketing", "Collect access and brand details for campaigns.", [
        ("Business Information", "FORM", "Tell us about your business.", _COMPANY_FORM),
        ("Target Audience", "INFORMATION", "Who are you trying to reach?", {}),
        ("Marketing Goals", "QUESTIONNAIRE", "What results do you want?", _REQ_FORM),
        ("Social Media Access", "INFORMATION", "Share your social profile links.", {}),
        ("Brand Assets", "FILE_UPLOAD", "Upload logos and images.", {}),
        ("Competitor Information", "INFORMATION", "List your main competitors.", {}),
        ("Google Analytics Access", "CLIENT_CONFIRMATION", "Confirm you added us as a viewer.", {}),
        ("Search Console Access", "CLIENT_CONFIRMATION", "Confirm you added us as a user.", {}),
        ("Campaign Approval", "APPROVAL", "Approve the campaign plan.", {}),
    ]),
    ("Freelancer Design", "Light onboarding for a design project.", [
        ("Project Brief", "QUESTIONNAIRE", "Describe the project.", _REQ_FORM),
        ("Reference Images", "FILE_UPLOAD", "Upload designs you like.", {}),
        ("Brand Colors", "INFORMATION", "List your brand colors.", {}),
        ("Logo", "FILE_UPLOAD", "Upload your logo.", {}),
        ("Content", "INFORMATION", "Share the text for the design.", {}),
        ("Design Preferences", "INFORMATION", "Style, mood, things to avoid.", {}),
        ("First Draft Approval", "APPROVAL", "Review the first draft.", {}),
        ("Final Approval", "APPROVAL", "Approve the final design.", {}),
    ]),
]


def seed_templates(db: Session, org: Organization, user: User) -> None:
    for name, desc, tasks in DEFAULT_TEMPLATES:
        t = Template(organization_id=org.id, name=name, description=desc, created_by=user.id)
        for i, (title, type_, d, cfg) in enumerate(tasks):
            t.tasks.append(TemplateTask(position=i, title=title, description=d, type=type_, config=cfg))
        t.tasks.append(TemplateTask(position=len(tasks), title="Internal: review all client submissions",
                                    type="INTERNAL_TASK", audience="agency", is_required=True))
        db.add(t)


# ---------------------------------------------------------------------------- progress

def stats_for(tasks: list[Task]) -> dict:
    required = [t for t in tasks if t.is_required]
    base = required if required else []
    done = sum(1 for t in base if t.status == "APPROVED")
    progress = round(done / len(base) * 100) if base else 0
    return {
        "progress": progress,
        "total": len(tasks),
        "required_total": len(base),
        "completed": sum(1 for t in tasks if t.status == "APPROVED"),
        "changes_requested": sum(1 for t in tasks if t.status == "CHANGES_REQUESTED"),
        "pending": sum(1 for t in tasks if t.status != "APPROVED"),
    }


def refresh_client_status(db: Session, client: Client) -> None:
    if client.status == "Archived":
        return
    projects = list(db.scalars(select(Project).where(Project.client_id == client.id,
                                                      Project.status.in_(("ACTIVE", "COMPLETED")))))
    if not projects:
        return
    active = [p.id for p in projects if p.status == "ACTIVE"]
    if not active:
        client.status = "Completed"
        return
    tasks = list(db.scalars(select(Task).where(Task.project_id.in_(active), Task.audience == "client")))
    if any(t.status in REVIEWABLE and t.type != "APPROVAL" for t in tasks):
        client.status = "Under Review"
    elif any(t.status == "CHANGES_REQUESTED" for t in tasks):
        client.status = "Changes Requested"
    elif all(t.status == "NOT_STARTED" for t in tasks):
        client.status = "Onboarding"
    else:
        client.status = "Waiting for Client"


def recompute(db: Session, project: Project) -> None:
    db.flush()
    tasks = list(db.scalars(select(Task).where(Task.project_id == project.id)))
    st = stats_for(tasks)
    project.progress = st["progress"]
    if project.status != "ARCHIVED":
        if st["required_total"] > 0 and st["progress"] == 100:
            if project.status != "COMPLETED":
                project.status, project.completed_at = "COMPLETED", utcnow()
        elif project.status == "COMPLETED":
            project.status, project.completed_at = "ACTIVE", None
    db.flush()
    client = db.get(Client, project.client_id)
    if client:
        refresh_client_status(db, client)


# ---------------------------------------------------------------------------- workflow

def validate_submission(db: Session, task: Task, content: dict) -> None:
    fields = (task.config or {}).get("fields") or []
    if task.type in ("FORM", "QUESTIONNAIRE") and fields:
        missing = [f.get("label") or f["name"] for f in fields
                   if f.get("required") and content.get(f["name"]) in (None, "", [], False)]
        if missing:
            raise HTTPException(422, f"Required fields missing: {', '.join(missing)}")
    elif task.type in ("FORM", "QUESTIONNAIRE", "INFORMATION"):
        if not any(str(v).strip() for v in content.values() if v not in (None, False)):
            raise HTTPException(422, "Please provide an answer before submitting")
    elif task.type == "CLIENT_CONFIRMATION":
        if content.get("confirmed") is not True:
            raise HTTPException(422, "You must confirm to continue")
    elif task.type == "SIGNATURE":
        if not str(content.get("signature_name", "")).strip():
            raise HTTPException(422, "Please type your full name as your signature")
    elif task.type == "FILE_UPLOAD":
        has_file = db.scalar(select(Document.id).where(Document.task_id == task.id).limit(1))
        if not has_file:
            raise HTTPException(422, "Upload at least one file before submitting")


def _pending(db: Session, task: Task) -> Optional[Approval]:
    return db.scalar(select(Approval).where(Approval.task_id == task.id, Approval.status == "PENDING")
                     .order_by(Approval.id.desc()))


def do_submit(db: Session, user: User, task: Task, content: dict, comment: str,
              deadline: Optional[date], ip: str) -> Task:
    project = db.get(Project, task.project_id)
    client = db.get(Client, project.client_id)
    if task.status == "APPROVED":
        raise HTTPException(409, "This task is already approved")
    resubmission = task.status == "CHANGES_REQUESTED"

    if task.type == "APPROVAL":
        # Staff ask the client for approval.
        if user.role not in STAFF:
            raise HTTPException(403, "Only agency staff can request an approval")
        if task.status not in WAITING_ON_CLIENT:
            raise HTTPException(409, "Approval has already been requested")
        db.add(Approval(organization_id=task.organization_id, task_id=task.id, requested_by=user.id,
                        comment=comment, deadline=deadline))
        if deadline:
            task.due_date = deadline
        task.status = "RESUBMITTED" if resubmission else "SUBMITTED"
        notify(db, getattr(client_user(db, client.id), "id", None), "approval_requested",
               f"Approval needed: {task.title}", f"Your approval is required for {task.title}.", email=True)
        audit(db, user, "APPROVAL_REQUESTED", "task", task.id, f"Requested approval for '{task.title}'",
              client.id, ip)
    else:
        if user.role != "client" or task.audience != "client":
            raise HTTPException(403, "Only the client can submit this task")
        if task.status not in WAITING_ON_CLIENT:
            raise HTTPException(409, "This task is already waiting for review")
        validate_submission(db, task, content)
        db.add(TaskSubmission(task_id=task.id, submitted_by=user.id, content=content))
        task.response = content
        task.status = "RESUBMITTED" if resubmission else "SUBMITTED"
        db.add(Approval(organization_id=task.organization_id, task_id=task.id, requested_by=user.id,
                        comment=comment))
        for uid in staff_recipients(db, client):
            notify(db, uid, "task_submitted", f"{client.name} submitted: {task.title}",
                   f"'{task.title}' is ready for your review.")
        audit(db, user, "TASK_SUBMITTED", "task", task.id, f"{client.name} submitted '{task.title}'",
              client.id, ip)
    if comment.strip():
        db.add(Comment(task_id=task.id, user_id=user.id, body=comment.strip()))
    recompute(db, project)
    return task


def mark_under_review(db: Session, user: User, task: Task, ip: str) -> Task:
    if task.type == "APPROVAL" or task.status not in ("SUBMITTED", "RESUBMITTED"):
        raise HTTPException(409, "Task is not waiting for agency review")
    project = db.get(Project, task.project_id)
    task.status = "UNDER_REVIEW"
    audit(db, user, "TASK_UNDER_REVIEW", "task", task.id, f"Started reviewing '{task.title}'", project.client_id, ip)
    recompute(db, project)
    return task


def do_decision(db: Session, user: User, task: Task, decision: str, comment: str, ip: str) -> Task:
    assert decision in ("APPROVED", "CHANGES_REQUESTED")
    project = db.get(Project, task.project_id)
    client = db.get(Client, project.client_id)
    if task.type == "APPROVAL":
        if user.role != "client":
            raise HTTPException(403, "Only the client can decide on this approval")
        allowed = task.status in ("SUBMITTED", "RESUBMITTED")
    else:
        if user.role not in STAFF:
            raise HTTPException(403, "Only agency staff can review this task")
        if task.audience == "agency":
            if decision != "APPROVED":
                raise HTTPException(400, "Internal tasks can only be marked complete")
            allowed = task.status != "APPROVED"
        else:
            allowed = task.status in REVIEWABLE
    if not allowed:
        raise HTTPException(409, f"Task cannot be decided in status {task.status}")
    if decision == "CHANGES_REQUESTED" and not comment.strip():
        raise HTTPException(422, "Please describe the changes you need")

    approval = _pending(db, task)
    if approval is None:
        approval = Approval(organization_id=task.organization_id, task_id=task.id, requested_by=user.id)
        db.add(approval)
    approval.status, approval.reviewed_by = decision, user.id
    approval.comment, approval.reviewed_at = comment.strip() or approval.comment, utcnow()
    sub = db.scalar(select(TaskSubmission).where(TaskSubmission.task_id == task.id)
                    .order_by(TaskSubmission.id.desc()))
    if sub:
        sub.status, sub.reviewed_at = decision, utcnow()
    prev = task.status
    task.status = decision
    if comment.strip():
        db.add(Comment(task_id=task.id, user_id=user.id, body=comment.strip()))

    verb = "approved" if decision == "APPROVED" else "requested changes to"
    audit(db, user, "TASK_" + decision, "task", task.id, f"{user.name} {verb} '{task.title}'", client.id, ip,
          meta={"previous": prev, "new": decision})
    if task.type == "APPROVAL":
        for uid in staff_recipients(db, client):
            notify(db, uid, "approval_decided", f"{client.name} {verb} {task.title}", comment)
    else:
        title = "Changes requested" if decision == "CHANGES_REQUESTED" else "Approved"
        msg = (f"Changes have been requested for '{task.title}': {comment}" if decision == "CHANGES_REQUESTED"
               else f"'{task.title}' was approved.")
        if task.audience == "client":
            notify(db, getattr(client_user(db, client.id), "id", None), "task_decision",
                   f"{title}: {task.title}", msg, email=decision == "CHANGES_REQUESTED")
    was_done = project.status
    recompute(db, project)
    if project.status == "COMPLETED" and was_done != "COMPLETED":
        audit(db, user, "ONBOARDING_COMPLETED", "project", project.id, f"Onboarding '{project.name}' completed",
              client.id, ip)
        for uid in staff_recipients(db, client):
            notify(db, uid, "onboarding_completed", f"Onboarding completed: {client.name}", project.name)
        notify(db, getattr(client_user(db, client.id), "id", None), "onboarding_completed",
               "Onboarding completed", f"All required steps for '{project.name}' are done. Thank you!")
    return task


def create_project(db: Session, user: User, client: Client, template: Optional[Template],
                   name: Optional[str], due: Optional[date], ip: str) -> Project:
    start = today()
    project = Project(organization_id=user.organization_id, client_id=client.id,
                      template_id=template.id if template else None,
                      name=name or (f"{template.name} onboarding" if template else "Onboarding"),
                      start_date=start, due_date=due)
    if template:
        for i, tt in enumerate(template.tasks):
            project.tasks.append(Task(
                organization_id=user.organization_id, position=i, title=tt.title, description=tt.description,
                type=tt.type, audience=tt.audience, priority=tt.priority, is_required=tt.is_required,
                config=tt.config or {}, due_date=start + timedelta(days=tt.due_in_days) if tt.due_in_days is not None else None,
                assigned_to=client.assigned_user_id if tt.audience == "agency" else None))
    db.add(project)
    db.flush()
    if client.status in ("Invited", "Completed"):
        client.status = "Onboarding"
    recompute(db, project)
    audit(db, user, "ONBOARDING_CREATED", "project", project.id, f"Started '{project.name}' for {client.name}",
          client.id, ip)
    notify(db, getattr(client_user(db, client.id), "id", None), "new_onboarding",
           "New onboarding tasks", f"You have new onboarding tasks from your agency: {project.name}.", email=True)
    return project


# ---------------------------------------------------------------------------- reminders

def run_reminders(db: Session, org_id: Optional[int] = None) -> dict:
    """Remind clients about overdue tasks. Safe to run many times: one reminder per task every 2 days."""
    now, t = utcnow(), today()
    q = select(Task, Project).join(Project, Project.id == Task.project_id).where(
        Task.due_date.is_not(None), Task.due_date < t, Task.status != "APPROVED",
        Task.audience == "client", Project.status == "ACTIVE")
    if org_id:
        q = q.where(Task.organization_id == org_id)
    sent = escalated = 0
    for task, project in db.execute(q).all():
        waiting = (task.status in WAITING_ON_CLIENT) if task.type != "APPROVAL" else task.status in ("SUBMITTED", "RESUBMITTED")
        if not waiting:
            continue
        if task.last_reminded_at and now - task.last_reminded_at < timedelta(days=2):
            continue
        client = db.get(Client, project.client_id)
        cu = client_user(db, client.id)
        if cu:
            notify(db, cu.id, "reminder", f"Reminder: {task.title} is overdue",
                   f"'{task.title}' was due on {task.due_date.isoformat()}. Please complete it.", email=True)
            sent += 1
        task.reminder_count += 1
        task.last_reminded_at = now
        if task.reminder_count >= 2:
            for uid in staff_recipients(db, client):
                notify(db, uid, "client_overdue", f"{client.name} is overdue on {task.title}",
                       f"{task.reminder_count} reminders sent.")
            escalated += 1
    db.commit()
    return {"reminders_sent": sent, "agency_notified": escalated}
