import pytest
from fastapi.testclient import TestClient

import app as app_module
import auth
import db

client = TestClient(app_module.app)


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    yield
    client.cookies.clear()


# -- password hashing ---------------------------------------------------------

def test_hash_password_round_trips():
    hashed = auth.hash_password("correct horse battery staple")
    assert auth.verify_password("correct horse battery staple", hashed) is True


def test_verify_password_rejects_wrong_password():
    hashed = auth.hash_password("correct horse battery staple")
    assert auth.verify_password("wrong password", hashed) is False


def test_hash_password_uses_distinct_salts():
    assert auth.hash_password("same password") != auth.hash_password("same password")


def test_verify_password_rejects_malformed_hash():
    assert auth.verify_password("anything", "not-a-real-hash") is False


# -- /api/auth/register --------------------------------------------------------

def test_register_creates_account_and_sets_cookie():
    resp = client.post("/api/auth/register", json={"email": "new@example.com", "password": "testpassword123"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email"] == "new@example.com"
    assert body["user"]["has_master_cv"] is False
    assert auth.SESSION_COOKIE_NAME in resp.cookies


def test_register_rejects_invalid_email():
    resp = client.post("/api/auth/register", json={"email": "not-an-email", "password": "testpassword123"})
    assert resp.status_code == 400


def test_register_rejects_short_password():
    resp = client.post("/api/auth/register", json={"email": "short@example.com", "password": "abc"})
    assert resp.status_code == 400


def test_register_rejects_duplicate_email():
    client.post("/api/auth/register", json={"email": "dupe@example.com", "password": "testpassword123"})
    resp = client.post("/api/auth/register", json={"email": "dupe@example.com", "password": "testpassword123"})
    assert resp.status_code == 409


# -- /api/auth/login ------------------------------------------------------------

def test_login_succeeds_with_correct_credentials():
    client.post("/api/auth/register", json={"email": "login@example.com", "password": "testpassword123"})
    client.cookies.clear()
    resp = client.post("/api/auth/login", json={"email": "login@example.com", "password": "testpassword123"})
    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == "login@example.com"


def test_login_rejects_wrong_password():
    client.post("/api/auth/register", json={"email": "login2@example.com", "password": "testpassword123"})
    client.cookies.clear()
    resp = client.post("/api/auth/login", json={"email": "login2@example.com", "password": "wrongpassword"})
    assert resp.status_code == 401


def test_login_rejects_unknown_email():
    resp = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "testpassword123"})
    assert resp.status_code == 401


# -- /api/auth/me and /api/auth/logout -------------------------------------------

def test_me_returns_401_without_session():
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_returns_user_when_logged_in():
    client.post("/api/auth/register", json={"email": "me@example.com", "password": "testpassword123"})
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == "me@example.com"


def test_logout_clears_session():
    client.post("/api/auth/register", json={"email": "logout@example.com", "password": "testpassword123"})
    assert client.get("/api/auth/me").status_code == 200
    logout_resp = client.post("/api/auth/logout")
    assert logout_resp.status_code == 200
    assert client.get("/api/auth/me").status_code == 401


# -- guarded tailor routes require auth -----------------------------------------

def test_tailor_start_requires_login():
    resp = client.post(
        "/api/tailor/start",
        files={"cv_file": ("cv.pdf", b"%PDF-1.4 fake pdf content", "application/pdf")},
        data={"job_text": "We need a data analyst.", "output_format": "pdf"},
    )
    assert resp.status_code == 401


def test_tailor_status_requires_login():
    assert client.get("/api/tailor/some-run-id/status").status_code == 401


def test_tailor_ownership_check_returns_404_for_other_users_run(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "RUNS_DIR", tmp_path)
    client.post("/api/auth/register", json={"email": "owner@example.com", "password": "testpassword123"})
    owner_id = client.get("/api/auth/me").json()["id"]
    app_module.RUNS["owned-by-someone-else"] = {
        "step": "Done!", "percent": 100, "done": True, "error": None,
        "output_file": tmp_path / "output.pdf", "output_format": "pdf", "download_name": "cv",
        "rationale": None, "proc": None, "cancelled": False, "revising": False,
        "revision_count": 0, "cover_letter_file": None, "user_id": owner_id + 999,
    }
    try:
        resp = client.get("/api/tailor/owned-by-someone-else/status")
        assert resp.status_code == 404
    finally:
        app_module.RUNS.pop("owned-by-someone-else", None)
