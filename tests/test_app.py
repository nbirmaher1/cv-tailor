import pytest
from fastapi.testclient import TestClient

import app as app_module
from app import _classify_event, _sanitize_filename

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
