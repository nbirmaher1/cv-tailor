import json
import time

import pytest
from fastapi.testclient import TestClient

import app as app_module
from app import _classify_event, _read_rationale, _sanitize_filename, _sweep_stale_runs

client = TestClient(app_module.app)


@pytest.fixture(autouse=True)
def _isolate_runs_dir(tmp_path, monkeypatch):
    # Redirect the app's run-folder storage into a temp dir so tests never
    # write into the project's real runs/ directory.
    monkeypatch.setattr(app_module, "RUNS_DIR", tmp_path)


def _cv_file():
    return {"cv_file": ("cv.pdf", b"%PDF-1.4 fake pdf content", "application/pdf")}


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


# -- /api/tailor/start ------------------------------------------------------

def test_start_requires_job_url_or_text():
    response = client.post("/api/tailor/start", files=_cv_file(), data={"output_format": "pdf"})
    assert response.status_code == 400
    assert "job posting" in response.json()["detail"].lower()


def test_start_rejects_invalid_output_format():
    response = client.post(
        "/api/tailor/start", files=_cv_file(),
        data={"job_text": "We need a data analyst.", "output_format": "epub"},
    )
    assert response.status_code == 400


def test_start_accepts_valid_request_and_returns_run_id():
    response = client.post(
        "/api/tailor/start", files=_cv_file(),
        data={"job_text": "We need a data analyst.", "output_format": "pdf"},
    )
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    assert run_id in app_module.RUNS


# -- /api/tailor/{run_id}/status and /result --------------------------------

def test_status_unknown_run_id_returns_404():
    assert client.get("/api/tailor/does-not-exist/status").status_code == 404


def test_result_unknown_run_id_returns_404():
    assert client.get("/api/tailor/does-not-exist/result").status_code == 404


# -- upload validation ------------------------------------------------------

def test_start_rejects_non_pdf_docx_cv():
    response = client.post(
        "/api/tailor/start",
        files={"cv_file": ("cv.txt", b"plain text resume", "text/plain")},
        data={"job_text": "We need a data analyst.", "output_format": "pdf"},
    )
    assert response.status_code == 400
    assert "PDF" in response.json()["detail"]


def test_start_rejects_oversized_cv():
    oversized = b"x" * (app_module.MAX_CV_BYTES + 1)
    response = client.post(
        "/api/tailor/start",
        files={"cv_file": ("cv.pdf", oversized, "application/pdf")},
        data={"job_text": "We need a data analyst.", "output_format": "pdf"},
    )
    assert response.status_code == 400
    assert "too large" in response.json()["detail"].lower()


def test_start_rejects_oversized_job_text():
    response = client.post(
        "/api/tailor/start", files=_cv_file(),
        data={"job_text": "x" * (app_module.MAX_JOB_TEXT_CHARS + 1), "output_format": "pdf"},
    )
    assert response.status_code == 400
    assert "too long" in response.json()["detail"].lower()


def test_start_rejects_oversized_notes():
    response = client.post(
        "/api/tailor/start", files=_cv_file(),
        data={
            "job_text": "We need a data analyst.",
            "notes": "x" * (app_module.MAX_NOTES_CHARS + 1),
            "output_format": "pdf",
        },
    )
    assert response.status_code == 400
    assert "too long" in response.json()["detail"].lower()


def test_start_rejects_non_image_photo():
    response = client.post(
        "/api/tailor/start", files={
            **_cv_file(),
            "photo": ("headshot.txt", b"not an image", "text/plain"),
        },
        data={"job_text": "We need a data analyst.", "output_format": "pdf"},
    )
    assert response.status_code == 400
    assert "JPG" in response.json()["detail"]


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
