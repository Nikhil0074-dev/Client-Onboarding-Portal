from datetime import date, timedelta

from .conftest import Agency, ClientUser, make_client_user, uid

PDF = b"%PDF-1.4\n%test\n"


def tpl_id(agency, name="Web Development"):
    return next(t["id"] for t in agency.get("/api/templates").json() if t["name"] == name)


# ----------------------------------------------------------------------------- auth

def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_register_login_me_logout(client, agency):
    assert agency.user["role"] == "org_admin"
    r = client.post("/api/auth/login", json={"email": agency.email, "password": "password123"})
    assert r.status_code == 200
    tok = r.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert me.json()["email"] == agency.email
    assert client.post("/api/auth/logout", headers={"Authorization": f"Bearer {tok}"}).status_code == 200


def test_register_validation_and_duplicates(client, agency):
    r = client.post("/api/auth/register", json={"organization_name": "X", "name": "Y", "email": agency.email,
                                                "password": "password123"})
    assert r.status_code == 409
    r = client.post("/api/auth/register", json={"organization_name": "X", "name": "Y", "email": "bad", "password": "short"})
    assert r.status_code == 422


def test_bad_login_and_no_token(client, agency):
    assert client.post("/api/auth/login", json={"email": agency.email, "password": "wrong-pass"}).status_code == 401
    assert client.get("/api/clients").status_code == 401
    assert client.get("/api/clients", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_login_rate_limit(client):
    for _ in range(10):
        assert client.post("/api/auth/login", json={"email": "nobody@othertest.com", "password": "x"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "nobody@othertest.com", "password": "x"}).status_code == 429


def test_verify_email(client, agency):
    r = client.post("/api/auth/verify-email", json={"token": agency.verification_token})
    assert r.status_code == 200
    assert agency.get("/api/auth/me").json()["email_verified"] is True


def test_password_reset_single_use(client, agency):
    r = client.post("/api/auth/forgot-password", json={"email": agency.email})
    token = r.json()["reset_token"]
    assert client.post("/api/auth/reset-password", json={"token": token, "new_password": "newpassword9"}).status_code == 200
    assert client.post("/api/auth/login", json={"email": agency.email, "password": "newpassword9"}).status_code == 200
    # same token cannot be reused
    assert client.post("/api/auth/reset-password", json={"token": token, "new_password": "another-pass1"}).status_code == 400
    # unknown email gives the same generic answer
    assert client.post("/api/auth/forgot-password", json={"email": "ghost@othertest.com"}).status_code == 200


# ----------------------------------------------------------------------------- team / roles

def test_team_and_roles(client, agency):
    r = agency.post("/api/team", {"name": "Rahul", "email": f"r-{uid()}@agencytest.com", "password": "password123"})
    assert r.status_code == 201
    login = client.post("/api/auth/login", json={"email": r.json()["email"], "password": "password123"})
    member = Agency.__new__(Agency)
    member.c, member.token = client, login.json()["access_token"]
    assert member.get("/api/clients").status_code == 200
    # team member cannot manage team, templates or see audit logs
    assert member.post("/api/team", {"name": "Z", "email": f"z-{uid()}@agencytest.com", "password": "password123"}).status_code == 403
    assert member.post("/api/templates", {"name": "T"}).status_code == 403
    assert member.get("/api/audit-logs").status_code == 403
    # admin can disable member; disabled member cannot log in
    assert agency.patch(f"/api/team/{r.json()['id']}", {"status": "disabled"}).status_code == 200
    assert client.post("/api/auth/login", json={"email": r.json()["email"], "password": "password123"}).status_code == 403
    # admin cannot disable self
    assert agency.patch(f"/api/team/{agency.user['id']}", {"status": "disabled"}).status_code == 400


# ----------------------------------------------------------------------------- clients

def test_client_crud_search_and_archive(agency):
    r = agency.post("/api/clients", {"name": "Zed Zebra", "company_name": "Stripes Ltd", "email": f"z-{uid()}@clienttest.com"})
    assert r.status_code == 201
    cid = r.json()["id"]
    assert r.json()["status"] == "Invited"
    assert agency.post("/api/clients", {"name": "Dup", "email": r.json()["email"]}).status_code == 409
    assert any(c["id"] == cid for c in agency.get("/api/clients", params={"q": "stripes"}).json()["items"])
    assert not agency.get("/api/clients", params={"q": "nomatchxyz"}).json()["items"]
    assert agency.patch(f"/api/clients/{cid}", {"phone": "123"}).json()["phone"] == "123"
    assert agency.delete(f"/api/clients/{cid}").status_code == 200
    assert all(c["id"] != cid for c in agency.get("/api/clients").json()["items"])
    assert agency.get("/api/clients", params={"status": "Archived"}).json()["total"] == 1


def test_invite_flow(client, agency):
    cid = agency.post("/api/clients", {"name": "Inv", "email": f"i-{uid()}@clienttest.com"}).json()["id"]
    inv = agency.post(f"/api/clients/{cid}/invite").json()
    assert client.post("/api/auth/accept-invite", json={"token": "bad-token", "password": "password123"}).status_code == 400
    ok = client.post("/api/auth/accept-invite", json={"token": inv["token"], "password": "password123"})
    assert ok.status_code == 200 and ok.json()["user"]["role"] == "client"
    # single use
    assert client.post("/api/auth/accept-invite", json={"token": inv["token"], "password": "password123"}).status_code == 400
    # cannot invite again once accepted
    assert agency.post(f"/api/clients/{cid}/invite").status_code == 409


def test_client_cannot_use_staff_endpoints(client, agency):
    cid, cu = make_client_user(client, agency)
    for url in ("/api/clients", "/api/templates", "/api/team", "/api/analytics", "/api/audit-logs"):
        assert cu.get(url).status_code == 403, url
    assert cu.get(f"/api/clients/{cid}").status_code == 200


# ----------------------------------------------------------------------------- templates

def test_default_templates_and_custom_template(agency):
    names = {t["name"] for t in agency.get("/api/templates").json()}
    assert {"Web Development", "Digital Marketing", "Freelancer Design"} <= names
    r = agency.post("/api/templates", {"name": "Mine", "tasks": [
        {"title": "A", "type": "INFORMATION"}, {"title": "B", "type": "FILE_UPLOAD", "is_required": False}]})
    assert r.status_code == 201 and len(r.json()["tasks"]) == 2
    tid = r.json()["id"]
    r = agency.patch(f"/api/templates/{tid}", {"tasks": [{"title": "Only"}]})
    assert [t["title"] for t in r.json()["tasks"]] == ["Only"]
    assert agency.delete(f"/api/templates/{tid}").status_code == 200
    assert agency.get(f"/api/templates/{tid}").status_code == 404
    assert agency.post("/api/templates", {"name": "X", "tasks": [{"title": "t", "type": "BOGUS"}]}).status_code == 422


# ----------------------------------------------------------------------------- full workflow

def test_full_onboarding_workflow(client, agency):
    cid, cu = make_client_user(client, agency)
    tpl = agency.post("/api/templates", {"name": "Flow", "tasks": [
        {"title": "Info", "type": "INFORMATION", "due_in_days": 5},
        {"title": "Logo", "type": "FILE_UPLOAD"},
        {"title": "Design", "type": "APPROVAL"},
        {"title": "Internal check", "type": "INTERNAL_TASK", "audience": "agency"},
        {"title": "Nice to have", "type": "INFORMATION", "is_required": False},
    ]}).json()
    p = agency.post("/api/onboarding", {"client_id": cid, "template_id": tpl["id"]})
    assert p.status_code == 201
    pid = p.json()["id"]
    tasks = {t["title"]: t for t in p.json()["tasks"]}
    assert p.json()["progress"] == 0 and p.json()["required_total"] == 4
    assert agency.get(f"/api/clients/{cid}").json()["status"] == "Onboarding"

    # client only sees client tasks
    cp = cu.get(f"/api/onboarding/{pid}").json()
    assert {t["title"] for t in cp["tasks"]} == {"Info", "Logo", "Design", "Nice to have"}
    internal_id = tasks["Internal check"]["id"]
    assert cu.get(f"/api/tasks/{internal_id}").status_code == 404

    # information task: empty submit fails, real submit works
    info = tasks["Info"]["id"]
    assert cu.post(f"/api/tasks/{info}/submit", {"content": {"text": "  "}}).status_code == 422
    assert cu.post(f"/api/tasks/{info}/submit", {"content": {"text": "We sell chairs"}}).json()["status"] == "SUBMITTED"
    assert cu.post(f"/api/tasks/{info}/submit", {"content": {"text": "again"}}).status_code == 409
    assert agency.get(f"/api/clients/{cid}").json()["status"] == "Under Review"
    # client cannot approve own work
    assert cu.post(f"/api/tasks/{info}/approve", {}).status_code == 403
    # staff asks for changes (comment required)
    assert agency.post(f"/api/tasks/{info}/request-changes", {"comment": ""}).status_code == 422
    r = agency.post(f"/api/tasks/{info}/request-changes", {"comment": "Add your address"})
    assert r.json()["status"] == "CHANGES_REQUESTED"
    assert agency.get(f"/api/clients/{cid}").json()["status"] == "Changes Requested"
    assert cu.post(f"/api/tasks/{info}/submit", {"content": {"text": "We sell chairs at 1 Main St"}}).json()["status"] == "RESUBMITTED"
    assert agency.post(f"/api/tasks/{info}/review").json()["status"] == "UNDER_REVIEW"
    assert agency.post(f"/api/tasks/{info}/approve", {"comment": "Thanks"}).json()["status"] == "APPROVED"
    assert cu.get(f"/api/onboarding/{pid}").json()["progress"] == 33  # 1 of 3 client-visible required tasks

    # file upload with versioning
    logo = tasks["Logo"]["id"]
    assert cu.post(f"/api/tasks/{logo}/submit", {"content": {}}).status_code == 422  # no file yet
    up = lambda name, data: cu.post("/api/documents/upload", data={"task_id": str(logo)}, files={"file": (name, data)})  # noqa: E731
    assert up("virus.exe", b"MZ").status_code == 415
    assert up("fake.pdf", b"not a pdf").status_code == 415
    assert up("empty.txt", b"").status_code == 422
    v1 = up("brand.pdf", PDF)
    assert v1.status_code == 201 and v1.json()["version"] == 1
    v2 = up("brand.pdf", PDF + b"v2")
    assert v2.json()["version"] == 2
    assert cu.get(f"/api/tasks/{logo}").json()["status"] == "IN_PROGRESS"
    dl = cu.get(f"/api/documents/{v2.json()['id']}/download")
    assert dl.status_code == 200 and dl.content == PDF + b"v2"
    assert len(cu.get(f"/api/documents/{v2.json()['id']}").json()["versions"]) == 2
    assert cu.post(f"/api/tasks/{logo}/submit", {"content": {}}).json()["status"] == "SUBMITTED"
    assert agency.post(f"/api/tasks/{logo}/approve", {}).json()["status"] == "APPROVED"
    assert up("late.pdf", PDF).status_code == 409  # approved tasks are locked

    # approval task: only staff can request, only client can decide
    design = tasks["Design"]["id"]
    assert cu.post(f"/api/tasks/{design}/submit", {"content": {}}).status_code == 403
    ar = agency.post("/api/approvals", {"task_id": design, "comment": "Homepage v1",
                                        "deadline": (date.today() + timedelta(days=3)).isoformat()})
    assert ar.status_code == 201 and ar.json()["status"] == "PENDING"
    assert agency.post(f"/api/tasks/{design}/approve", {}).status_code == 403
    assert len(cu.get("/api/approvals", params={"status": "PENDING"}).json()) == 1
    assert cu.post(f"/api/approvals/{ar.json()['id']}/request-changes", {"comment": "Bigger logo"}).json()["status"] == "CHANGES_REQUESTED"
    ar2 = agency.post("/api/approvals", {"task_id": design, "comment": "Homepage v2"})
    assert ar2.status_code == 201
    assert cu.post(f"/api/approvals/{ar2.json()['id']}/approve", {}).json()["status"] == "APPROVED"
    assert cu.post(f"/api/approvals/{ar2.json()['id']}/approve", {}).status_code == 409

    # still not complete: internal task open
    assert agency.get(f"/api/onboarding/{pid}").json()["status"] == "ACTIVE"
    assert agency.post(f"/api/tasks/{internal_id}/request-changes", {"comment": "x"}).status_code == 400
    assert agency.post(f"/api/tasks/{internal_id}/approve", {}).json()["status"] == "APPROVED"
    done = agency.get(f"/api/onboarding/{pid}").json()
    assert done["progress"] == 100 and done["status"] == "COMPLETED"
    assert agency.get(f"/api/clients/{cid}").json()["status"] == "Completed"

    # optional task didn't block completion; notifications and timeline exist
    n = cu.get("/api/notifications").json()
    assert n["unread"] > 0 and any(i["type"] == "onboarding_completed" for i in n["items"])
    assert cu.post("/api/notifications/read-all").status_code == 200
    assert cu.get("/api/notifications").json()["unread"] == 0
    tl = agency.get(f"/api/clients/{cid}/timeline").json()
    assert any(e["action"] == "ONBOARDING_COMPLETED" for e in tl)

    # dashboards and analytics
    d = agency.get("/api/dashboard").json()
    assert d["stats"]["completed"] == 1 and d["stats"]["total_clients"] == 1
    a = agency.get("/api/analytics").json()
    assert a["completion_rate"] == 100 and a["changes_requested_total"] == 2
    assert cu.get("/api/dashboard").json()["projects"][0]["progress"] == 100
    logs = agency.get("/api/audit-logs").json()
    assert any(l["action"] == "TASK_APPROVED" and l["meta"]["new"] == "APPROVED" for l in logs)


def test_form_validation(client, agency):
    cid, cu = make_client_user(client, agency)
    pid = agency.post("/api/onboarding", {"client_id": cid, "template_id": tpl_id(agency)}).json()["id"]
    form = next(t for t in agency.get(f"/api/onboarding/{pid}/tasks").json() if t["title"] == "Company Information")
    assert cu.post(f"/api/tasks/{form['id']}/submit", {"content": {"company_name": "X"}}).status_code == 422
    ok = cu.post(f"/api/tasks/{form['id']}/submit", {"content": {"company_name": "X", "description": "d", "primary_contact": "me"}})
    assert ok.status_code == 200


def test_comments_and_internal_notes(client, agency):
    cid, cu = make_client_user(client, agency)
    pid = agency.post("/api/onboarding", {"client_id": cid, "template_id": tpl_id(agency)}).json()["id"]
    tid = agency.get(f"/api/onboarding/{pid}/tasks").json()[0]["id"]
    assert cu.post(f"/api/tasks/{tid}/comments", {"body": "Hi from client"}).status_code == 201
    assert agency.post(f"/api/tasks/{tid}/comments", {"body": "Client is slow, follow up Friday", "is_internal": True}).status_code == 201
    assert agency.post(f"/api/tasks/{tid}/comments", {"body": "Visible reply"}).status_code == 201
    assert cu.post(f"/api/tasks/{tid}/comments", {"body": "sneaky", "is_internal": True}).status_code == 403
    client_view = [c["body"] for c in cu.get(f"/api/tasks/{tid}/comments").json()]
    assert client_view == ["Hi from client", "Visible reply"]
    assert len(agency.get(f"/api/tasks/{tid}/comments").json()) == 3
    assert "Client is slow" not in str(cu.get(f"/api/tasks/{tid}").json())


# ----------------------------------------------------------------------------- isolation / security

def test_cross_organization_and_cross_client_isolation(client):
    a, b = Agency(client, "Agency A"), Agency(client, "Agency B")
    cid_a, cu_a = make_client_user(client, a)
    cid_a2, cu_a2 = make_client_user(client, a)
    pid = a.post("/api/onboarding", {"client_id": cid_a, "template_id": tpl_id(a)}).json()["id"]
    tid = a.get(f"/api/onboarding/{pid}/tasks").json()[0]["id"]
    up = cu_a.post("/api/documents/upload", data={"task_id": str(tid)}, files={"file": ("a.txt", b"secret")})
    assert up.status_code == 201
    did = up.json()["id"]

    # another agency sees nothing
    assert b.get(f"/api/clients/{cid_a}").status_code == 404
    assert b.get(f"/api/onboarding/{pid}").status_code == 404
    assert b.get(f"/api/tasks/{tid}").status_code == 404
    assert b.get(f"/api/documents/{did}/download").status_code == 404
    assert b.post("/api/onboarding", {"client_id": cid_a}).status_code == 404
    assert b.get("/api/clients").json()["total"] == 0
    assert b.get("/api/documents").json() == []
    # another client of the same agency sees nothing
    assert cu_a2.get(f"/api/clients/{cid_a}").status_code == 404
    assert cu_a2.get(f"/api/onboarding/{pid}").status_code == 404
    assert cu_a2.get(f"/api/documents/{did}/download").status_code == 404
    assert cu_a2.get("/api/documents").json() == []
    assert cu_a2.post("/api/documents/upload", data={"task_id": str(tid)}, files={"file": ("x.txt", b"x")}).status_code == 404
    assert cu_a2.delete(f"/api/documents/{did}").status_code == 404
    # owner can still delete own file
    assert cu_a.delete(f"/api/documents/{did}").status_code == 200


def test_tampered_token_rejected(client, agency):
    tok = agency.token[:-3] + ("abc" if not agency.token.endswith("abc") else "xyz")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401
    # a reset token is not an access token
    reset = client.post("/api/auth/forgot-password", json={"email": agency.email}).json()["reset_token"]
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {reset}"}).status_code == 401


def test_path_traversal_filename_is_sanitized(client, agency):
    cid, cu = make_client_user(client, agency)
    pid = agency.post("/api/onboarding", {"client_id": cid, "template_id": tpl_id(agency)}).json()["id"]
    tid = agency.get(f"/api/onboarding/{pid}/tasks").json()[0]["id"]
    r = cu.post("/api/documents/upload", data={"task_id": str(tid)}, files={"file": ("../../etc/passwd.txt", b"x")})
    assert r.status_code == 201 and "/" not in r.json()["file_name"] and ".." not in r.json()["file_name"]


# ----------------------------------------------------------------------------- reminders / search / platform

def test_reminders(client, agency):
    cid, cu = make_client_user(client, agency)
    tpl = agency.post("/api/templates", {"name": "R", "tasks": [{"title": "Late one", "type": "INFORMATION"}]}).json()
    pid = agency.post("/api/onboarding", {"client_id": cid, "template_id": tpl["id"]}).json()["id"]
    tid = agency.get(f"/api/onboarding/{pid}/tasks").json()[0]["id"]
    agency.patch(f"/api/tasks/{tid}", {"due_date": (date.today() - timedelta(days=3)).isoformat()})
    assert agency.get("/api/tasks", params={"overdue": True}).json()[0]["id"] == tid
    assert agency.get("/api/dashboard").json()["stats"]["overdue_tasks"] == 1
    assert agency.post("/api/reminders/run").json()["reminders_sent"] == 1
    assert agency.post("/api/reminders/run").json()["reminders_sent"] == 0  # throttled
    assert any(n["type"] == "reminder" for n in cu.get("/api/notifications").json()["items"])
    assert agency.get("/api/tasks", params={"q": "late"}).json()[0]["id"] == tid


def test_platform_admin(client, agency):
    login = client.post("/api/auth/login", json={"email": "root@platformtest.com", "password": "platform-pass-1"})
    assert login.status_code == 200
    h = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get("/api/platform/stats", headers=h).json()["organizations"] >= 1
    assert len(client.get("/api/platform/organizations", headers=h).json()) >= 1
    assert agency.get("/api/platform/stats").status_code == 403
    assert client.get("/api/clients", headers=h).status_code == 403


def test_validation_errors(agency):
    assert agency.post("/api/clients", {"name": "", "email": "nope"}).status_code == 422
    assert agency.get("/api/clients/999999").status_code == 404
    assert agency.post("/api/onboarding", {"client_id": 999999}).status_code == 404
    assert agency.post("/api/tasks/999999/approve", {}).status_code == 404
