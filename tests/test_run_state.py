import json

import run_state


def test_write_and_load_round_trip(tmp_path):
    state = {"run_id": "abc", "phase": run_state.PHASE_DRAFTING, "user_id": 1}
    run_state.write_state(tmp_path, state)
    assert run_state.load_state(tmp_path) == state


def test_load_missing_returns_none(tmp_path):
    assert run_state.load_state(tmp_path) is None


def test_load_corrupt_returns_none(tmp_path):
    (tmp_path / run_state.STATE_FILENAME).write_text("{not valid json")
    assert run_state.load_state(tmp_path) is None


def test_update_phase_sets_phase_and_extra(tmp_path):
    run_state.write_state(tmp_path, {"run_id": "abc", "phase": run_state.PHASE_DRAFTING})
    run_state.update_phase(tmp_path, run_state.PHASE_FAILED, failed_phase="drafted", error_cause="limit hit")

    state = run_state.load_state(tmp_path)
    assert state["phase"] == run_state.PHASE_FAILED
    assert state["failed_phase"] == "drafted"
    assert state["error_cause"] == "limit hit"
    assert state["run_id"] == "abc"  # untouched keys preserved


def test_update_phase_noop_when_no_state_file(tmp_path):
    # Must not raise or create a file when there's nothing to update.
    run_state.update_phase(tmp_path, run_state.PHASE_RENDERED)
    assert not (tmp_path / run_state.STATE_FILENAME).exists()


def test_paths_from_state_rebuilds_absolute_paths_and_preserves_none(tmp_path):
    state = {
        "output_format": "pdf",
        "path_names": {
            "content_file": "content.json",
            "html_file": "output.html",
            "cl_output_file": None,
        },
    }
    paths = run_state.paths_from_state(tmp_path, state)
    assert paths["output_format"] == "pdf"
    assert paths["content_file"] == tmp_path / "content.json"
    assert paths["html_file"] == tmp_path / "output.html"
    assert paths["cl_output_file"] is None


def test_phase_order_is_the_expected_sequence():
    assert run_state.PHASE_ORDER == ["drafting", "drafted", "voice_checked", "rendered", "finalized"]
