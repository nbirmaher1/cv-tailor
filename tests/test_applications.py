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

def _seed_run(run_id, run_dir, user_id, metadata=None, output_format="pdf", job_url=None, job_text=None):
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
        "job_url": job_url,
        "job_text": job_text,
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


# -- stage/CRM column migration -------------------------------------------------------

def test_stage_migration_backfills_legacy_applied_status(tmp_path, monkeypatch):
    # Simulates a real pre-existing local install: an `applications` table from before
    # `stage` existed, with a genuine status='applied' row -- the exact situation the
    # user's own account was in when this migration shipped. Deliberately bypasses
    # db.init_db() to build that legacy shape directly, since a fresh init already has
    # every current column.
    legacy_db_path = tmp_path / "legacy.db"
    monkeypatch.setattr(db, "DB_PATH", legacy_db_path)
    with db._conn() as conn:
        conn.executescript("""
            CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, password_hash TEXT);
            CREATE TABLE applications (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL,
              company_name TEXT NOT NULL, company_slug TEXT NOT NULL,
              role_name TEXT NOT NULL, role_slug TEXT NOT NULL,
              status TEXT,
              created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
        """)
        conn.execute("INSERT INTO users (id, email, password_hash) VALUES (1, 'a@b.com', 'x')")
        conn.execute(
            "INSERT INTO applications (user_id, company_name, company_slug, role_name, role_slug, status) "
            "VALUES (1, 'Acme', 'acme', 'Engineer', 'engineer', 'applied')"
        )
        conn.execute(
            "INSERT INTO applications (user_id, company_name, company_slug, role_name, role_slug, status) "
            "VALUES (1, 'Beta', 'beta', 'Analyst', 'analyst', NULL)"
        )

    db._migrate_applications_columns()

    with db._conn() as conn:
        rows = {r["company_slug"]: r["stage"] for r in conn.execute("SELECT company_slug, stage FROM applications")}
    assert rows == {"acme": "applied", "beta": "tailored"}


def test_stage_migration_is_idempotent(tmp_path, monkeypatch):
    legacy_db_path = tmp_path / "legacy2.db"
    monkeypatch.setattr(db, "DB_PATH", legacy_db_path)
    with db._conn() as conn:
        conn.executescript("""
            CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, password_hash TEXT);
            CREATE TABLE applications (
              id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
              company_name TEXT NOT NULL, company_slug TEXT NOT NULL,
              role_name TEXT NOT NULL, role_slug TEXT NOT NULL, status TEXT,
              created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
        """)
        conn.execute(
            "INSERT INTO applications (user_id, company_name, company_slug, role_name, role_slug, status) "
            "VALUES (1, 'Acme', 'acme', 'Engineer', 'engineer', 'applied')"
        )

    db._migrate_applications_columns()
    # A user manually moves it further along after the migration -- re-running the
    # migration (e.g. on the next app restart) must not regress that.
    with db._conn() as conn:
        conn.execute("UPDATE applications SET stage = 'interviewing' WHERE company_slug = 'acme'")

    db._migrate_applications_columns()

    with db._conn() as conn:
        stage = conn.execute("SELECT stage FROM applications WHERE company_slug = 'acme'").fetchone()["stage"]
    assert stage == "interviewing"


# -- stage/CRM db functions ------------------------------------------------------------

def _make_application_with_attempt(user_id):
    application = db.find_or_create_application(user_id, "Acme Corp", "acme-corp", "Engineer", "engineer")
    attempt = db.create_application_attempt(
        application["id"], "pdf", "tailored_cv", f"users/{user_id}/applications/acme-corp/engineer/2026-01-01", False,
        "2026-01-01T00:00:00.000000Z",
    )
    return application["id"], attempt["id"]


def test_mark_applied_promotes_stage_from_tailored(_isolate):
    user_id = _isolate
    application_id, attempt_id = _make_application_with_attempt(user_id)
    assert db.find_application_by_id(application_id, user_id)["stage"] == "tailored"

    db.mark_application_applied(application_id, attempt_id)

    row = db.find_application_by_id(application_id, user_id)
    assert row["stage"] == "applied"
    activities = db.list_application_activities(application_id)
    assert len(activities) == 1
    assert activities[0]["activity_type"] == "stage_change"


