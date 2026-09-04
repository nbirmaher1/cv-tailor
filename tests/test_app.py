import json
import time

import pytest
from fastapi.testclient import TestClient

import app as app_module
import db
import routes_master_cv
from app import _classify_event, _read_rationale, _sanitize_filename, _sweep_stale_runs

client = TestClient(app_module.app)

CURRENT_USER_ID = None

SAMPLE_MASTER_CV = {
    "full_name": "Jane Doe", "target_title": "Data Analyst", "email": "jane@example.com",
    "phone": "", "location": "Berlin, Germany", "links": "", "work_authorization": "",
    "photo_path": None, "summary": "Experienced analyst.",
    "experience": [], "education": [], "skills": [], "languages": [], "extra_sections": [],
}


@pytest.fixture(autouse=True)
def _isolate_runs_dir(tmp_path, monkeypatch):
    # Redirect the app's run-folder storage into a temp dir so tests never
    # write into the project's real runs/ directory.
    monkeypatch.setattr(app_module, "RUNS_DIR", tmp_path)


@pytest.fixture(autouse=True)
def _clear_runs():
    # RUNS is a module-global keyed by run_id; the isolated per-test DB restarts user ids at
    # 1, so leftover entries from a prior test would otherwise leak into list endpoints
    # (/runs, /resumable) for the current test's user. Clear it around every test.
    app_module.RUNS.clear()
    yield
    app_module.RUNS.clear()


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    # Redirect the account/session DB into a fresh temp file per test so tests
    # never touch the project's real data/cvtailor.db.
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    monkeypatch.setattr(routes_master_cv, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path / "data")


def _seed_master_cv(user_id):
    cv_dir = routes_master_cv.master_cv_dir(user_id)
    cv_dir.mkdir(parents=True, exist_ok=True)
    (cv_dir / "content.json").write_text(json.dumps(SAMPLE_MASTER_CV))
    db.touch_master_cv(user_id)


@pytest.fixture(autouse=True)
def _logged_in_user(_isolate_db):
    # Every /api/tailor/* route requires a logged-in user with a saved master
    # CV; register+log in a fresh test account per test (letting the
    # TestClient's cookie jar carry the session across requests) and seed a
    # master CV by default, since almost every test here exercises /start.
    global CURRENT_USER_ID
    resp = client.post(
        "/api/auth/register",
        json={"email": "test-user@example.com", "password": "testpassword123"},
    )
    assert resp.status_code == 200
    CURRENT_USER_ID = resp.json()["user"]["id"]
    _seed_master_cv(CURRENT_USER_ID)
    yield CURRENT_USER_ID
    client.cookies.clear()
    CURRENT_USER_ID = None


# -- _sanitize_filename --------------------------------------------------

def test_sanitize_filename_blank_defaults_to_tailored_cv():
    assert _sanitize_filename("") == "tailored_cv"
    assert _sanitize_filename("   ") == "tailored_cv"


def test_sanitize_filename_strips_unsafe_characters():
    assert _sanitize_filename('my/cv:name*?"<>|') == "mycvname"


def test_sanitize_filename_truncates_to_100_chars():
    assert len(_sanitize_filename("a" * 500)) == 100


def test_sanitize_filename_keeps_safe_name_untouched():
    assert _sanitize_filename("Jane_Doe_Resume") == "Jane_Doe_Resume"


# -- _classify_event ------------------------------------------------------

def test_classify_event_maps_first_read_only():
    counts = {"Read": 1, "WebFetch": 0, "Agent": 0, "Write": 0, "Bash": 0}
    assert _classify_event("Read", counts) == ("Reading your CV…", 12)
    counts["Read"] = 2
    assert _classify_event("Read", counts) is None


def test_classify_event_maps_agent_review_rounds():
    counts = {"Read": 0, "WebFetch": 0, "Agent": 1, "Write": 0, "Bash": 0}
    assert _classify_event("Agent", counts)[0] == "Running an independent review pass…"
    counts["Agent"] = 2
    assert _classify_event("Agent", counts)[0] == "Double-checking the revised draft…"


def test_classify_event_unknown_tool_returns_none():
    counts = {"Read": 0, "WebFetch": 0, "Agent": 0, "Write": 0, "Bash": 0}
    assert _classify_event("SomeOtherTool", counts) is None


def test_classify_event_maps_web_search():
    counts = {"Read": 0, "WebFetch": 0, "WebSearch": 1, "Agent": 0, "Write": 0, "Bash": 0}
    assert _classify_event("WebSearch", counts) == ("Researching the company…", 30)


# -- /api/tailor/start ------------------------------------------------------

def test_start_requires_job_url_or_text():
    response = client.post("/api/tailor/start", data={"output_format": "pdf"})
    assert response.status_code == 400
    assert "job posting" in response.json()["detail"].lower()


def test_start_rejects_invalid_output_format():
    response = client.post(
        "/api/tailor/start",
        data={"job_text": "We need a data analyst.", "output_format": "epub"},
    )
    assert response.status_code == 400


def test_start_accepts_valid_request_and_returns_run_id():
    response = client.post(
        "/api/tailor/start",
        data={"job_text": "We need a data analyst.", "output_format": "pdf"},
    )
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    assert run_id in app_module.RUNS


def test_start_without_master_cv_returns_409():
    client.cookies.clear()
    client.post("/api/auth/register", json={"email": "no-master-cv@example.com", "password": "testpassword123"})
    response = client.post(
        "/api/tailor/start",
        data={"job_text": "We need a data analyst.", "output_format": "pdf"},
    )
    assert response.status_code == 409


# -- /api/tailor/{run_id}/status and /result --------------------------------

def test_status_unknown_run_id_returns_404():
    assert client.get("/api/tailor/does-not-exist/status").status_code == 404


def test_result_unknown_run_id_returns_404():
    assert client.get("/api/tailor/does-not-exist/result").status_code == 404


# -- request validation ------------------------------------------------------

def test_start_rejects_oversized_job_text():
    response = client.post(
        "/api/tailor/start",
        data={"job_text": "x" * (app_module.MAX_JOB_TEXT_CHARS + 1), "output_format": "pdf"},
    )
    assert response.status_code == 400
    assert "too long" in response.json()["detail"].lower()


def test_start_rejects_oversized_notes():
    response = client.post(
        "/api/tailor/start",
        data={
            "job_text": "We need a data analyst.",
            "notes": "x" * (app_module.MAX_NOTES_CHARS + 1),
            "output_format": "pdf",
        },
    )
    assert response.status_code == 400
    assert "too long" in response.json()["detail"].lower()


def test_start_rejects_oversized_cover_letter_notes():
    response = client.post(
        "/api/tailor/start",
        data={
            "job_text": "We need a data analyst.",
            "include_cover_letter": "true",
            "cover_letter_notes": "x" * (app_module.MAX_COVER_LETTER_NOTES_CHARS + 1),
            "output_format": "pdf",
        },
    )
    assert response.status_code == 400
    assert "too long" in response.json()["detail"].lower()


def test_start_rejects_invalid_cover_letter_template_extension():
    response = client.post(
        "/api/tailor/start",
        files={"cover_letter_template": ("old_letter.exe", b"not a document", "application/octet-stream")},
        data={"job_text": "We need a data analyst.", "include_cover_letter": "true", "output_format": "pdf"},
    )
    assert response.status_code == 400
    assert "template" in response.json()["detail"].lower()


def test_start_accepts_cover_letter_request(monkeypatch):
    monkeypatch.setattr(app_module, "_run_claude", lambda *a, **kw: None)
    response = client.post(
        "/api/tailor/start",
        data={
            "job_text": "We need a data analyst.",
            "include_cover_letter": "true",
            "cover_letter_notes": "Mention my passion for climate tech.",
            "output_format": "pdf",
        },
    )
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    assert run_id in app_module.RUNS
    assert app_module.RUNS[run_id]["cover_letter_file"].name == "cover_letter.pdf"


class _FakeThread:
    """Captures the target/args a threading.Thread would have run, without starting one."""

    last_args = None

    def __init__(self, target=None, args=(), daemon=None):
        _FakeThread.last_args = args

    def start(self):
        pass


def test_start_with_intelligent_cover_letter_grants_web_search(monkeypatch):
    monkeypatch.setattr(app_module.threading, "Thread", _FakeThread)
    response = client.post(
        "/api/tailor/start",
        data={
            "job_text": "We need a data analyst.",
            "include_cover_letter": "true",
            "intelligent_cover_letter": "true",
            "output_format": "pdf",
        },
    )
    assert response.status_code == 200
    allowed_tools = _FakeThread.last_args[4]
    assert "WebSearch" in allowed_tools
    app_module.RUNS.pop(response.json()["run_id"], None)


def test_start_intelligent_cover_letter_ignored_without_cover_letter(monkeypatch):
    monkeypatch.setattr(app_module.threading, "Thread", _FakeThread)
    response = client.post(
        "/api/tailor/start",
        data={
            "job_text": "We need a data analyst.",
            "include_cover_letter": "false",
            "intelligent_cover_letter": "true",
            "output_format": "pdf",
        },
    )
    assert response.status_code == 200
    allowed_tools = _FakeThread.last_args[4]
    assert "WebSearch" not in allowed_tools
    app_module.RUNS.pop(response.json()["run_id"], None)


def test_start_without_intelligent_cover_letter_omits_web_search(monkeypatch):
    monkeypatch.setattr(app_module.threading, "Thread", _FakeThread)
    response = client.post(
        "/api/tailor/start",
        data={"job_text": "We need a data analyst.", "output_format": "pdf"},
    )
    assert response.status_code == 200
    allowed_tools = _FakeThread.last_args[4]
    assert "WebSearch" not in allowed_tools
    app_module.RUNS.pop(response.json()["run_id"], None)


# -- _read_rationale ----------------------------------------------------------

def test_read_rationale_missing_file_returns_none(tmp_path):
    assert _read_rationale(tmp_path) is None


