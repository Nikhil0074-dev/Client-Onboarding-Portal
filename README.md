# Client Onboarding Portal

A web app where agencies and freelancers onboard clients: checklists, forms, file uploads
(with versions), approvals, comments, reminders, dashboards and an audit trail.

## Quick start (no Docker)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

Open http://localhost:8000 and click **Create an agency account**.
API docs: http://localhost:8000/docs

## With Docker (PostgreSQL)

```bash
cp .env.example .env     # then edit SECRET_KEY
docker compose up --build
```

## Run the tests

```bash
cd backend
pytest -q
```

## How a typical flow works

1. Agency admin registers (3 starter templates are created automatically).
2. Create a client -> **Send invitation** -> copy the link (it is also emailed if SMTP is set).
3. Client opens the link, sets a password, sees only their own tasks.
4. Agency: **Start onboarding** from a template. Client fills forms, uploads files, submits.
5. Agency approves or requests changes. For "Approval" tasks the agency uploads the item and the client approves.
6. When every required task is approved the onboarding is marked **COMPLETED**.

## Roles

| Role | Can do |
|---|---|
| platform_admin | See all organizations and totals (set PLATFORM_ADMIN_* in `.env`) |
| org_admin | Everything in their agency: team, templates, audit log, archive clients |
| team_member | Manage clients, review tasks, comment, upload |
| client | See and complete only their own client-visible tasks; never sees internal notes or internal tasks |

## Project layout

```
backend/app/        FastAPI app (models, routers, services)
backend/tests/      pytest suite (API + security checks)
frontend/           Plain HTML/CSS/JS single-page UI (no build step), served by the backend
docker-compose.yml  App + PostgreSQL
```

## Security notes

- Passwords hashed with bcrypt; login rate-limited; reset tokens are single-use.
- Every record is checked against the user's organization (and client) on every request.
- Uploads: extension allow-list, size limit, signature check for PDF/PNG/JPG/GIF, random storage names, download only through an authenticated endpoint.
- Before going live: set a strong `SECRET_KEY`, serve over HTTPS, set `DEBUG_RETURN_TOKENS=false`.