def test_mark_applied_does_not_regress_an_already_advanced_stage(_isolate):
    user_id = _isolate
    application_id, attempt_id = _make_application_with_attempt(user_id)
    db.mark_application_applied(application_id, attempt_id)
    db.update_application_stage(application_id, "interviewing")

    # Re-picking which attempt counts as "the applied one" must not regress the stage.
    db.mark_application_applied(application_id, attempt_id)

    assert db.find_application_by_id(application_id, user_id)["stage"] == "interviewing"


def test_unmark_applied_resets_stage_to_tailored(_isolate):
    user_id = _isolate
    application_id, attempt_id = _make_application_with_attempt(user_id)
    db.mark_application_applied(application_id, attempt_id)
    db.update_application_stage(application_id, "offer")

    db.unmark_application_applied(application_id)

    row = db.find_application_by_id(application_id, user_id)
    assert row["stage"] == "tailored"
    assert row["applied_attempt_id"] is None
    assert row["applied_at"] is None


def test_update_application_stage_logs_activity_with_custom_description(_isolate):
    user_id = _isolate
    application_id, attempt_id = _make_application_with_attempt(user_id)
    db.mark_application_applied(application_id, attempt_id)

    db.update_application_stage(application_id, "screening", description="Phone screen scheduled for Friday")

    activities = db.list_application_activities(application_id)
    assert activities[0]["description"] == "Phone screen scheduled for Friday"
    assert db.find_application_by_id(application_id, user_id)["stage"] == "screening"


def test_list_application_activities_ordered_most_recent_first(_isolate):
    user_id = _isolate
    application_id, attempt_id = _make_application_with_attempt(user_id)
    db.mark_application_applied(application_id, attempt_id)
    db.update_application_stage(application_id, "screening")
    db.update_application_stage(application_id, "interviewing")

    activities = db.list_application_activities(application_id)

    assert [a["description"] for a in activities] == [
        "Moved to interviewing", "Moved to screening", "Moved to applied",
    ]


def test_update_application_details_partial_update_only_touches_given_fields(_isolate):
    user_id = _isolate
    application_id, _ = _make_application_with_attempt(user_id)

    db.update_application_details(application_id, salary_min=90000, salary_max=110000, location="Remote")
    row = db.find_application_by_id(application_id, user_id)
    assert row["salary_min"] == 90000
    assert row["salary_max"] == 110000
    assert row["location"] == "Remote"
    assert row["notes"] is None

    db.update_application_details(application_id, notes="Great culture fit")
    row = db.find_application_by_id(application_id, user_id)
    assert row["notes"] == "Great culture fit"
    assert row["salary_min"] == 90000  # untouched by the second call


def test_update_application_details_ignores_unknown_fields(_isolate):
    user_id = _isolate
    application_id, _ = _make_application_with_attempt(user_id)

    # Must not raise or write to an arbitrary column just because a caller passed one.
    db.update_application_details(application_id, stage="hacked", id=999)

    row = db.find_application_by_id(application_id, user_id)
    assert row["stage"] == "tailored"
    assert row["id"] == application_id


def test_set_application_job_details_persists_job_text(_isolate):
    user_id = _isolate
    application_id, _ = _make_application_with_attempt(user_id)

    db.set_application_job_details(application_id, None, "We are hiring a backend engineer...")

    row = db.find_application_by_id(application_id, user_id)
    assert row["job_url"] is None
    assert row["job_description_raw"] == "We are hiring a backend engineer..."


def test_list_applications_tree_includes_new_crm_fields(_isolate):
    user_id = _isolate
    application_id, _ = _make_application_with_attempt(user_id)
    db.update_application_details(application_id, location="Remote", work_model="remote")

    tree = db.list_applications_tree(user_id)

    role = tree[0]["roles"][0]
    assert role["stage"] == "tailored"
    assert role["location"] == "Remote"
    assert role["work_model"] == "remote"


# -- PATCH .../stage, PATCH .../details, GET .../activities ---------------------------

def test_stage_endpoint_requires_applied_first(_isolate):
    user_id = _isolate
    application_id, _ = _make_application_with_attempt(user_id)

    resp = client.patch(f"/api/applications/{application_id}/stage", json={"stage": "screening"})

    assert resp.status_code == 400


