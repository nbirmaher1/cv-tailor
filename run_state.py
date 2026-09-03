"""Durable, on-disk run state for tailoring runs, so a failed or interrupted run can be
resumed from its last completed phase instead of re-running the expensive draft+review
Claude call from scratch.

A run's scratch folder (`runs/<id>/`) already holds its artifacts (content.json, the
rendered output, etc.); this adds a small `run_state.json` alongside them recording which
phase last completed and everything needed to rehydrate the in-memory RUNS entry after a
server restart. The run folder is the single source of truth -- there's no DB row for an
in-flight run (finalized applications go to the DB via the existing finalize path).

Phases, in order: drafting -> drafted -> voice_checked -> rendered -> finalized.
`failed` is a terminal-but-resumable marker written on any failure, with the phase that
failed recorded separately in `failed_phase`.
"""
import json
from pathlib import Path

STATE_FILENAME = "run_state.json"

PHASE_DRAFTING = "drafting"
PHASE_DRAFTED = "drafted"
PHASE_VOICE_CHECKED = "voice_checked"
PHASE_RENDERED = "rendered"
PHASE_FINALIZED = "finalized"
PHASE_FAILED = "failed"

# Ordered so resume can find the furthest-completed phase; `failed` is not on this line
# (it's a marker, not a progress point -- the real progress is in `failed_phase`).
PHASE_ORDER = [PHASE_DRAFTING, PHASE_DRAFTED, PHASE_VOICE_CHECKED, PHASE_RENDERED, PHASE_FINALIZED]


def _state_path(run_dir: Path) -> Path:
    return run_dir / STATE_FILENAME


def write_state(run_dir: Path, state: dict) -> None:
    """Writes the full state dict to run_state.json (best-effort: a failure to persist
    must never crash the run itself, so callers don't need to guard this)."""
    try:
        _state_path(run_dir).write_text(json.dumps(state))
    except OSError:
        pass


def load_state(run_dir: Path):
    """Returns the state dict, or None if the file is missing or unreadable/corrupt."""
    try:
        data = json.loads(_state_path(run_dir).read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def update_phase(run_dir: Path, phase: str, **extra) -> None:
    """Loads the state, sets `phase` (and any extra keys, e.g. error/error_cause/
    failed_phase), and writes it back. A no-op if no state file exists yet -- state is
    always created up front in start_tailor, so a missing file here means the run predates
    this feature or its folder is gone; either way there's nothing to update."""
    state = load_state(run_dir)
    if state is None:
        return
    state["phase"] = phase
    state.update(extra)
    write_state(run_dir, state)


def paths_from_state(run_dir: Path, state: dict) -> dict:
    """Rebuilds the absolute `paths` dict (the one _run_post_draft_phases/_render_output
    consume) from the bare filenames stored in state["path_names"]. A stored null stays
    None (e.g. cl_output_file for a run with no cover letter). output_format is carried on
    the paths dict too, matching how start_tailor builds it."""
    names = state.get("path_names", {})
    paths = {"output_format": state.get("output_format", "pdf")}
    for key, name in names.items():
        paths[key] = (run_dir / name) if name else None
    return paths