def test_read_rationale_parses_valid_summary(tmp_path):
    (tmp_path / "summary.json").write_text(json.dumps({
        "summary": "Led with ETL experience.",
        "changes": ["Reordered experience", "  ", "Cut retail bullet"],
        "review_note": "Fixed one overstated bullet.",
    }))
    result = _read_rationale(tmp_path)
    assert result["summary"] == "Led with ETL experience."
    assert result["changes"] == ["Reordered experience", "Cut retail bullet"]
    assert result["review_note"] == "Fixed one overstated bullet."


def test_read_rationale_malformed_json_returns_none(tmp_path):
    (tmp_path / "summary.json").write_text("not json")
    assert _read_rationale(tmp_path) is None


def test_read_rationale_parses_valid_requirements(tmp_path):
    (tmp_path / "summary.json").write_text(json.dumps({
        "summary": "s", "changes": [], "review_note": "",
        "requirements": [
            {"name": "SQL", "status": "matched", "evidence": "Built a dashboard in SQL."},
            {"name": "Tableau", "status": "listed_only"},
            {"name": "Kubernetes", "status": "missing"},
        ],
    }))
    result = _read_rationale(tmp_path)
    assert result["requirements"] == [
        {"name": "SQL", "status": "matched", "evidence": "Built a dashboard in SQL."},
        {"name": "Tableau", "status": "listed_only", "evidence": ""},
        {"name": "Kubernetes", "status": "missing", "evidence": ""},
    ]


def test_read_rationale_filters_malformed_requirements(tmp_path):
    (tmp_path / "summary.json").write_text(json.dumps({
        "summary": "s", "changes": [], "review_note": "",
        "requirements": [
            {"name": "SQL", "status": "matched", "evidence": "ok"},
            {"name": "", "status": "matched"},           # missing name
            {"name": "Bad", "status": "not-a-real-status"},  # invalid status
            "just a string",                              # not even a dict
            {"status": "missing"},                        # missing name key entirely
        ],
    }))
    result = _read_rationale(tmp_path)
    assert result["requirements"] == [{"name": "SQL", "status": "matched", "evidence": "ok"}]


def test_read_rationale_missing_requirements_key_defaults_to_empty_list(tmp_path):
    (tmp_path / "summary.json").write_text(json.dumps({"summary": "s", "changes": [], "review_note": ""}))
    result = _read_rationale(tmp_path)
    assert result["requirements"] == []


# -- _sweep_stale_runs --------------------------------------------------------

