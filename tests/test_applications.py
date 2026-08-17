import json

import pytest
from fastapi.testclient import TestClient

import app as app_module
import db
import routes_applications

client = TestClient(app_module.app)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    monkeypatch.setattr(app_module, "RUNS_DIR", tmp_path / "runs")
    app_module.RUNS_DIR.mkdir()
    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(routes_applications, "DATA_DIR", tmp_path / "data")
    resp = client.post("/api/auth/register", json={"email": "appowner@example.com", "password": "testpassword123"})
    assert resp.status_code == 200
    user_id = resp.json()["user"]["id"]
    yield user_id
    client.cookies.clear()
    app_module.RUNS.clear()


# -- _slugify -----------------------------------------------------------------

def test_slugify_lowercases_and_dashes():
    assert routes_applications._slugify("Acme Corp, Inc.", "company") == "acme-corp-inc"


def test_slugify_falls_back_when_empty():
    assert routes_applications._slugify("   ", "company") == "company"
    assert routes_applications._slugify("!!!", "role") == "role"


def test_slugify_truncates_long_text():
    assert len(routes_applications._slugify("x" * 200, "company")) <= 60


# -- _resolve_company_role_slugs -----------------------------------------------

def test_resolve_slugs_reuses_existing_company_case_insensitively(_isolate):
    user_id = _isolate
    db.find_or_create_application(user_id, "Acme Corp", "acme-corp", "Engineer", "engineer")
    slug1, _ = app_module._resolve_company_role_slugs(user_id, "acme corp", "Designer")
    assert slug1 == "acme-corp"


def test_resolve_slugs_collision_appends_suffix(_isolate):
    user_id = _isolate
    db.find_or_create_application(user_id, "Acme Co", "acme", "Engineer", "engineer")
    company_slug, _ = app_module._resolve_company_role_slugs(user_id, "ACME", "Designer")
    assert company_slug == "acme-2"


def test_resolve_slugs_role_scoped_to_company(_isolate):
    user_id = _isolate
    db.find_or_create_application(user_id, "Acme", "acme", "Engineer", "engineer")
    db.find_or_create_application(user_id, "Beta", "beta", "Engineer", "engineer")
    # A new company can reuse the same role slug -- role uniqueness is scoped per company.
    company_slug, role_slug = app_module._resolve_company_role_slugs(user_id, "Gamma", "Engineer")
    assert company_slug == "gamma"
    assert role_slug == "engineer"


# -- _finalize_application_run -------------------------------------------------

def _seed_run(run_id, run_dir, user_id, metadata=None, output_format="pdf"):
    run_dir.mkdir(parents=True)
    output_file = run_dir / f"output.{output_format}"
    output_file.write_bytes(b"%PDF-1.4 fake")
    if metadata is not None:
        (run_dir / "metadata.json").write_text(json.dumps(metadata))
    app_module.RUNS[run_id] = {
        "output_file": output_file,
        "output_format": output_format,
        "download_name": "tailored_cv",
        "cover_letter_file": None,
        "user_id": user_id,
        "run_dir": run_dir,
        "metadata_file": run_dir / "metadata.json",
        "application": None,
        "error": None,
    }
    return run_dir


def test_finalize_files_run_when_company_and_role_known(_isolate, tmp_path):
    user_id = _isolate
    run_dir = _seed_run("run-1", tmp_path / "runs" / "run-1", user_id, {"company_name": "Acme", "role_name": "Backend Engineer"})
    app_module._finalize_application_run("run-1")

    run = app_module.RUNS["run-1"]
    assert run["application"]["pending"] is False
    assert run["application"]["company_name"] == "Acme"
    assert not run_dir.exists()
    assert run["run_dir"].exists()
    assert run["output_file"].exists()

    tree = db.list_applications_tree(user_id)
    assert tree[0]["company_name"] == "Acme"
    assert tree[0]["roles"][0]["role_name"] == "Backend Engineer"
    assert len(tree[0]["roles"][0]["attempts"]) == 1


