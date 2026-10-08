# Architecture

Browser (static JS) -> FastAPI (`/api/*`) -> SQLAlchemy -> SQLite (dev) / PostgreSQL (docker)
Uploaded files -> local disk, random keys, served only via `/api/documents/{id}/download`.

Task status flow:
NOT_STARTED -> IN_PROGRESS -> SUBMITTED -> UNDER_REVIEW -> APPROVED
                                   \-> CHANGES_REQUESTED -> RESUBMITTED -> (review again)

Approval tasks (type APPROVAL) reverse the roles: staff request, client decides.
Progress = approved required tasks / required tasks x 100. A project becomes COMPLETED at 100%.
Overdue reminders: `POST /api/reminders/run` or the hourly background loop (one reminder per task per 2 days;
from the 2nd reminder the assigned team member is also notified).