def test_sweep_stale_runs_removes_old_dirs_keeps_fresh(tmp_path, monkeypatch):
    import os

    monkeypatch.setattr(app_module, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(app_module, "RUN_RETENTION_SECONDS", 100)

    old_run = tmp_path / "old-run"
    old_run.mkdir()
    long_ago = time.time() - 1000
    os.utime(old_run, (long_ago, long_ago))
    app_module.RUNS["old-run"] = {"done": True}

    fresh_run = tmp_path / "fresh-run"
    fresh_run.mkdir()
    app_module.RUNS["fresh-run"] = {"done": False}

    _sweep_stale_runs()

    assert not old_run.exists()
    assert "old-run" not in app_module.RUNS
    assert fresh_run.exists()
    assert "fresh-run" in app_module.RUNS
    app_module.RUNS.pop("fresh-run", None)


# -- /api/tailor/{run_id}/preview --------------------------------------------

def _make_run(run_id, tmp_path, **overrides):
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    run = {
        "step": "Done!",
        "percent": 100,
        "done": True,
        "error": None,
        "output_file": run_dir / "output.pdf",
        "output_format": "pdf",
        "download_name": "tailored_cv",
        "rationale": None,
        "proc": None,
        "cancelled": False,
        "revising": False,
        "revision_count": 0,
        "cover_letter_file": run_dir / "cover_letter.pdf",
        "user_id": CURRENT_USER_ID,
    }
    run.update(overrides)
    app_module.RUNS[run_id] = run
    return run_dir


def test_preview_unknown_run_id_returns_404():
    assert client.get("/api/tailor/does-not-exist/preview").status_code == 404


def test_preview_not_done_returns_409(tmp_path):
    _make_run("run-a", tmp_path, done=False)
    assert client.get("/api/tailor/run-a/preview").status_code == 409
    app_module.RUNS.pop("run-a", None)


def test_preview_with_error_returns_422(tmp_path):
    _make_run("run-b", tmp_path, error="Cancelled.")
    assert client.get("/api/tailor/run-b/preview").status_code == 422
    app_module.RUNS.pop("run-b", None)


def test_preview_rejects_docx_format(tmp_path):
    _make_run("run-c", tmp_path, output_format="docx")
    response = client.get("/api/tailor/run-c/preview")
    assert response.status_code == 404
    assert "PDF" in response.json()["detail"]
    app_module.RUNS.pop("run-c", None)


def test_preview_serves_pdf_inline(tmp_path):
    run_dir = _make_run("run-d", tmp_path)
    (run_dir / "output.pdf").write_bytes(b"%PDF-1.4 fake")
    response = client.get("/api/tailor/run-d/preview")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "content-disposition" not in response.headers
    app_module.RUNS.pop("run-d", None)


# -- cover letter (status/preview/result) ------------------------------------

def test_status_has_cover_letter_false_when_none_generated(tmp_path):
    _make_run("run-s1", tmp_path)
    response = client.get("/api/tailor/run-s1/status")
    assert response.status_code == 200
    assert response.json()["has_cover_letter"] is False
    app_module.RUNS.pop("run-s1", None)


def test_status_has_cover_letter_true_when_file_exists(tmp_path):
    run_dir = _make_run("run-s2", tmp_path)
    (run_dir / "cover_letter.pdf").write_bytes(b"%PDF-1.4 fake")
    response = client.get("/api/tailor/run-s2/status")
    assert response.status_code == 200
    assert response.json()["has_cover_letter"] is True
    app_module.RUNS.pop("run-s2", None)


def test_cover_letter_preview_404_when_not_generated(tmp_path):
    _make_run("run-s3", tmp_path)
    response = client.get("/api/tailor/run-s3/cover-letter/preview")
    assert response.status_code == 404
    app_module.RUNS.pop("run-s3", None)


def test_cover_letter_preview_serves_pdf_inline(tmp_path):
    run_dir = _make_run("run-s4", tmp_path)
    (run_dir / "cover_letter.pdf").write_bytes(b"%PDF-1.4 fake")
    response = client.get("/api/tailor/run-s4/cover-letter/preview")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "content-disposition" not in response.headers
    app_module.RUNS.pop("run-s4", None)


def test_cover_letter_result_404_when_not_generated(tmp_path):
    _make_run("run-s5", tmp_path)
    response = client.get("/api/tailor/run-s5/cover-letter/result")
    assert response.status_code == 404
    app_module.RUNS.pop("run-s5", None)


def test_cover_letter_result_serves_with_suffixed_filename(tmp_path):
    run_dir = _make_run("run-s6", tmp_path)
    (run_dir / "cover_letter.pdf").write_bytes(b"%PDF-1.4 fake")
    response = client.get("/api/tailor/run-s6/cover-letter/result")
    assert response.status_code == 200
    assert "tailored_cv_cover_letter.pdf" in response.headers["content-disposition"]
    app_module.RUNS.pop("run-s6", None)


# -- /api/tailor/{run_id}/cancel ---------------------------------------------

def test_cancel_unknown_run_id_returns_404():
    assert client.post("/api/tailor/does-not-exist/cancel").status_code == 404


def test_cancel_already_done_run_is_a_no_op(tmp_path):
    _make_run("run-e", tmp_path, done=True)
    response = client.post("/api/tailor/run-e/cancel")
    assert response.status_code == 200
    assert response.json() == {"cancelled": False}
    app_module.RUNS.pop("run-e", None)


def test_cancel_in_progress_run_marks_done_and_deletes_dir(tmp_path):
    run_dir = _make_run("run-f", tmp_path, done=False, error=None)
    killed = {"called": False}

    class FakeProc:
        def kill(self):
            killed["called"] = True

    app_module.RUNS["run-f"]["proc"] = FakeProc()

    response = client.post("/api/tailor/run-f/cancel")
    assert response.status_code == 200
    assert response.json() == {"cancelled": True}
    assert killed["called"] is True
    assert not run_dir.exists()
    assert app_module.RUNS["run-f"]["done"] is True
    assert app_module.RUNS["run-f"]["error"] == "Cancelled."
    assert app_module.RUNS["run-f"]["cancelled"] is True
    app_module.RUNS.pop("run-f", None)


def test_cancel_during_revision_keeps_dir_and_clears_error(tmp_path):
    run_dir = _make_run("run-j", tmp_path, done=False, error=None, revising=True, revision_count=1)
    (run_dir / "content.json").write_text("{}")
    (run_dir / "output.pdf").write_bytes(b"%PDF-1.4 fake")
    killed = {"called": False}

    class FakeProc:
        def kill(self):
            killed["called"] = True

    app_module.RUNS["run-j"]["proc"] = FakeProc()

    response = client.post("/api/tailor/run-j/cancel")
    assert response.status_code == 200
    assert response.json() == {"cancelled": True}
    assert killed["called"] is True
    # Unlike a cancelled fresh run, a cancelled revision keeps the previous,
    # still-valid result on disk and leaves the run usable (no persistent error).
    assert run_dir.exists()
    assert (run_dir / "output.pdf").exists()
    run = app_module.RUNS["run-j"]
    assert run["done"] is True
    assert run["error"] is None
    assert run["cancelled"] is True
    assert run["revising"] is False
    app_module.RUNS.pop("run-j", None)


# -- /api/tailor/{run_id}/revise ---------------------------------------------

def test_revise_unknown_run_id_returns_404():
    response = client.post("/api/tailor/does-not-exist/revise", data={"feedback": "Shorten it."})
    assert response.status_code == 404


def test_revise_empty_feedback_returns_400(tmp_path):
    _make_run("run-k", tmp_path)
    response = client.post("/api/tailor/run-k/revise", data={"feedback": "   "})
    assert response.status_code == 400
    app_module.RUNS.pop("run-k", None)


def test_revise_oversized_feedback_returns_400(tmp_path):
    _make_run("run-l", tmp_path)
    response = client.post(
        "/api/tailor/run-l/revise",
        data={"feedback": "x" * (app_module.MAX_FEEDBACK_CHARS + 1)},
    )
    assert response.status_code == 400
    assert "too long" in response.json()["detail"].lower()
    app_module.RUNS.pop("run-l", None)


def test_revise_not_done_returns_409(tmp_path):
    _make_run("run-m", tmp_path, done=False)
    response = client.post("/api/tailor/run-m/revise", data={"feedback": "Shorten it."})
    assert response.status_code == 409
    app_module.RUNS.pop("run-m", None)


def test_revise_with_existing_error_returns_409(tmp_path):
    _make_run("run-n", tmp_path, error="Cancelled.")
    response = client.post("/api/tailor/run-n/revise", data={"feedback": "Shorten it."})
    assert response.status_code == 409
    app_module.RUNS.pop("run-n", None)


def test_revise_at_max_revisions_returns_400(tmp_path):
    _make_run("run-o", tmp_path, revision_count=app_module.MAX_REVISIONS)
    response = client.post("/api/tailor/run-o/revise", data={"feedback": "Shorten it."})
    assert response.status_code == 400
    assert "used all" in response.json()["detail"].lower()
    app_module.RUNS.pop("run-o", None)


def test_revise_missing_content_json_returns_409(tmp_path):
    run_dir = _make_run("run-p", tmp_path)
    (run_dir / "output.pdf").write_bytes(b"%PDF-1.4 fake")
    response = client.post("/api/tailor/run-p/revise", data={"feedback": "Shorten it."})
    assert response.status_code == 409
    app_module.RUNS.pop("run-p", None)


def test_revise_starts_background_and_updates_run_state(tmp_path, monkeypatch):
    run_dir = _make_run("run-q", tmp_path)
    (run_dir / "content.json").write_text('{"summary": "x"}')
    (run_dir / "output.pdf").write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(app_module, "_run_revision", lambda *a, **kw: None)

    response = client.post("/api/tailor/run-q/revise", data={"feedback": "Shorten the summary."})
    assert response.status_code == 200
    assert response.json() == {"run_id": "run-q"}
    run = app_module.RUNS["run-q"]
    assert run["revising"] is True
    assert run["revision_count"] == 1
    assert run["done"] is False
    assert run["error"] is None
    app_module.RUNS.pop("run-q", None)


def test_revise_clears_stale_error_file(tmp_path, monkeypatch):
    run_dir = _make_run("run-r", tmp_path)
    (run_dir / "content.json").write_text("{}")
    (run_dir / "output.pdf").write_bytes(b"%PDF-1.4 fake")
    (run_dir / "error.txt").write_text("stale error from a previous failed revision")
    monkeypatch.setattr(app_module, "_run_revision", lambda *a, **kw: None)

    response = client.post("/api/tailor/run-r/revise", data={"feedback": "Tweak something."})
    assert response.status_code == 200
    assert not (run_dir / "error.txt").exists()
    app_module.RUNS.pop("run-r", None)


# -- _render_cover_letter_output resilience --------------------------------------------

def test_render_cover_letter_output_skips_when_content_file_missing(tmp_path, monkeypatch):
    # A cover letter was requested (cl_output_file set) but its content record was never
    # written -- e.g. a run interrupted partway. Must skip gracefully, not crash.
    called = {"pdf": 0, "docx": 0}
    monkeypatch.setattr(app_module.render_pdf, "render", lambda *a: called.__setitem__("pdf", called["pdf"] + 1))
    monkeypatch.setattr(app_module.render_cover_letter_docx, "build", lambda *a: called.__setitem__("docx", called["docx"] + 1))

    paths = {
        "output_format": "pdf",
        "cl_content_file": tmp_path / "cover_letter_content.json",  # deliberately not created
        "cl_html_file": tmp_path / "cover_letter.html",
        "cl_output_file": tmp_path / "cover_letter.pdf",
    }
    # Should not raise, and should not attempt any render.
    app_module._render_cover_letter_output(paths)
    assert called == {"pdf": 0, "docx": 0}


def test_render_cover_letter_output_renders_when_content_file_present(tmp_path, monkeypatch):
    rendered = {"pdf": 0}
    monkeypatch.setattr(app_module.render_pdf, "render", lambda *a: rendered.__setitem__("pdf", rendered["pdf"] + 1))
    monkeypatch.setattr(app_module.fill_html, "fill_cover_letter_html", lambda content: "<html></html>")

    cl_content_file = tmp_path / "cover_letter_content.json"
    cl_content_file.write_text(json.dumps({"paragraphs": ["Hello."]}))
    paths = {
        "output_format": "pdf",
        "cl_content_file": cl_content_file,
        "cl_html_file": tmp_path / "cover_letter.html",
        "cl_output_file": tmp_path / "cover_letter.pdf",
    }
    app_module._render_cover_letter_output(paths)
    assert rendered["pdf"] == 1


# -- voice guard (em dash + banned-phrase deterministic check) -------------------------
#
# The deterministic backup for cv-standards.md / cover-letter-standards.md's Voice
# sections: a free scan for an em dash or a banned phrase, with capped Claude fix rounds
# if something's found, and a hard code-level fallback for em dash specifically (never for
# banned phrases -- those aren't safely auto-fixable).

def test_find_voice_issues_detects_em_dash_but_not_en_dash_or_hyphen():
    content = {
        "summary": "Data-driven analyst, 2021–2023 tenure.",  # hyphen + en dash: must NOT trigger
        "experience": [{"bullets": ["Built a pipeline — end to end."]}],  # em dash: must trigger
    }
    result = app_module._find_voice_issues(content)
    assert result["em_dash"] is True
    assert result["phrases"] == []


def test_find_voice_issues_ignores_clean_date_ranges_and_hyphenated_words():
    content = {"summary": "Self-directed engineer, 2021 – Present, data-driven approach."}
    result = app_module._find_voice_issues(content)
    assert result["em_dash"] is False
    assert result["phrases"] == []


def test_find_voice_issues_finds_banned_phrases_case_insensitively():
    content = {"summary": "A RESULTS-DRIVEN professional with a proven track record."}
    result = app_module._find_voice_issues(content)
    assert result["em_dash"] is False
    assert "results-driven" in result["phrases"]
    assert "proven track record" in result["phrases"]


def test_find_voice_issues_does_not_flag_tier_2_situational_words():
    content = {"experience": [{"bullets": ["Built a robust ETL pipeline with dynamic scaling."]}]}
    result = app_module._find_voice_issues(content)
    assert result["em_dash"] is False
    assert result["phrases"] == []


def test_find_voice_issues_walks_nested_lists_and_dicts():
    content = {"skills": [{"category": "Tools", "items": ["Uses a seamless workflow"]}]}
    result = app_module._find_voice_issues(content)
    assert "seamless" in result["phrases"]


def test_strip_em_dash_in_place_replaces_character_and_preserves_rest(tmp_path):
    content_file = tmp_path / "content.json"
    content_file.write_text(json.dumps({"summary": "Built X — then scaled it.", "full_name": "Jane Doe"}))

    app_module._strip_em_dash_in_place(content_file)

    data = json.loads(content_file.read_text())
    assert "—" not in data["summary"]
    assert data["summary"] == "Built X, then scaled it."
    assert data["full_name"] == "Jane Doe"


def _make_voice_guard_paths(run_dir, cl=False):
    content_file = run_dir / "content.json"
    content_file.write_text(json.dumps({"summary": "Clean summary."}))
    cl_content_file = run_dir / "cover_letter_content.json"
    if cl:
        cl_content_file.write_text(json.dumps({"paragraphs": ["Clean paragraph."]}))
    return {
        "content_file": content_file,
        "cl_content_file": cl_content_file if cl else None,
    }


def test_voice_guard_returns_true_immediately_when_clean_no_claude_call(tmp_path, monkeypatch):
    app_module.RUNS["vg-1"] = {"percent": 50, "step": "", "done": False, "error": None, "cancelled": False}
    paths = _make_voice_guard_paths(tmp_path)

    def fail_if_called(*a, **kw):
        raise AssertionError("no Claude call should happen when content is already clean")

    monkeypatch.setattr(app_module.claude_runner, "run_single_call", fail_if_called)

    assert app_module._run_voice_guard("vg-1", tmp_path, paths) is True
    app_module.RUNS.pop("vg-1", None)


def test_voice_guard_fixes_em_dash_on_first_round(tmp_path, monkeypatch):
    app_module.RUNS["vg-2"] = {"percent": 50, "step": "", "done": False, "error": None, "cancelled": False}
    paths = _make_voice_guard_paths(tmp_path)
    paths["content_file"].write_text(json.dumps({"summary": "Built X — then shipped it."}))

    calls = {"n": 0}

    def fake_run_single_call(job_id, cmd, project_root, run_dir, on_event, jobs, timeout_seconds=None, fail_messages=None):
        calls["n"] += 1
        # Simulate Claude fixing it in place.
        paths["content_file"].write_text(json.dumps({"summary": "Built X, then shipped it."}))
        return True, ""

    monkeypatch.setattr(app_module.claude_runner, "run_single_call", fake_run_single_call)

    result = app_module._run_voice_guard("vg-2", tmp_path, paths)

    assert result is True
    assert calls["n"] == 1
    assert "—" not in paths["content_file"].read_text()
    app_module.RUNS.pop("vg-2", None)


def test_voice_guard_em_dash_falls_back_to_deterministic_strip_at_cap(tmp_path, monkeypatch):
    app_module.RUNS["vg-3"] = {"percent": 50, "step": "", "done": False, "error": None, "cancelled": False}
    paths = _make_voice_guard_paths(tmp_path)
    paths["content_file"].write_text(json.dumps({"summary": "Built X — then shipped it."}))

    def fake_run_single_call(job_id, cmd, project_root, run_dir, on_event, jobs, timeout_seconds=None, fail_messages=None):
        # Claude never actually fixes it -- em dash survives every round.
        return True, ""

    monkeypatch.setattr(app_module.claude_runner, "run_single_call", fake_run_single_call)

    result = app_module._run_voice_guard("vg-3", tmp_path, paths)

    assert result is True
    data = json.loads(paths["content_file"].read_text())
    assert "—" not in data["summary"]
    assert data["summary"] == "Built X, then shipped it."
    app_module.RUNS.pop("vg-3", None)


def test_voice_guard_lingering_banned_phrase_ships_as_is_without_corrupting(tmp_path, monkeypatch, capsys):
    app_module.RUNS["vg-4"] = {"percent": 50, "step": "", "done": False, "error": None, "cancelled": False}
    paths = _make_voice_guard_paths(tmp_path)
    original = {"summary": "A results-driven analyst with real experience."}
    paths["content_file"].write_text(json.dumps(original))

    def fake_run_single_call(job_id, cmd, project_root, run_dir, on_event, jobs, timeout_seconds=None, fail_messages=None):
        # Claude never actually fixes it -- the phrase survives every round.
        return True, ""

    monkeypatch.setattr(app_module.claude_runner, "run_single_call", fake_run_single_call)

    result = app_module._run_voice_guard("vg-4", tmp_path, paths)

    assert result is True
    # Content is untouched -- no unsafe auto-strip of a banned phrase.
    assert json.loads(paths["content_file"].read_text()) == original
    assert "results-driven" in capsys.readouterr().err
    app_module.RUNS.pop("vg-4", None)


def test_voice_guard_returns_false_when_fix_call_fails(tmp_path, monkeypatch):
    app_module.RUNS["vg-5"] = {"percent": 50, "step": "", "done": False, "error": None, "cancelled": False}
    paths = _make_voice_guard_paths(tmp_path)
    paths["content_file"].write_text(json.dumps({"summary": "Built X — then shipped it."}))

    def fake_run_single_call(job_id, cmd, project_root, run_dir, on_event, jobs, timeout_seconds=None, fail_messages=None):
        jobs[job_id].update(done=True, error="Something went wrong on our end. Please try again.")
        return False, ""

    monkeypatch.setattr(app_module.claude_runner, "run_single_call", fake_run_single_call)

    result = app_module._run_voice_guard("vg-5", tmp_path, paths)

    assert result is False
    assert app_module.RUNS["vg-5"]["done"] is True
    app_module.RUNS.pop("vg-5", None)


def test_voice_guard_checks_cover_letter_content_too(tmp_path, monkeypatch):
    app_module.RUNS["vg-6"] = {"percent": 50, "step": "", "done": False, "error": None, "cancelled": False}
    paths = _make_voice_guard_paths(tmp_path, cl=True)
    paths["cl_content_file"].write_text(json.dumps({"paragraphs": ["A seamless transition into this role."]}))

    def fake_run_single_call(job_id, cmd, project_root, run_dir, on_event, jobs, timeout_seconds=None, fail_messages=None):
        paths["cl_content_file"].write_text(json.dumps({"paragraphs": ["A smooth transition into this role."]}))
        return True, ""

    monkeypatch.setattr(app_module.claude_runner, "run_single_call", fake_run_single_call)

    result = app_module._run_voice_guard("vg-6", tmp_path, paths)

    assert result is True
    assert "seamless" not in paths["cl_content_file"].read_text().lower()
    app_module.RUNS.pop("vg-6", None)


# -- _run_render_qa_loop ---------------------------------------------------------------
#
# The backend-orchestrated render + visual-QA/cut loop that replaced Claude's own
# Bash-driven rendering (see A4). Renders are mocked out (no real Playwright/docx work);
# what's under test is the loop's control flow: does it stop on "pass", does it respect
# the round cap, does it re-render between rounds, does a QA-call failure propagate.

def _make_qa_paths(run_dir, output_format="pdf", target_pages=1):
    content_file = run_dir / "content.json"
    content_file.write_text(json.dumps({"summary": "x", "experience": [], "skills": [], "extra_sections": []}))
    html_file = run_dir / "output.html" if output_format == "pdf" else None
    if html_file is not None:
        html_file.write_text("<html></html>")
    output_file = run_dir / f"output.{output_format}"
    summary_file = run_dir / "summary.json"
    summary_file.write_text(json.dumps({"summary": "s", "changes": [], "review_note": "", "target_pages": target_pages}))
    return {
        "output_file": output_file, "output_format": output_format, "content_file": content_file,
        "html_file": html_file, "summary_file": summary_file,
        "cl_content_file": None, "cl_html_file": None, "cl_output_file": None,
    }


def _mock_renders(monkeypatch, page_count=1):
    monkeypatch.setattr(app_module.render_pdf, "render", lambda html, pdf: page_count)
    monkeypatch.setattr(app_module.render_docx, "build", lambda content, path: None)


def test_qa_loop_passes_on_first_round_without_re_render(tmp_path, monkeypatch):
    app_module.RUNS["qa-1"] = {"percent": 0, "step": "", "done": False, "error": None, "cancelled": False}
    paths = _make_qa_paths(tmp_path)
    _mock_renders(monkeypatch)

    calls = {"n": 0}

    def fake_run_single_call(job_id, cmd, project_root, run_dir, on_event, jobs, timeout_seconds=None, fail_messages=None):
        calls["n"] += 1
        (tmp_path / "qa_result.json").write_text(json.dumps({"status": "pass"}))
        return True, ""

    monkeypatch.setattr(app_module.claude_runner, "run_single_call", fake_run_single_call)

    result = app_module._run_render_qa_loop("qa-1", tmp_path, paths)

    assert result is True
    assert calls["n"] == 1
    app_module.RUNS.pop("qa-1", None)


def test_qa_loop_cuts_once_then_passes(tmp_path, monkeypatch):
    app_module.RUNS["qa-2"] = {"percent": 0, "step": "", "done": False, "error": None, "cancelled": False}
    paths = _make_qa_paths(tmp_path)
    _mock_renders(monkeypatch)

    calls = {"n": 0}

    def fake_run_single_call(job_id, cmd, project_root, run_dir, on_event, jobs, timeout_seconds=None, fail_messages=None):
        calls["n"] += 1
        status = "revise" if calls["n"] == 1 else "pass"
        (tmp_path / "qa_result.json").write_text(json.dumps({"status": status}))
        return True, ""

    monkeypatch.setattr(app_module.claude_runner, "run_single_call", fake_run_single_call)

    result = app_module._run_render_qa_loop("qa-2", tmp_path, paths)

    assert result is True
    assert calls["n"] == 2
    summary = json.loads(paths["summary_file"].read_text())
    assert "Further condensed to fit the page budget." in summary["changes"]
    app_module.RUNS.pop("qa-2", None)


def test_qa_loop_stops_at_round_cap_even_if_still_over_budget(tmp_path, monkeypatch):
    app_module.RUNS["qa-3"] = {"percent": 0, "step": "", "done": False, "error": None, "cancelled": False}
    paths = _make_qa_paths(tmp_path)
    _mock_renders(monkeypatch)

    calls = {"n": 0}

    def fake_run_single_call(job_id, cmd, project_root, run_dir, on_event, jobs, timeout_seconds=None, fail_messages=None):
        calls["n"] += 1
        (tmp_path / "qa_result.json").write_text(json.dumps({"status": "revise"}))
        return True, ""

    monkeypatch.setattr(app_module.claude_runner, "run_single_call", fake_run_single_call)

    result = app_module._run_render_qa_loop("qa-3", tmp_path, paths)

    assert result is True  # the final round always ends the loop, whatever Claude wrote
    assert calls["n"] == app_module.MAX_QA_ROUNDS + 1  # initial check + capped extra rounds
    app_module.RUNS.pop("qa-3", None)


def test_qa_loop_returns_false_when_qa_call_fails(tmp_path, monkeypatch):
    app_module.RUNS["qa-4"] = {"percent": 0, "step": "", "done": False, "error": None, "cancelled": False}
    paths = _make_qa_paths(tmp_path)
    _mock_renders(monkeypatch)

    def fake_run_single_call(job_id, cmd, project_root, run_dir, on_event, jobs, timeout_seconds=None, fail_messages=None):
        # run_single_call already marks the job done+errored itself on a real failure.
        jobs[job_id].update(done=True, error="Something went wrong on our end. Please try again.")
        return False, ""

    monkeypatch.setattr(app_module.claude_runner, "run_single_call", fake_run_single_call)

    result = app_module._run_render_qa_loop("qa-4", tmp_path, paths)

    assert result is False
    assert app_module.RUNS["qa-4"]["done"] is True
    app_module.RUNS.pop("qa-4", None)


def test_qa_loop_docx_uses_stats_not_page_count(tmp_path, monkeypatch):
    app_module.RUNS["qa-5"] = {"percent": 0, "step": "", "done": False, "error": None, "cancelled": False}
    paths = _make_qa_paths(tmp_path, output_format="docx")
    _mock_renders(monkeypatch)
    monkeypatch.setattr(app_module.docx_stats, "compute_stats", lambda path: {"total_bullets": 42})

    def fake_run_single_call(job_id, cmd, project_root, run_dir, on_event, jobs, timeout_seconds=None, fail_messages=None):
        # The DOCX branch of _qa_prompt should surface the docx_stats numbers, not a
        # page count (there is no rendered PDF to point Claude at for a DOCX run).
        assert "total_bullets" in cmd[2]
        assert "page(s) at Read the rendered PDF" not in cmd[2]
        (tmp_path / "qa_result.json").write_text(json.dumps({"status": "pass"}))
        return True, ""

    monkeypatch.setattr(app_module.claude_runner, "run_single_call", fake_run_single_call)

    result = app_module._run_render_qa_loop("qa-5", tmp_path, paths)

    assert result is True
    app_module.RUNS.pop("qa-5", None)


# -- CSS micro-fit for near-miss PDF overflow (tiered skip) ----------------------------

def _mock_render_sequence(monkeypatch, page_counts):
    """page_counts is consumed in call order; a call beyond the list raises StopIteration
    -- an implicit "this called render more times than expected" assertion."""
    counts = iter(page_counts)
    monkeypatch.setattr(app_module.render_pdf, "render", lambda html, pdf: next(counts))
    monkeypatch.setattr(app_module.render_docx, "build", lambda content, path: None)


def test_css_fit_within_first_two_tweaks_skips_qa_call_entirely(tmp_path, monkeypatch):
    app_module.RUNS["qa-6"] = {"percent": 0, "step": "", "done": False, "error": None, "cancelled": False}
    paths = _make_qa_paths(tmp_path, target_pages=1)
    # initial render: 2 pages (one over budget) -> tweak 1: still 2 -> tweak 2: fits at 1.
    _mock_render_sequence(monkeypatch, [2, 2, 1])

    def fail_if_called(*a, **kw):
        raise AssertionError("Claude QA call should have been skipped for a mild CSS fit")

    monkeypatch.setattr(app_module.claude_runner, "run_single_call", fail_if_called)

    result = app_module._run_render_qa_loop("qa-6", tmp_path, paths)

    assert result is True
    app_module.RUNS.pop("qa-6", None)


def test_css_fit_needing_harder_tweak_still_runs_qa_call(tmp_path, monkeypatch):
    app_module.RUNS["qa-7"] = {"percent": 0, "step": "", "done": False, "error": None, "cancelled": False}
    paths = _make_qa_paths(tmp_path, target_pages=1)
    # initial: 2 -> tweak 1: 2 -> tweak 2: 2 -> tweak 3: fits at 1.
    _mock_render_sequence(monkeypatch, [2, 2, 2, 1])

    calls = {"n": 0}

    def fake_run_single_call(job_id, cmd, project_root, run_dir, on_event, jobs, timeout_seconds=None, fail_messages=None):
        calls["n"] += 1
        (tmp_path / "qa_result.json").write_text(json.dumps({"status": "pass"}))
        return True, ""

    monkeypatch.setattr(app_module.claude_runner, "run_single_call", fake_run_single_call)

    result = app_module._run_render_qa_loop("qa-7", tmp_path, paths)

    assert result is True
    assert calls["n"] == 1
    app_module.RUNS.pop("qa-7", None)


def test_css_fit_no_tweak_works_falls_through_to_normal_qa(tmp_path, monkeypatch):
    app_module.RUNS["qa-8"] = {"percent": 0, "step": "", "done": False, "error": None, "cancelled": False}
    paths = _make_qa_paths(tmp_path, target_pages=1)
    # initial: 2 -> all 5 tweaks still 2 -> revert re-render: still 2.
    _mock_render_sequence(monkeypatch, [2, 2, 2, 2, 2, 2, 2])

    calls = {"n": 0}

    def fake_run_single_call(job_id, cmd, project_root, run_dir, on_event, jobs, timeout_seconds=None, fail_messages=None):
        calls["n"] += 1
        (tmp_path / "qa_result.json").write_text(json.dumps({"status": "pass"}))
        return True, ""

    monkeypatch.setattr(app_module.claude_runner, "run_single_call", fake_run_single_call)

    result = app_module._run_render_qa_loop("qa-8", tmp_path, paths)

    assert result is True
    assert calls["n"] == 1
    app_module.RUNS.pop("qa-8", None)


def test_css_fit_not_attempted_when_more_than_one_page_over(tmp_path, monkeypatch):
    app_module.RUNS["qa-9"] = {"percent": 0, "step": "", "done": False, "error": None, "cancelled": False}
    paths = _make_qa_paths(tmp_path, target_pages=1)
    # 2 pages over budget -- CSS fit is only attempted for exactly one page over, so only
    # the single initial render call should happen (a second call would raise StopIteration).
    _mock_render_sequence(monkeypatch, [3])

    calls = {"n": 0}

    def fake_run_single_call(job_id, cmd, project_root, run_dir, on_event, jobs, timeout_seconds=None, fail_messages=None):
        calls["n"] += 1
        (tmp_path / "qa_result.json").write_text(json.dumps({"status": "pass"}))
        return True, ""

    monkeypatch.setattr(app_module.claude_runner, "run_single_call", fake_run_single_call)

    result = app_module._run_render_qa_loop("qa-9", tmp_path, paths)

    assert result is True
    assert calls["n"] == 1
    app_module.RUNS.pop("qa-9", None)


# -- resume / checkpoint + friendly failure causes -------------------------------------
#
# Durable run-state (run_state.json) lets a failed/interrupted run resume from its last
# completed phase instead of re-running the expensive draft. These cover the app-level
# glue: failure classification, phase selection, skipping already-done phases, startup
# rehydration, and the sweep keeping resumable folders.

import run_state as run_state_mod


def test_classify_failure_detects_usage_limit(tmp_path):
    tail = '{"is_error":true,"result":"You\'ve hit your session limit","api_error_status":429}'
    cause = app_module._classify_failure(tail, tmp_path, None)
    assert "usage limit" in cause.lower()


def test_classify_failure_detects_blocked_url_when_no_content(tmp_path):
    tail = "WebFetch returned 403 Forbidden (login wall)"
    cause = app_module._classify_failure(tail, tmp_path, "https://example.com/job")
    assert "paste" in cause.lower()


def test_classify_failure_content_present_is_resumable_message(tmp_path):
    (tmp_path / "content.json").write_text("{}")
    cause = app_module._classify_failure("some unrelated crash", tmp_path, None)
    assert "resume" in cause.lower()


def test_classify_failure_returns_none_when_nothing_recognizable(tmp_path):
    assert app_module._classify_failure("totally generic noise", tmp_path, None) is None


def test_furthest_completed_phase_uses_failed_phase():
    state = {"phase": run_state_mod.PHASE_FAILED, "failed_phase": run_state_mod.PHASE_RENDERED}
    assert app_module._furthest_completed_phase(state) == run_state_mod.PHASE_RENDERED


def test_furthest_completed_phase_clamps_drafting_to_drafted():
    assert app_module._furthest_completed_phase({"phase": run_state_mod.PHASE_DRAFTING}) == run_state_mod.PHASE_DRAFTED


def test_furthest_completed_phase_passes_through_voice_checked():
    assert app_module._furthest_completed_phase({"phase": run_state_mod.PHASE_VOICE_CHECKED}) == run_state_mod.PHASE_VOICE_CHECKED


def test_post_draft_resume_from_rendered_skips_voice_and_render(tmp_path, monkeypatch):
    # Starting at 'rendered' means voice guard and the QA loop are already done -- neither
    # should run again; only finalize should happen. This is the token/latency win.
    app_module.RUNS["rp-1"] = {
        "percent": 50, "step": "", "done": False, "error": None, "cancelled": False,
        "run_dir": tmp_path, "user_id": 1,
    }
    (tmp_path / "content.json").write_text(json.dumps({"summary": "x"}))
    (tmp_path / "summary.json").write_text(json.dumps({"summary": "s", "changes": []}))

    def boom(*a, **kw):
        raise AssertionError("should not re-run this phase when resuming from 'rendered'")

    monkeypatch.setattr(app_module, "_run_voice_guard", boom)
    monkeypatch.setattr(app_module, "_run_render_qa_loop", boom)
    finalized = {"n": 0}
    monkeypatch.setattr(app_module, "_finalize_application_run", lambda rid: finalized.__setitem__("n", finalized["n"] + 1))

    app_module._run_post_draft_phases("rp-1", tmp_path, {"output_format": "pdf", "content_file": tmp_path / "content.json"}, run_state_mod.PHASE_RENDERED, True)

    assert finalized["n"] == 1
    assert app_module.RUNS["rp-1"]["done"] is True
    assert app_module.RUNS["rp-1"]["error"] is None
    app_module.RUNS.pop("rp-1", None)


def test_rehydrate_interrupted_runs_lists_unfinalized_as_resumable(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "RUNS_DIR", tmp_path)
    run_dir = tmp_path / "run-abc"
    run_dir.mkdir()
    run_state_mod.write_state(run_dir, {
        "run_id": "run-abc", "user_id": 7, "output_format": "pdf", "download_name": "cv",
        "path_names": {"output_file": "output.pdf", "content_file": "content.json", "cl_output_file": None},
        "phase": run_state_mod.PHASE_DRAFTED,
    })
    app_module.RUNS.pop("run-abc", None)

    app_module._rehydrate_interrupted_runs()

    assert "run-abc" in app_module.RUNS
    entry = app_module.RUNS["run-abc"]
    assert entry["done"] is True and entry["resumable"] is True
    assert entry["user_id"] == 7
    assert entry["error_cause"] == app_module.INTERRUPTED_CAUSE
    app_module.RUNS.pop("run-abc", None)


def test_rehydrate_skips_finalized_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "RUNS_DIR", tmp_path)
    run_dir = tmp_path / "run-fin"
    run_dir.mkdir()
    run_state_mod.write_state(run_dir, {"run_id": "run-fin", "phase": run_state_mod.PHASE_FINALIZED})

    app_module._rehydrate_interrupted_runs()

    assert "run-fin" not in app_module.RUNS


