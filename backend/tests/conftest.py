import os
import tempfile
import uuid

_tmp = tempfile.mkdtemp(prefix="onb_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["STORAGE_DIR"] = f"{_tmp}/storage"
os.environ["DEBUG_RETURN_TOKENS"] = "true"
os.environ["REMINDER_INTERVAL_MINUTES"] = "0"
os.environ["PLATFORM_ADMIN_EMAIL"] = "root@platformtest.com"
os.environ["PLATFORM_ADMIN_PASSWORD"] = "platform-pass-1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def uid() -> str:
    return uuid.uuid4().hex[:8]


class Agency:
    """Helper: a registered agency admin with convenience request methods."""

    def __init__(self, client, name="Acme Agency"):
        self.c = client
        self.email = f"admin-{uid()}@agencytest.com"
        r = client.post("/api/auth/register", json={"organization_name": name, "name": "Admin One",
                                                    "email": self.email, "password": "password123"})
        assert r.status_code == 201, r.text
        self.token = r.json()["access_token"]
        self.user = r.json()["user"]
        self.verification_token = r.json()["verification_token"]

    @property
    def h(self):
        return {"Authorization": f"Bearer {self.token}"}

    def get(self, url, **kw):
        return self.c.get(url, headers=self.h, **kw)

    def post(self, url, json=None, **kw):
        return self.c.post(url, json=json, headers=self.h, **kw)

    def patch(self, url, json=None):
        return self.c.patch(url, json=json, headers=self.h)

    def delete(self, url):
        return self.c.delete(url, headers=self.h)


class ClientUser:
    def __init__(self, client, token):
        self.c, self.token = client, token

    @property
    def h(self):
        return {"Authorization": f"Bearer {self.token}"}

    def get(self, url, **kw):
        return self.c.get(url, headers=self.h, **kw)

    def post(self, url, json=None, **kw):
        return self.c.post(url, json=json, headers=self.h, **kw)

    def delete(self, url):
        return self.c.delete(url, headers=self.h)


def make_client_user(client, agency, **extra):
    r = agency.post("/api/clients", {"name": "XYZ Corp", "email": f"xyz-{uid()}@clienttest.com", **extra})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    inv = agency.post(f"/api/clients/{cid}/invite")
    assert inv.status_code == 200, inv.text
    acc = client.post("/api/auth/accept-invite", json={"token": inv.json()["token"], "password": "clientpass1"})
    assert acc.status_code == 200, acc.text
    return cid, ClientUser(client, acc.json()["access_token"])


@pytest.fixture()
def agency(client):
    return Agency(client)