def test_stage_endpoint_rejects_invalid_stage(_isolate):
    user_id = _isolate
    application_id, attempt_id = _make_application_with_attempt(user_id)
    db.mark_application_applied(application_id, attempt_id)

    resp = client.patch(f"/api/applications/{application_id}/stage", json={"stage": "tailored"})

    assert resp.status_code == 400


def test_stage_endpoint_updates_stage_and_logs_activity(_isolate):
    user_id = _isolate
    application_id, attempt_id = _make_application_with_attempt(user_id)
    db.mark_application_applied(application_id, attempt_id)

    resp = client.patch(f"/api/applications/{application_id}/stage", json={"stage": "interviewing", "note": "Round 2 next week"})

    assert resp.status_code == 200
    assert db.find_application_by_id(application_id, user_id)["stage"] == "interviewing"
    activities_resp = client.get(f"/api/applications/{application_id}/activities")
    assert activities_resp.status_code == 200
    assert activities_resp.json()[0]["description"] == "Round 2 next week"


def test_stage_endpoint_is_ownership_checked():
    resp = client.patch("/api/applications/999999/stage", json={"stage": "applied"})
    assert resp.status_code == 404


def test_details_endpoint_updates_and_returns_ok(_isolate):
    user_id = _isolate
    application_id, _ = _make_application_with_attempt(user_id)

    resp = client.patch(f"/api/applications/{application_id}/details", json={"location": "Berlin", "salary_min": 80000})

    assert resp.status_code == 200
    row = db.find_application_by_id(application_id, user_id)
    assert row["location"] == "Berlin"
    assert row["salary_min"] == 80000


def test_details_endpoint_is_ownership_checked():
    resp = client.patch("/api/applications/999999/details", json={"location": "Berlin"})
    assert resp.status_code == 404


def test_activities_endpoint_empty_before_any_stage_change(_isolate):
    user_id = _isolate
    application_id, _ = _make_application_with_attempt(user_id)

    resp = client.get(f"/api/applications/{application_id}/activities")

    assert resp.status_code == 200
    assert resp.json() == []


# -- job_url/job_description_raw persistence -------------------------------------------

def test_finalize_persists_job_text_no_url(_isolate, tmp_path):
    user_id = _isolate
    run_dir = _seed_run(
        "run-jd1", tmp_path / "runs" / "run-jd1", user_id,
        {"company_name": "Acme", "role_name": "Engineer"},
        job_text="We are hiring a backend engineer to own our payments API.",
    )
    app_module._finalize_application_run("run-jd1")

    tree = client.get("/api/applications").json()
    role = tree[0]["roles"][0]
    assert role["job_url"] is None
    assert role["job_description_raw"] == "We are hiring a backend engineer to own our payments API."


def test_finalize_persists_job_url(_isolate, tmp_path):
    user_id = _isolate
    _seed_run(
        "run-jd2", tmp_path / "runs" / "run-jd2", user_id,
        {"company_name": "Acme", "role_name": "Engineer"},
        job_url="https://example.com/jobs/123",
    )
    app_module._finalize_application_run("run-jd2")

    tree = client.get("/api/applications").json()
    role = tree[0]["roles"][0]
    assert role["job_url"] == "https://example.com/jobs/123"
    assert role["job_description_raw"] is None


def test_finalize_keeps_most_recent_job_text_on_re_tailor(_isolate, tmp_path):
    user_id = _isolate
    _seed_run(
        "run-jd3a", tmp_path / "runs" / "run-jd3a", user_id,
        {"company_name": "Acme", "role_name": "Engineer"}, job_text="First posting version.",
    )
    app_module._finalize_application_run("run-jd3a")
    _seed_run(
        "run-jd3b", tmp_path / "runs" / "run-jd3b", user_id,
        {"company_name": "Acme", "role_name": "Engineer"}, job_text="Updated posting version.",
    )
    app_module._finalize_application_run("run-jd3b")

    tree = client.get("/api/applications").json()
    role = tree[0]["roles"][0]
    assert role["job_description_raw"] == "Updated posting version."