def test_sweep_keeps_resumable_run_under_long_ttl(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "RUNS_DIR", tmp_path)
    resumable = tmp_path / "resumable"
    resumable.mkdir()
    run_state_mod.write_state(resumable, {"run_id": "resumable", "phase": run_state_mod.PHASE_FAILED})
    plain = tmp_path / "plain"
    plain.mkdir()
    # Age both well past the short 2h scratch TTL but under the 7-day resumable TTL.
    old = time.time() - (app_module.RUN_RETENTION_SECONDS + 3600)
    import os
    os.utime(resumable, (old, old))
    os.utime(plain, (old, old))

    app_module._sweep_stale_runs()

    assert resumable.exists()      # kept: resumable, long TTL not reached
    assert not plain.exists()      # deleted: no saved state, short TTL exceeded


# -- resume / discard / resumable-list endpoints ---------------------------------------

def test_resume_unknown_run_returns_404():
    assert client.post("/api/tailor/nope/resume").status_code == 404


def test_resume_non_resumable_run_returns_409(tmp_path):
    app_module.RUNS["res-1"] = {
        "user_id": CURRENT_USER_ID, "done": True, "resumable": False,
        "run_dir": tmp_path, "step": "", "percent": 0, "error": None,
    }
    resp = client.post("/api/tailor/res-1/resume")
    assert resp.status_code == 409
    app_module.RUNS.pop("res-1", None)