def test_finalize_creates_pending_when_role_missing(_isolate, tmp_path):
    user_id = _isolate
    run_dir = _seed_run("run-2", tmp_path / "runs" / "run-2", user_id, {"company_name": "Acme", "role_name": ""})
    app_module._finalize_application_run("run-2")

    run = app_module.RUNS["run-2"]
    assert run["application"]["pending"] is True
    assert run["application"]["missing_fields"] == ["role_name"]
    assert not run_dir.exists()

    pending = db.list_pending_attempts(user_id)
    assert len(pending) == 1
    assert pending[0]["company_name"] == "Acme"
    assert pending[0]["role_name"] is None


def test_finalize_creates_pending_when_metadata_missing_entirely(_isolate, tmp_path):
    user_id = _isolate
    _seed_run("run-3", tmp_path / "runs" / "run-3", user_id, metadata=None)
    app_module._finalize_application_run("run-3")
    run = app_module.RUNS["run-3"]
    assert run["application"]["pending"] is True
    assert set(run["application"]["missing_fields"]) == {"company_name", "role_name"}


def test_finalize_second_application_to_same_role_creates_new_attempt(_isolate, tmp_path):
    user_id = _isolate
    _seed_run("run-4a", tmp_path / "runs" / "run-4a", user_id, {"company_name": "Acme", "role_name": "Engineer"})
    app_module._finalize_application_run("run-4a")
    _seed_run("run-4b", tmp_path / "runs" / "run-4b", user_id, {"company_name": "Acme", "role_name": "Engineer"})
    app_module._finalize_application_run("run-4b")

    tree = db.list_applications_tree(user_id)
    assert len(tree) == 1
    assert len(tree[0]["roles"]) == 1
    assert len(tree[0]["roles"][0]["attempts"]) == 2


# -- /api/applications ----------------------------------------------------------

def test_list_applications_empty(_isolate):
    resp = client.get("/api/applications")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_applications_requires_login():
    client.cookies.clear()
    assert client.get("/api/applications").status_code == 401


def test_attempt_result_downloads_and_is_ownership_checked(_isolate, tmp_path):
    user_id = _isolate
    _seed_run("run-5", tmp_path / "runs" / "run-5", user_id, {"company_name": "Acme", "role_name": "Engineer"})
    app_module._finalize_application_run("run-5")
    attempt_id = db.list_applications_tree(user_id)[0]["roles"][0]["attempts"][0]["id"]

    resp = client.get(f"/api/applications/attempts/{attempt_id}/result")
    assert resp.status_code == 200
    assert "tailored_cv.pdf" in resp.headers["content-disposition"]

    # A different user must not be able to fetch it.
    client.cookies.clear()
    client.post("/api/auth/register", json={"email": "someoneelse@example.com", "password": "testpassword123"})
    resp = client.get(f"/api/applications/attempts/{attempt_id}/result")
    assert resp.status_code == 404


# -- /api/applications/pending ---------------------------------------------------

def test_pending_list_and_resolve(_isolate, tmp_path):
    user_id = _isolate
    _seed_run("run-6", tmp_path / "runs" / "run-6", user_id, {"company_name": "", "role_name": ""})
    app_module._finalize_application_run("run-6")

    pending_list = client.get("/api/applications/pending").json()
    assert len(pending_list) == 1
    pending_id = pending_list[0]["id"]
    assert set(pending_list[0]["missing_fields"]) == {"company_name", "role_name"}

    resp = client.patch(f"/api/applications/pending/{pending_id}", json={"company_name": "Acme", "role_name": "Engineer"})
    assert resp.status_code == 200

    assert client.get("/api/applications/pending").json() == []
    tree = client.get("/api/applications").json()
    assert tree[0]["company_name"] == "Acme"
    assert tree[0]["roles"][0]["role_name"] == "Engineer"


def test_resolve_pending_rejects_blank_fields(_isolate, tmp_path):
    user_id = _isolate
    _seed_run("run-7", tmp_path / "runs" / "run-7", user_id, {"company_name": "", "role_name": ""})
    app_module._finalize_application_run("run-7")
    pending_id = client.get("/api/applications/pending").json()[0]["id"]

    resp = client.patch(f"/api/applications/pending/{pending_id}", json={"company_name": "", "role_name": "Engineer"})
    assert resp.status_code == 400
