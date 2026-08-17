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