def test_resume_skips_draft_when_content_present(tmp_path, monkeypatch):
    run_dir = tmp_path / "res-2"
    run_dir.mkdir()
    (run_dir / "content.json").write_text(json.dumps({"summary": "x"}))
    run_state_mod.write_state(run_dir, {
        "run_id": "res-2", "user_id": CURRENT_USER_ID, "output_format": "pdf",
        "path_names": {"content_file": "content.json", "output_file": "output.pdf", "cl_output_file": None},
        "phase": run_state_mod.PHASE_FAILED, "failed_phase": run_state_mod.PHASE_RENDERED,
        "draft_prompt": "SHOULD NOT BE USED", "allowed_tools": "Read Write",
    })
    app_module.RUNS["res-2"] = app_module._rehydrate_run(run_dir, run_state_mod.load_state(run_dir))

    seen = {"phase": None, "redraft": False}

    def fake_post_draft(run_id, rd, paths, start_phase, finalize):
        seen["phase"] = start_phase

    def fake_run_claude(*a, **kw):
        seen["redraft"] = True

    monkeypatch.setattr(app_module, "_run_post_draft_phases", fake_post_draft)
    monkeypatch.setattr(app_module, "_run_claude", fake_run_claude)

    resp = client.post("/api/tailor/res-2/resume")
    assert resp.status_code == 200
    # Give the daemon thread a beat to run the (trivial) target.
    for _ in range(50):
        if seen["phase"] is not None:
            break
        time.sleep(0.01)
    assert seen["phase"] == run_state_mod.PHASE_RENDERED  # resumed at last completed phase
    assert seen["redraft"] is False                        # the expensive draft was skipped
    app_module.RUNS.pop("res-2", None)


