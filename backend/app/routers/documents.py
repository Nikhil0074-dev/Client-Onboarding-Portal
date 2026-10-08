import mimetypes
import os
import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import client_ip, get_client_for, get_current_user, get_task_for
from ..models import Document, Project, Task, User
from ..serializers import document_out
from ..services import audit, notify, recompute, staff_recipients

router = APIRouter(prefix="/api/documents", tags=["documents"])

ALLOWED_EXT = {"pdf", "png", "jpg", "jpeg", "gif", "webp", "svg", "doc", "docx", "xls", "xlsx", "csv", "txt",
               "md", "ppt", "pptx", "zip", "json"}
SIGNATURES = {"pdf": (b"%PDF",), "png": (b"\x89PNG",), "jpg": (b"\xff\xd8\xff",), "jpeg": (b"\xff\xd8\xff",),
              "gif": (b"GIF87a", b"GIF89a")}


def clean_name(name: str) -> str:
    name = os.path.basename((name or "").replace("\\", "/"))
    name = re.sub(r"[^A-Za-z0-9._ \-()]", "_", name).strip(" .")
    return name[:200]


def storage_path(key: str):
    path = (settings.STORAGE_DIR / key).resolve()
    if settings.STORAGE_DIR not in path.parents:
        raise HTTPException(400, "Invalid storage key")
    return path


@router.post("/upload", status_code=201)
async def upload(request: Request, task_id: int = Form(...), file: UploadFile = File(...),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = get_task_for(db, user, task_id)
    if task.status == "APPROVED":
        raise HTTPException(409, "This task is already approved; files can no longer be added")
    name = clean_name(file.filename or "")
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if not name or ext not in ALLOWED_EXT:
        raise HTTPException(415, f"File type not allowed. Allowed: {', '.join(sorted(ALLOWED_EXT))}")
    limit = settings.MAX_UPLOAD_MB * 1024 * 1024
    data = bytearray()
    while chunk := await file.read(1024 * 1024):
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(413, f"File is larger than {settings.MAX_UPLOAD_MB} MB")
    if not data:
        raise HTTPException(422, "The file is empty")
    sigs = SIGNATURES.get(ext)
    if sigs and not any(bytes(data[:8]).startswith(s) for s in sigs):
        raise HTTPException(415, "File content does not match its extension")

    project = db.get(Project, task.project_id)
    key = f"{task.organization_id}/{uuid.uuid4().hex}"
    path = storage_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(data))
    version = (db.scalar(select(func.max(Document.version)).where(
        Document.task_id == task.id, Document.file_name == name)) or 0) + 1
    doc = Document(organization_id=task.organization_id, client_id=project.client_id, task_id=task.id,
                   file_name=name, storage_key=key, file_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
                   file_size=len(data), version=version, uploaded_by=user.id)
    db.add(doc)
    if user.role == "client" and task.status == "NOT_STARTED":
        task.status = "IN_PROGRESS"
        recompute(db, project)
    db.flush()
    audit(db, user, "DOCUMENT_UPLOADED", "document", doc.id, f"{user.name} uploaded {name} (v{version})",
          project.client_id, client_ip(request))
    if user.role == "client":
        for uid in staff_recipients(db, get_client_for(db, user, project.client_id)):
            notify(db, uid, "upload", f"New upload: {name}", f"For task '{task.title}'")
    db.commit()
    return document_out(doc, task)


@router.get("")
def list_documents(client_id: Optional[int] = None, task_id: Optional[int] = None,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stmt = (select(Document, Task).join(Task, Task.id == Document.task_id)
            .where(Document.organization_id == user.organization_id))
    if user.role == "client":
        stmt = stmt.where(Document.client_id == user.client_id, Task.audience == "client")
    elif client_id:
        stmt = stmt.where(Document.client_id == client_id)
    if task_id:
        stmt = stmt.where(Document.task_id == task_id)
    return [document_out(d, t) for d, t in db.execute(stmt.order_by(Document.id.desc()).limit(500)).all()]


def _doc_for(db: Session, user: User, doc_id: int) -> tuple[Document, Task]:
    doc = db.get(Document, doc_id)
    if not doc or doc.organization_id != user.organization_id:
        raise HTTPException(404, "File not found")
    task = get_task_for(db, user, doc.task_id)  # also enforces client-level access
    return doc, task


@router.get("/{doc_id}")
def get_document(doc_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    doc, task = _doc_for(db, user, doc_id)
    out = document_out(doc, task)
    out["versions"] = [document_out(d, task) for d in db.scalars(
        select(Document).where(Document.task_id == doc.task_id, Document.file_name == doc.file_name)
        .order_by(Document.version))]
    return out


@router.get("/{doc_id}/download")
def download(doc_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    doc, task = _doc_for(db, user, doc_id)
    path = storage_path(doc.storage_key)
    if not path.exists():
        raise HTTPException(404, "File is missing from storage")
    audit(db, user, "DOCUMENT_DOWNLOADED", "document", doc.id, f"{user.name} downloaded {doc.file_name}",
          doc.client_id, client_ip(request))
    db.commit()
    return FileResponse(path, media_type="application/octet-stream", filename=doc.file_name,
                        headers={"X-Content-Type-Options": "nosniff"})


@router.delete("/{doc_id}")
def delete_document(doc_id: int, request: Request, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    doc, task = _doc_for(db, user, doc_id)
    if user.role == "client" and doc.uploaded_by != user.id:
        raise HTTPException(403, "You can only delete files you uploaded")
    if task.status == "APPROVED" and user.role == "client":
        raise HTTPException(409, "Approved files cannot be deleted")
    try:
        storage_path(doc.storage_key).unlink(missing_ok=True)
    except OSError:
        pass
    audit(db, user, "DOCUMENT_DELETED", "document", doc.id, f"{user.name} deleted {doc.file_name} (v{doc.version})",
          doc.client_id, client_ip(request))
    db.delete(doc)
    db.commit()
    return {"message": "File deleted"}
