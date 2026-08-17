import pytest
from fastapi.testclient import TestClient

import app as app_module
import db
import routes_master_cv

client = TestClient(app_module.app)

SAMPLE_CV = {
    "full_name": "Jane Doe",
    "target_title": "Software Engineer",
    "email": "jane@example.com",
    "phone": "",
    "location": "Berlin, Germany",
    "links": "",
    "work_authorization": "",
    "photo_path": None,
    "summary": "Experienced engineer.",
    "experience": [
        {"title": "Engineer", "company": "Acme", "location": "Berlin", "dates": "2020-2024", "bullets": ["Built things"]}
    ],
    "education": [],
    "skills": [{"category": None, "items": ["Python"]}],
    "languages": [],
    "extra_sections": [],
}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    monkeypatch.setattr(routes_master_cv, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(routes_master_cv, "RUNS_DIR", tmp_path / "runs")
    routes_master_cv.RUNS_DIR.mkdir(exist_ok=True)
    resp = client.post("/api/auth/register", json={"email": "cvowner@example.com", "password": "testpassword123"})
    assert resp.status_code == 200
    yield resp.json()["user"]["id"]
    client.cookies.clear()


def _seed_master_cv(user_id):
    cv_dir = routes_master_cv.master_cv_dir(user_id)
    cv_dir.mkdir(parents=True)
    import json
    (cv_dir / "content.json").write_text(json.dumps(SAMPLE_CV))
    db.touch_master_cv(user_id)


# -- GET /api/master-cv -------------------------------------------------------

def test_get_master_cv_404_when_none_saved():
    resp = client.get("/api/master-cv")
    assert resp.status_code == 404


def test_get_master_cv_returns_saved_record(_isolate):
    _seed_master_cv(_isolate)
    resp = client.get("/api/master-cv")
    assert resp.status_code == 200
    assert resp.json()["full_name"] == "Jane Doe"
    assert resp.json()["has_photo"] is False


def test_get_master_cv_requires_login():
    client.cookies.clear()
    assert client.get("/api/master-cv").status_code == 401


# -- PUT /api/master-cv --------------------------------------------------------

def test_put_master_cv_requires_existing_record():
    resp = client.put("/api/master-cv", json=SAMPLE_CV)
    assert resp.status_code == 409


def test_put_master_cv_updates_saved_record(_isolate):
    _seed_master_cv(_isolate)
    updated = dict(SAMPLE_CV, full_name="Jane A. Doe")
    resp = client.put("/api/master-cv", json=updated)
    assert resp.status_code == 200
    assert client.get("/api/master-cv").json()["full_name"] == "Jane A. Doe"


def test_put_master_cv_rejects_missing_full_name(_isolate):
    _seed_master_cv(_isolate)
    bad = dict(SAMPLE_CV)
    del bad["full_name"]
    resp = client.put("/api/master-cv", json=bad)
    assert resp.status_code == 422


# -- POST /api/master-cv/upload validation -------------------------------------

def test_upload_rejects_non_pdf_docx():
    resp = client.post(
        "/api/master-cv/upload",
        files={"cv_file": ("cv.txt", b"plain text resume", "text/plain")},
    )
    assert resp.status_code == 400


def test_upload_rejects_oversized_cv():
    oversized = b"x" * (routes_master_cv.MAX_CV_BYTES + 1)
    resp = client.post(
        "/api/master-cv/upload",
        files={"cv_file": ("cv.pdf", oversized, "application/pdf")},
    )
    assert resp.status_code == 400


def test_upload_requires_login():
    client.cookies.clear()
    resp = client.post(
        "/api/master-cv/upload",
        files={"cv_file": ("cv.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 401


class _FakeThread:
    last_args = None

    def __init__(self, target=None, args=(), daemon=None):
        _FakeThread.last_args = args

    def start(self):
        pass


def test_upload_accepts_valid_cv_and_starts_background_job(monkeypatch):
    monkeypatch.setattr(routes_master_cv.threading, "Thread", _FakeThread)
    resp = client.post(
        "/api/master-cv/upload",
        files={"cv_file": ("cv.pdf", b"%PDF-1.4 fake pdf content", "application/pdf")},
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    assert job_id in routes_master_cv.MASTER_CV_JOBS
    routes_master_cv.MASTER_CV_JOBS.pop(job_id, None)


# -- photo endpoints ------------------------------------------------------------

def test_photo_upload_requires_master_cv():
    resp = client.post(
        "/api/master-cv/photo",
        files={"photo": ("headshot.jpg", b"fake jpg bytes", "image/jpeg")},
    )
    assert resp.status_code == 409


def test_photo_upload_and_delete(_isolate):
    _seed_master_cv(_isolate)
    resp = client.post(
        "/api/master-cv/photo",
        files={"photo": ("headshot.jpg", b"fake jpg bytes", "image/jpeg")},
    )
    assert resp.status_code == 200
    assert (routes_master_cv.master_cv_dir(_isolate) / "photo.jpg").exists()

    assert client.get("/api/master-cv").json()["has_photo"] is True

    resp = client.delete("/api/master-cv/photo")
    assert resp.status_code == 200
    assert not (routes_master_cv.master_cv_dir(_isolate) / "photo.jpg").exists()
    assert client.get("/api/master-cv").json()["has_photo"] is False


def test_get_photo_404_when_none_saved(_isolate):
    _seed_master_cv(_isolate)
    resp = client.get("/api/master-cv/photo")
    assert resp.status_code == 404


def test_get_photo_requires_login():
    client.cookies.clear()
    assert client.get("/api/master-cv/photo").status_code == 401


def test_get_photo_returns_image_bytes(_isolate):
    _seed_master_cv(_isolate)
    client.post(
        "/api/master-cv/photo",
        files={"photo": ("headshot.jpg", b"fake jpg bytes", "image/jpeg")},
    )
    resp = client.get("/api/master-cv/photo")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.content == b"fake jpg bytes"


def test_get_photo_is_ownership_scoped(_isolate):
    _seed_master_cv(_isolate)
    client.post(
        "/api/master-cv/photo",
        files={"photo": ("headshot.png", b"fake png bytes", "image/png")},
    )
    client.cookies.clear()
    client.post("/api/auth/register", json={"email": "otherviewer@example.com", "password": "testpassword123"})
    resp = client.get("/api/master-cv/photo")
    assert resp.status_code == 404