def test_discard_deletes_run_and_removes_entry(tmp_path):
    run_dir = tmp_path / "disc-1"
    run_dir.mkdir()
    (run_dir / "content.json").write_text("{}")
    app_module.RUNS["disc-1"] = {
        "user_id": CURRENT_USER_ID, "done": True, "resumable": True, "run_dir": run_dir,
        "step": "", "percent": 0, "error": None,
    }
    resp = client.post("/api/tailor/disc-1/discard")
    assert resp.status_code == 200
    assert not run_dir.exists()
    assert "disc-1" not in app_module.RUNS


def test_resumable_list_returns_only_own_resumable_runs(tmp_path):
    app_module.RUNS["mine-r"] = {
        "user_id": CURRENT_USER_ID, "resumable": True, "error_cause": "limit hit",
        "metadata_file": tmp_path / "nope.json", "run_dir": tmp_path,
    }
    app_module.RUNS["mine-done"] = {"user_id": CURRENT_USER_ID, "resumable": False, "run_dir": tmp_path}
    app_module.RUNS["other-r"] = {"user_id": CURRENT_USER_ID + 999, "resumable": True, "run_dir": tmp_path}

    resp = client.get("/api/tailor/resumable")
    assert resp.status_code == 200
    ids = {r["run_id"] for r in resp.json()["runs"]}
    assert ids == {"mine-r"}
    for k in ("mine-r", "mine-done", "other-r"):
        app_module.RUNS.pop(k, None)


# -- recent field memory (short-term "carry over from recent CVs") ----------------------

import os as _os


def _write_recent_cv(user_id, name, mtime, **fields):
    d = app_module.DATA_DIR / "users" / str(user_id) / "applications" / "co" / name / "2026-01-01"
    d.mkdir(parents=True, exist_ok=True)
    base = {"full_name": "Jane Doe", "email": "j@x.com"}
    base.update(fields)
    p = d / "content.json"
    p.write_text(json.dumps(base))
    _os.utime(p, (mtime, mtime))
    return p


def test_field_memory_majority_default_and_most_recent_value():
    uid = CURRENT_USER_ID
    # 3 recent CVs: phone in all (newest = the value we expect), work_auth in only 1 (minority).
    _write_recent_cv(uid, "a", 3000, phone="+31 NEW", work_authorization="visa line")
    _write_recent_cv(uid, "b", 2000, phone="+31 OLD")
    _write_recent_cv(uid, "c", 1000, phone="+31 OLDEST")

    mem = {f["field"]: f for f in app_module._recent_field_memory(uid)}
    assert mem["phone"]["value"] == "+31 NEW"       # newest non-empty value
    assert mem["phone"]["default_checked"] is True  # 3/3 -> majority
    assert mem["work_authorization"]["default_checked"] is False  # 1/3 -> minority
    assert "links" not in mem  # never present -> not suggested


def test_field_memory_empty_when_no_history():
    assert app_module._recent_field_memory(CURRENT_USER_ID) == []


def test_field_memory_includes_photo_when_master_photo_exists():
    uid = CURRENT_USER_ID
    _write_recent_cv(uid, "a", 3000, phone="+31", photo_path="/somewhere/photo.jpg")
    # Seed a master photo file.
    master = routes_master_cv.master_cv_dir(uid)
    master.mkdir(parents=True, exist_ok=True)
    (master / "photo.jpeg").write_bytes(b"not-a-real-image")

    mem = {f["field"]: f for f in app_module._recent_field_memory(uid)}
    assert "photo" in mem
    assert mem["photo"]["value"] == ""
    assert mem["photo"]["default_checked"] is True  # 1/1 had a photo


def test_field_memory_endpoint_returns_fields():
    _write_recent_cv(CURRENT_USER_ID, "a", 3000, phone="+31 6 123")
    resp = client.get("/api/tailor/field-memory")
    assert resp.status_code == 200
    fields = {f["field"] for f in resp.json()["fields"]}
    assert "phone" in fields


# -- field-override parsing + prompt directive -----------------------------------------

def test_parse_field_overrides_keeps_known_caps_and_strips():
    raw = json.dumps([
        {"field": "phone", "include": True, "value": "  +31 6 999  "},
        {"field": "work_authorization", "include": False, "value": "x"},
        {"field": "bogus", "include": True, "value": "nope"},
    ])
    parsed = app_module._parse_field_overrides(raw)
    assert parsed["phone"] == {"include": True, "value": "+31 6 999"}
    assert parsed["work_authorization"]["include"] is False
    assert "bogus" not in parsed


def test_parse_field_overrides_bad_json_returns_empty():
    assert app_module._parse_field_overrides("not json") == {}
    assert app_module._parse_field_overrides("") == {}


def test_build_contact_directive_include_and_omit():
    overrides = {
        "phone": {"include": True, "value": "+31 6 999"},
        "location": {"include": False, "value": ""},
    }
    directive = app_module._build_contact_directive(overrides)
    assert '+31 6 999' in directive
    assert "omit entirely" in directive.lower()


def test_start_injects_contact_directive_into_prompt(monkeypatch):
    captured = {}

    def fake_run_claude(run_id, prompt, run_dir, paths, allowed_tools=app_module.TAILOR_FROM_MASTER_ALLOWED_TOOLS):
        captured["prompt"] = prompt

    monkeypatch.setattr(app_module, "_run_claude", fake_run_claude)
    resp = client.post("/api/tailor/start", data={
        "job_text": "We need a data analyst.",
        "output_format": "pdf",
        "field_overrides": json.dumps([{"field": "phone", "include": True, "value": "+31 6 84034767"}]),
    })
    assert resp.status_code == 200
    assert "+31 6 84034767" in captured["prompt"]


def test_start_omits_photo_when_toggle_off(monkeypatch):
    captured = {}

    def fake_run_claude(run_id, prompt, run_dir, paths, allowed_tools=app_module.TAILOR_FROM_MASTER_ALLOWED_TOOLS):
        captured["prompt"] = prompt

    monkeypatch.setattr(app_module, "_run_claude", fake_run_claude)
    # Seed a master photo so the "omit" branch is meaningful.
    master = routes_master_cv.master_cv_dir(CURRENT_USER_ID)
    master.mkdir(parents=True, exist_ok=True)
    (master / "photo.jpeg").write_bytes(b"img")

    resp = client.post("/api/tailor/start", data={
        "job_text": "We need a data analyst.",
        "output_format": "pdf",
        "field_overrides": json.dumps([{"field": "photo", "include": False}]),
    })
    assert resp.status_code == 200
    assert "Do not include a photo" in captured["prompt"]


# -- concurrency cap + /runs listing ---------------------------------------------------

import threading as _threading


def test_run_claude_gate_queues_beyond_capacity(monkeypatch):
    # With a 1-slot semaphore, the second run must wait (queued) while the first holds it.
    monkeypatch.setattr(app_module, "_run_semaphore", _threading.Semaphore(1))
    started = _threading.Event()
    release = _threading.Event()
    inner_calls = []

    def fake_inner(run_id, *a, **kw):
        inner_calls.append(run_id)
        started.set()
        release.wait(timeout=5)

    monkeypatch.setattr(app_module, "_run_claude_inner", fake_inner)
    app_module.RUNS["g1"] = {"cancelled": False, "queued": False, "percent": 0, "step": ""}
    app_module.RUNS["g2"] = {"cancelled": False, "queued": False, "percent": 0, "step": ""}

    t1 = _threading.Thread(target=app_module._run_claude, args=("g1", "p", None, {}), daemon=True)
    t1.start()
    started.wait(timeout=5)  # g1 grabbed the only slot and entered inner
    t2 = _threading.Thread(target=app_module._run_claude, args=("g2", "p", None, {}), daemon=True)
    t2.start()
    for _ in range(100):
        if app_module.RUNS["g2"]["queued"]:
            break
        time.sleep(0.02)

    assert app_module.RUNS["g2"]["queued"] is True   # queued behind the full slot
    assert inner_calls == ["g1"]                       # g2 hasn't started

    release.set()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert set(inner_calls) == {"g1", "g2"}            # g2 ran once the slot freed
    app_module.RUNS.pop("g1", None)
    app_module.RUNS.pop("g2", None)


