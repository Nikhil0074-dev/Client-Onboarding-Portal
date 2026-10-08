from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import Client, Project, Task, User
from .security import decode_token

bearer = HTTPBearer(auto_error=False)
STAFF_ROLES = ("org_admin", "team_member")


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)
) -> User:
    if creds is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(creds.credentials, "access")
    user = db.get(User, int(payload["sub"]))
    if user is None or user.status != "active":
        raise HTTPException(status_code=401, detail="Account not available")
    return user


def require_staff(user: User = Depends(get_current_user)) -> User:
    if user.role not in STAFF_ROLES:
        raise HTTPException(status_code=403, detail="Agency staff only")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "org_admin":
        raise HTTPException(status_code=403, detail="Organization admin only")
    return user


def require_client(user: User = Depends(get_current_user)) -> User:
    if user.role != "client":
        raise HTTPException(status_code=403, detail="Client only")
    return user


def require_platform_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "platform_admin":
        raise HTTPException(status_code=403, detail="Platform admin only")
    return user


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


# ---- object-level authorization helpers ---------------------------------------------------------

def get_client_for(db: Session, user: User, client_id: int) -> Client:
    """Return the client if this user may see it, otherwise 404 (never reveal existence)."""
    client = db.get(Client, client_id)
    if client is None or client.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Client not found")
    if user.role == "client" and user.client_id != client.id:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


def get_project_for(db: Session, user: User, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Onboarding not found")
    if user.role == "client" and user.client_id != project.client_id:
        raise HTTPException(status_code=404, detail="Onboarding not found")
    return project


def get_task_for(db: Session, user: User, task_id: int) -> Task:
    task = db.get(Task, task_id)
    if task is None or task.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Task not found")
    if user.role == "client":
        project = db.get(Project, task.project_id)
        if project is None or project.client_id != user.client_id or task.audience != "client":
            raise HTTPException(status_code=404, detail="Task not found")
    return task