def test_run_claude_gate_cancel_while_queued_never_runs(monkeypatch):
    monkeypatch.setattr(app_module, "_run_semaphore", _threading.Semaphore(1))
    release = _threading.Event()
    inner_calls = []

    def fake_inner(run_id, *a, **kw):
        inner_calls.append(run_id)
        release.wait(timeout=5)

    monkeypatch.setattr(app_module, "_run_claude_inner", fake_inner)
    app_module.RUNS["h1"] = {"cancelled": False, "queued": False, "percent": 0, "step": ""}
    app_module.RUNS["h2"] = {"cancelled": False, "queued": False, "percent": 0, "step": ""}

    t1 = _threading.Thread(target=app_module._run_claude, args=("h1", "p", None, {}), daemon=True)
    t1.start()
    for _ in range(100):
        if "h1" in inner_calls:
            break
        time.sleep(0.02)
    t2 = _threading.Thread(target=app_module._run_claude, args=("h2", "p", None, {}), daemon=True)
    t2.start()
    for _ in range(100):
        if app_module.RUNS["h2"]["queued"]:
            break
        time.sleep(0.02)

    app_module.RUNS["h2"]["cancelled"] = True  # cancel while queued
    t2.join(timeout=5)
    assert "h2" not in inner_calls  # never started Claude

    release.set()
    t1.join(timeout=5)
    app_module.RUNS.pop("h1", None)
    app_module.RUNS.pop("h2", None)


def test_status_includes_queued_flag(tmp_path):
    app_module.RUNS["q-run"] = {
        "user_id": CURRENT_USER_ID, "step": "Waiting…", "percent": 0, "done": False,
        "error": None, "queued": True, "run_dir": tmp_path, "output_format": "pdf",
        "download_name": "cv", "cover_letter_file": None,
    }
    resp = client.get("/api/tailor/q-run/status")
    assert resp.status_code == 200
    assert resp.json()["queued"] is True
    app_module.RUNS.pop("q-run", None)


def test_runs_endpoint_lists_active_and_resumable_only(tmp_path):
    app_module.RUNS["a-run"] = {"user_id": CURRENT_USER_ID, "done": False, "resumable": False, "run_dir": tmp_path}
    app_module.RUNS["r-run"] = {"user_id": CURRENT_USER_ID, "done": True, "resumable": True, "error": "e", "run_dir": tmp_path}
    app_module.RUNS["done-run"] = {"user_id": CURRENT_USER_ID, "done": True, "resumable": False, "run_dir": tmp_path}
    app_module.RUNS["other-user"] = {"user_id": CURRENT_USER_ID + 999, "done": False, "resumable": False, "run_dir": tmp_path}

    resp = client.get("/api/tailor/runs")
    assert resp.status_code == 200
    ids = {r["run_id"] for r in resp.json()["runs"]}
    assert ids == {"a-run", "r-run"}  # active + resumable; excludes finished-successful and other users
    for k in ("a-run", "r-run", "done-run", "other-user"):
        app_module.RUNS.pop(k, None)


# -- _ensure_summary_report (restore the requirement checklist on resume) ---------------

def _make_summary_paths(run_dir, with_requirements=None):
    content_file = run_dir / "content.json"
    content_file.write_text(json.dumps({"summary": "x", "experience": []}))
    summary_file = run_dir / "summary.json"
    if with_requirements is not None:
        summary_file.write_text(json.dumps({"summary": "s", "changes": [], "requirements": with_requirements}))
    return {"content_file": content_file, "summary_file": summary_file, "output_format": "pdf"}


def test_ensure_summary_report_noop_when_requirements_present(tmp_path, monkeypatch):
    paths = _make_summary_paths(tmp_path, with_requirements=[{"name": "SQL", "status": "matched"}])
    app_module.RUNS["s1"] = {"job_text": "Need SQL.", "percent": 50}

    def fail(*a, **kw):
        raise AssertionError("no Claude call when the summary already has requirements")

    monkeypatch.setattr(app_module.claude_runner, "run_single_call", fail)
    assert app_module._ensure_summary_report("s1", tmp_path, paths) is True
    app_module.RUNS.pop("s1", None)


def test_ensure_summary_report_noop_when_no_content(tmp_path, monkeypatch):
    summary_file = tmp_path / "summary.json"  # no content.json
    paths = {"content_file": tmp_path / "content.json", "summary_file": summary_file, "output_format": "pdf"}
    app_module.RUNS["s2"] = {"job_text": "Need SQL."}

    def fail(*a, **kw):
        raise AssertionError("no Claude call when there's no content record to base a report on")

    monkeypatch.setattr(app_module.claude_runner, "run_single_call", fail)
    assert app_module._ensure_summary_report("s2", tmp_path, paths) is True
    app_module.RUNS.pop("s2", None)


def test_ensure_summary_report_regenerates_when_missing(tmp_path, monkeypatch):
    paths = _make_summary_paths(tmp_path, with_requirements=None)  # summary.json absent
    app_module.RUNS["s3"] = {"job_text": "Need SQL and Python.", "percent": 50, "cancelled": False}

    calls = {"n": 0}

    def fake_run_single_call(job_id, cmd, project_root, run_dir, on_event, jobs, timeout_seconds=None, fail_messages=None):
        calls["n"] += 1
        (tmp_path / "summary.json").write_text(json.dumps({
            "summary": "regen", "changes": [], "requirements": [{"name": "SQL", "status": "matched"}],
        }))
        return True, ""

    monkeypatch.setattr(app_module.claude_runner, "run_single_call", fake_run_single_call)
    assert app_module._ensure_summary_report("s3", tmp_path, paths) is True
    assert calls["n"] == 1
    assert app_module._summary_has_requirements(paths["summary_file"]) is True
    app_module.RUNS.pop("s3", None)


def test_ensure_summary_report_best_effort_on_failure(tmp_path, monkeypatch):
    paths = _make_summary_paths(tmp_path, with_requirements=[])  # present but empty
    app_module.RUNS["s4"] = {"job_text": "Need SQL.", "percent": 50, "cancelled": False}

    def fake_run_single_call(job_id, cmd, project_root, run_dir, on_event, jobs, timeout_seconds=None, fail_messages=None):
        jobs[job_id].update(done=True, error="boom")  # run_single_call marks errored on failure
        return False, ""

    monkeypatch.setattr(app_module.claude_runner, "run_single_call", fake_run_single_call)
    # Best-effort: still returns True (proceed) and clears the errored state.
    assert app_module._ensure_summary_report("s4", tmp_path, paths) is True
    assert app_module.RUNS["s4"]["done"] is False
    assert app_module.RUNS["s4"]["error"] is None
    app_module.RUNS.pop("s4", None)


def test_ensure_summary_report_stops_when_cancelled(tmp_path, monkeypatch):
    paths = _make_summary_paths(tmp_path, with_requirements=[])
    app_module.RUNS["s5"] = {"job_text": "Need SQL.", "percent": 50, "cancelled": False}

    def fake_run_single_call(job_id, cmd, project_root, run_dir, on_event, jobs, timeout_seconds=None, fail_messages=None):
        jobs[job_id]["cancelled"] = True
        return False, ""

    monkeypatch.setattr(app_module.claude_runner, "run_single_call", fake_run_single_call)
    assert app_module._ensure_summary_report("s5", tmp_path, paths) is False
    app_module.RUNS.pop("s5", None)


# -- remove from applications (/unfile) -------------------------------------------------

def test_delete_application_attempt_removes_app_when_last(tmp_path):
    import db as db_mod
    app = db_mod.find_or_create_application(CURRENT_USER_ID, "Acme", "acme", "Analyst", "analyst")
    att = db_mod.create_application_attempt(app["id"], "pdf", "cv", "some/path", False, "2026-01-01T00:00:00Z")

    result = db_mod.delete_application_attempt(att["id"], CURRENT_USER_ID)
    assert result["deleted_application"] is True
    assert db_mod.get_application_attempt(att["id"], CURRENT_USER_ID) is None
    assert db_mod.find_application_by_id(app["id"], CURRENT_USER_ID) is None


def test_delete_application_attempt_keeps_app_when_other_attempts_remain(tmp_path):
    import db as db_mod
    app = db_mod.find_or_create_application(CURRENT_USER_ID, "Acme", "acme", "Analyst", "analyst")
    a1 = db_mod.create_application_attempt(app["id"], "pdf", "cv", "p1", False, "2026-01-01T00:00:00Z")
    a2 = db_mod.create_application_attempt(app["id"], "pdf", "cv", "p2", False, "2026-01-02T00:00:00Z")

    result = db_mod.delete_application_attempt(a1["id"], CURRENT_USER_ID)
    assert result["deleted_application"] is False
    assert db_mod.get_application_attempt(a1["id"], CURRENT_USER_ID) is None
    assert db_mod.get_application_attempt(a2["id"], CURRENT_USER_ID) is not None
    assert db_mod.find_application_by_id(app["id"], CURRENT_USER_ID) is not None


def test_delete_application_attempt_other_user_returns_none(tmp_path):
    import db as db_mod
    app = db_mod.find_or_create_application(CURRENT_USER_ID, "Acme", "acme", "Analyst", "analyst")
    att = db_mod.create_application_attempt(app["id"], "pdf", "cv", "p", False, "2026-01-01T00:00:00Z")
    assert db_mod.delete_application_attempt(att["id"], CURRENT_USER_ID + 999) is None


def test_unfile_endpoint_removes_and_keeps_files(tmp_path):
    import db as db_mod
    # Simulate a filed run: create the app/attempt + a folder under applications/.
    app = db_mod.find_or_create_application(CURRENT_USER_ID, "Acme", "acme", "Analyst", "analyst")
    att = db_mod.create_application_attempt(app["id"], "pdf", "cv", "acme/analyst/x", False, "2026-01-01T00:00:00Z")
    run_dir = app_module.DATA_DIR / "users" / str(CURRENT_USER_ID) / "applications" / "acme" / "analyst" / "x"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "output.pdf").write_bytes(b"%PDF-1.4 fake")

    app_module.RUNS["uf-1"] = {
        "user_id": CURRENT_USER_ID, "done": True, "error": None, "run_dir": run_dir,
        "output_file": run_dir / "output.pdf", "cover_letter_file": None, "output_format": "pdf",
        "application": {"pending": False, "company_name": "Acme", "role_name": "Analyst",
                        "application_id": app["id"], "attempt_id": att["id"], "is_applied": False},
    }

    resp = client.post("/api/tailor/uf-1/unfile")
    assert resp.status_code == 200
    assert resp.json()["application"]["removed"] is True
    # DB rows gone.
    assert db_mod.get_application_attempt(att["id"], CURRENT_USER_ID) is None
    # Files moved to unfiled/ and still present (downloadable).
    moved = app_module.DATA_DIR / "users" / str(CURRENT_USER_ID) / "unfiled" / "uf-1" / "output.pdf"
    assert moved.exists()
    assert app_module.RUNS["uf-1"]["output_file"] == moved
    app_module.RUNS.pop("uf-1", None)


def test_unfile_endpoint_409_when_not_filed(tmp_path):
    app_module.RUNS["uf-2"] = {
        "user_id": CURRENT_USER_ID, "done": True, "run_dir": tmp_path,
        "application": {"pending": True, "pending_id": 5},
    }
    resp = client.post("/api/tailor/uf-2/unfile")
    assert resp.status_code == 409
    app_module.RUNS.pop("uf-2", None)


# -- auto filename standard [name]_[company]_[role]_cv ----------------------------------

def test_build_default_filename_basic():
    assert app_module._build_default_filename("Nathan Birmaher", "Acme", "Analyst") == "Nathan_Birmaher_Acme_Analyst_cv"


def test_build_default_filename_first_last_for_long_names():
    out = app_module._build_default_filename("Ana Maria de la Cruz", "Acme", "Analyst")
    assert out == "Ana_Cruz_Acme_Analyst_cv"


def test_build_default_filename_shortens_long_role_to_acronym():
    out = app_module._build_default_filename(
        "Nathan Birmaher", "Acme",
        "Senior Business Intelligence and Analytics Engineering Lead",
    )
    # Role acronymized to keep it readable; name + company stay intact.
    assert out.startswith("Nathan_Birmaher_Acme_")
    assert out.endswith("_cv")
    assert len(out) <= 76
    assert "SBIAAEL" in out


def test_build_default_filename_empty_falls_back():
    assert app_module._build_default_filename("", "", "") == "tailored_cv"


def test_start_sets_auto_filename_flag_when_blank(monkeypatch):
    captured = {}
    monkeypatch.setattr(app_module, "_run_claude", lambda *a, **kw: None)
    resp = client.post("/api/tailor/start", data={"job_text": "Need an analyst.", "output_format": "pdf"})
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]
    assert app_module.RUNS[run_id]["auto_filename"] is True
    app_module.RUNS.pop(run_id, None)


def test_start_no_auto_filename_when_provided(monkeypatch):
    monkeypatch.setattr(app_module, "_run_claude", lambda *a, **kw: None)
    resp = client.post("/api/tailor/start", data={"job_text": "Need an analyst.", "output_format": "pdf", "filename": "MyCV"})
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]
    assert app_module.RUNS[run_id]["auto_filename"] is False
    assert app_module.RUNS[run_id]["download_name"] == "MyCV"
    app_module.RUNS.pop(run_id, None)


# -- job search: results parsing, job_leads DB, endpoints -------------------------------

def test_job_dedup_key_normalizes():
    a = app_module._job_dedup_key("Acme Inc.", "Data Analyst", "https://boards.greenhouse.io/acme/jobs/1")
    b = app_module._job_dedup_key("acme inc", "data  analyst", "https://boards.greenhouse.io/acme/jobs/1")
    assert a == b


def test_read_job_results_parses_and_drops_bad_rows(tmp_path):
    rf = tmp_path / "results.json"
    rf.write_text(json.dumps({"jobs": [
        {"company": "Acme", "role": "Data Analyst", "url": "https://x/1", "source": "greenhouse",
         "location": "Amsterdam", "why_fits": "Strong SQL match",
         "requirements": [{"name": "SQL", "status": "matched", "evidence": "Built SQL pipelines"},
                          {"name": "Bad", "status": "nonsense"}]},
        {"company": "", "role": "Missing company", "url": "https://x/2"},   # dropped: no company
        {"role": "No url", "company": "X"},                                 # dropped: no url
        "not a dict",                                                       # dropped
    ]}))
    out = app_module._read_job_results(rf)
    assert len(out) == 1
    assert out[0]["company_name"] == "Acme"
    assert [r["name"] for r in out[0]["requirements"]] == ["SQL"]  # bad-status requirement dropped
    assert out[0]["dedup_key"]


def test_read_job_results_missing_file_returns_empty(tmp_path):
    assert app_module._read_job_results(tmp_path / "nope.json") == []


def test_read_job_results_scores_clamped_and_sorted_desc(tmp_path):
    rf = tmp_path / "results.json"
    rf.write_text(json.dumps({"jobs": [
        {"company": "Low", "role": "R", "url": "https://x/low", "match_score": 40, "match_level": "possible"},
        {"company": "High", "role": "R", "url": "https://x/high", "match_score": 95, "match_level": "strong"},
        {"company": "Over", "role": "R", "url": "https://x/over", "match_score": 250, "match_level": "bogus"},  # clamped to 100, level dropped
        {"company": "Bad", "role": "R", "url": "https://x/bad", "match_score": "n/a"},  # unparseable -> 0
    ]}))
    out = app_module._read_job_results(rf)
    assert [o["company_name"] for o in out] == ["Over", "High", "Low", "Bad"]  # descending by score
    assert out[0]["match_score"] == 100 and out[0]["match_level"] == ""  # clamped, invalid level cleared
    assert out[-1]["match_score"] == 0  # unparseable defaults to 0


def test_job_leads_ordered_by_match_score_desc():
    import db as db_mod
    db_mod.upsert_job_lead(CURRENT_USER_ID, {"dedup_key": "s-lo", "company_name": "Lo", "role_name": "R",
                                             "url": "https://x/lo", "match_score": 42, "match_level": "possible"})
    db_mod.upsert_job_lead(CURRENT_USER_ID, {"dedup_key": "s-hi", "company_name": "Hi", "role_name": "R",
                                             "url": "https://x/hi", "match_score": 88, "match_level": "strong"})
    leads = db_mod.list_job_leads(CURRENT_USER_ID)
    assert [l["company_name"] for l in leads] == ["Hi", "Lo"]
    assert leads[0]["match_score"] == 88 and leads[0]["match_level"] == "strong"


def test_job_leads_upsert_dedupes_and_preserves_dismissal():
    import db as db_mod
    lead = {"dedup_key": "k1", "company_name": "Acme", "role_name": "Analyst", "url": "https://x/1",
            "location": "Amsterdam", "source": "greenhouse", "why_fits": "fit", "requirements": [{"name": "SQL", "status": "matched"}]}
    db_mod.upsert_job_lead(CURRENT_USER_ID, lead)
    db_mod.upsert_job_lead(CURRENT_USER_ID, {**lead, "why_fits": "updated fit"})  # same key -> update

    leads = db_mod.list_job_leads(CURRENT_USER_ID)
    assert len(leads) == 1
    assert leads[0]["why_fits"] == "updated fit"
    assert leads[0]["requirements"][0]["name"] == "SQL"

    # Dismiss, then re-find (upsert) -> stays dismissed (user action preserved).
    assert db_mod.dismiss_job_lead(leads[0]["id"], CURRENT_USER_ID) is True
    assert db_mod.list_job_leads(CURRENT_USER_ID) == []
    db_mod.upsert_job_lead(CURRENT_USER_ID, {**lead, "why_fits": "found again"})
    assert db_mod.list_job_leads(CURRENT_USER_ID) == []  # still dismissed


def test_job_leads_scoped_to_user():
    import db as db_mod
    db_mod.upsert_job_lead(CURRENT_USER_ID, {"dedup_key": "k2", "company_name": "A", "role_name": "R", "url": "https://x/2"})
    assert len(db_mod.list_job_leads(CURRENT_USER_ID)) == 1
    assert db_mod.list_job_leads(CURRENT_USER_ID + 999) == []


def test_start_job_search_requires_location():
    resp = client.post("/api/job-search/start", data={"titles": "Data Analyst"})
    assert resp.status_code == 422  # missing required Form field


def test_start_job_search_creates_search_run(monkeypatch):
    monkeypatch.setattr(app_module, "_run_job_search", lambda *a, **kw: None)
    resp = client.post("/api/job-search/start", data={"location": "Amsterdam", "titles": "Data Analyst"})
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]
    assert app_module.RUNS[run_id]["kind"] == "search"
    assert "Amsterdam" in app_module.RUNS[run_id]["label"]
    app_module.RUNS.pop(run_id, None)


def test_start_job_search_run_appears_in_runs_endpoint(monkeypatch):
    monkeypatch.setattr(app_module, "_run_job_search", lambda *a, **kw: None)
    run_id = client.post("/api/job-search/start", data={"location": "Berlin"}).json()["run_id"]
    runs = client.get("/api/tailor/runs").json()["runs"]
    match = [r for r in runs if r["run_id"] == run_id]
    assert match and match[0]["kind"] == "search"
    app_module.RUNS.pop(run_id, None)


def test_job_leads_endpoints(tmp_path):
    import db as db_mod
    db_mod.upsert_job_lead(CURRENT_USER_ID, {"dedup_key": "k3", "company_name": "Acme", "role_name": "Analyst", "url": "https://x/3"})
    leads = client.get("/api/job-leads").json()["leads"]
    assert len(leads) == 1
    lead_id = leads[0]["id"]
    assert client.post(f"/api/job-leads/{lead_id}/dismiss").status_code == 200
    assert client.get("/api/job-leads").json()["leads"] == []
    assert client.post("/api/job-leads/99999/dismiss").status_code == 404
