#!/usr/bin/env python3
"""
Local web app for cv-tailor. Drag in a CV, paste a job URL/description, get a
tailored PDF/DOCX back. Runs the tailoring itself by shelling out to the
Claude Code CLI in headless mode (`claude -p`), so it uses whatever Claude
Code subscription is logged in on the machine it runs on -- no separate API
key, no extra billing, and no server-side LLM cost. This means it only works
locally: each user clones this repo and runs the app on their own machine
with their own `claude` login.

The tailoring run happens in a background thread; progress is tracked by
parsing Claude Code's streaming JSON output for tool_use events (Read,
WebFetch, Agent, Bash, Write) and mapping them to human-readable steps, so
the frontend can poll for a live progress bar instead of staring at a static
"please wait" message.
"""
import contextlib
import json
import random
import re
import shutil
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

import auth
import claude_runner
import db
import run_state
from routes_applications import _resolve_company_role_slugs
from routes_applications import router as applications_router
from routes_auth import router as auth_router
from routes_master_cv import master_cv_dir
from routes_master_cv import router as master_cv_router

PROJECT_ROOT = Path(__file__).resolve().parent
RUNS_DIR = PROJECT_ROOT / "runs"
RUNS_DIR.mkdir(exist_ok=True)
DATA_DIR = PROJECT_ROOT / "data"

# The render/extract scripts are plain importable Python (not just CLI entry points) --
# imported directly rather than shelled out to via Bash, since Claude's own --allowedTools
# scoping of Bash turns out not to be reliably enforced by the CLI (verified empirically:
# a command outside an allowed Bash(...) pattern still executed). Untrusted job-posting
# text flows into the tailoring prompt via WebFetch, so Bash is dropped from Claude's tool
# access entirely for every web-app call site; these scripts run directly in this process
# instead, deterministically, with arguments this backend controls.
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import docx_stats  # noqa: E402
import extract_docx  # noqa: E402
import fill_html  # noqa: E402
import render_cover_letter_docx  # noqa: E402
import render_docx  # noqa: E402
import render_pdf  # noqa: E402

# Abandoned/errored run folders (uploaded CV, photo, any partial output) are swept on
# a best-effort basis at the start of each new request -- fine for a single-user local
# tool; a hosted multi-tenant version would want a real TTL/lifecycle policy instead.
RUN_RETENTION_SECONDS = 2 * 60 * 60
# Resumable (failed/interrupted, not-yet-finalized) runs are kept much longer, so a run
# that failed on a usage limit can still be resumed hours later once the limit resets --
# without re-running the expensive draft. Discarded explicitly via /discard, or swept once
# this longer TTL passes.
RESUMABLE_RETENTION_SECONDS = 7 * 24 * 60 * 60

MAX_CV_BYTES = 10 * 1024 * 1024
MAX_JOB_TEXT_CHARS = 50_000
MAX_NOTES_CHARS = 2_000
MAX_FEEDBACK_CHARS = 2_000
MAX_COVER_LETTER_NOTES_CHARS = 2_000
MAX_REVISIONS = 3
MAX_FIELD_VALUE_CHARS = 300
ALLOWED_COVER_LETTER_TEMPLATE_EXTENSIONS = {".pdf", ".docx", ".txt"}

# Optional identity/contact fields the "recent field memory" learns and offers as editable
# toggles on the tailor form (see _recent_field_memory). Core/derived fields (full_name,
# email, target_title) are excluded -- those aren't things the candidate re-adds per run.
# `photo` is special-cased (it has no text value -- it uses the saved master photo file).
FIELD_MEMORY_SAMPLE_SIZE = 10
FIELD_MEMORY_FIELDS = [
    ("phone", "Phone"),
    ("location", "Location"),
    ("links", "Links (LinkedIn / GitHub / portfolio)"),
    ("work_authorization", "Work authorization / visa status"),
]

FUNNY_ERROR_MESSAGES = [
    "Oopsie doopsie — our AI intern tripped over a semicolon. Mind trying again?",
    "Well, this is embarrassing. Something broke on our end — give it another shot?",
    "Plot twist: the tailoring gremlins won this round. Try again?",
    "Even Claude has off days. Let's give this another go.",
    "Our robots dropped your CV mid-air. One more try, please?",
    "That one's on us, not your CV. Give it another shot.",
    "We hit a snag wrangling the AI. Try again in a bit?",
    "Something short-circuited backstage. It's usually smoother than this — try again?",
    "404: our confidence just went missing. Try again in a moment?",
    "The tailoring gnomes are on strike. Try again shortly?",
]

# Friendly, actionable failure causes shown under the funny message (never raw logs) --
# see _classify_failure. The interrupted one is set when a run is rehydrated after a
# server restart rather than derived from a Claude output tail.
INTERRUPTED_CAUSE = (
    "This run was interrupted when the app restarted. Your progress is saved -- Resume to "
    "continue where it left off."
)


def _classify_failure(tail_output: str, run_dir: Path, job_url: Optional[str]) -> Optional[str]:
    """Turns the raw failure signals we already have (the draft call's stream-json tail,
    plus which artifacts exist on disk) into one plain-English, actionable cause, or None
    when nothing specific is recognizable (the funny message stands alone then). Never
    returns raw logs/tracebacks -- those stay in server stderr."""
    tail = (tail_output or "").lower()
    content_written = (run_dir / "content.json").exists()

    if any(sig in tail for sig in ("session limit", "rate_limit", '"api_error_status":429', "429")):
        return (
            "Claude hit its usage limit. Wait for it to reset, then Resume -- your progress "
            "is saved and won't be re-run from scratch."
        )
    # A blocked/empty job-posting fetch: only meaningful when a URL was given and the draft
    # never got far enough to write content.json.
    if job_url and not content_written and any(
        sig in tail for sig in ("403", "forbidden", "login", "blocked", "could not fetch", "web_fetch")
    ):
        return "Couldn't read the job posting from that URL. Paste the description text instead and try again."
    if content_written:
        # The expensive draft succeeded; whatever failed was a later, cheap phase, so Resume
        # is genuinely cheap here.
        return "Something went wrong finishing your document, but your tailored content is saved -- Resume to retry the last step."
    return None

# The CV itself comes from the saved master CV record now (Read is enough, no raw file to
# parse). No Bash on any of these -- rendering and .docx extraction happen in this backend
# process instead (see the script imports above), not via a Claude tool call.
TAILOR_FROM_MASTER_ALLOWED_TOOLS = "Read Write WebFetch Agent"

# Only granted for a run that opted into "intelligent" cover letter research -- WebSearch
# isn't needed for ordinary tailoring (WebFetch already covers the one known job-posting
# URL), so it stays out of the default tool set.
TAILOR_FROM_MASTER_ALLOWED_TOOLS_WITH_WEB_SEARCH = TAILOR_FROM_MASTER_ALLOWED_TOOLS + " WebSearch"

# Revisions only edit an already-tailored record -- no WebFetch (must not re-fetch/re-derive
# from the job posting) and no Agent (no full independent-review redo), which also
# structurally enforces "targeted edit, not a fresh tailoring pass."
REVISE_ALLOWED_TOOLS = "Read Write"

# The post-render visual-QA/cut-loop calls (see _run_render_qa_loop) never need anything
# beyond reading the rendered result and, if it needs a cut, rewriting the content record.
QA_ALLOWED_TOOLS = "Read Write"

# Belt-and-suspenders on every call site below, even though Bash is already absent from
# every allow-list above: --allowedTools scoping of Bash was verified empirically to not
# be reliably enforced by the CLI, but --disallowedTools is (confirmed: Bash becomes fully
# undiscoverable, not just unlisted). Never remove this without re-verifying that finding.
DISALLOWED_TOOLS = "Bash"

# The job-search helper needs the web tools (discovery + authoritative verification) plus the
# review subagent; still no Bash. See .claude/skills/job-search/.
JOB_SEARCH_ALLOWED_TOOLS = "Read Write WebSearch WebFetch Agent"

# Default source tiers for the job search (mirrors job-search-standards.md). Tier A = company
# ATS boards (public, fetchable, authoritative -> verification happens here). Tier B =
# aggregators (discovery only; a hit here must be confirmed on a Tier-A source or dropped).
JOB_SEARCH_ATS_DOMAINS = [
    "boards.greenhouse.io", "boards-api.greenhouse.io",
    "jobs.lever.co", "api.lever.co",
    "jobs.ashbyhq.com",
    "myworkdayjobs.com",
    "careers.smartrecruiters.com", "jobs.smartrecruiters.com",
    "apply.workable.com",
    "recruitee.com",
    "jobs.personio.com", "jobs.personio.de",
]
JOB_SEARCH_AGGREGATOR_DOMAINS = ["linkedin.com", "indeed.com", "glassdoor.com"]
# Wide, cheap discovery -> verify only a shortlist to keep tokens bounded (see the skill).
JOB_SEARCH_VERIFY_CAP = 25
MAX_JOB_SEARCH_LIST_CHARS = 2_000

CLAUDE_TIMEOUT_SECONDS = 720

# SKILL.md step 8's cut-and-recheck cap: the initial post-render QA check, plus up to this
# many additional cut-then-re-render rounds if it's still over budget.
MAX_QA_ROUNDS = 2

# How many fresh tailoring runs may execute their Claude draft at once. The user can trigger
# more in parallel; extras queue (shown as "Waiting…") and start as slots free. Kept modest
# because each run shells out to `claude -p` on the same subscription, so firing many at once
# would burn the usage limit and fail as a batch. Revisions/resumes aren't gated (cheap,
# user-initiated one at a time).
MAX_CONCURRENT_RUNS = 3
_run_semaphore = threading.Semaphore(MAX_CONCURRENT_RUNS)

EM_DASH = "—"

# Deterministic backup for cv-standards.md / cover-letter-standards.md's Voice sections --
# reproduced here verbatim so prompt wording and this code-level check can't silently drift
# apart. Only phrases with essentially zero legitimate use in a CV/cover letter -- "robust",
# "dynamic", and "leverage" as a noun are deliberately NOT here (see those files' Voice
# sections): they're real technical vocabulary in some contexts, so a blind substring match
# would risk flagging genuinely accurate claims. Judgment calls like that stay with the
# review pass, not this scan.
VOICE_BANNED_PHRASES = [
    "cutting-edge", "cutting edge",
    "seamlessly", "seamless",
    "state-of-the-art", "state of the art",
    "world-class", "world class",
    "best-in-class", "best in class",
    "holistic",
    "synergies", "synergy",
    "passionate about",
    "delve",
    "landscape",
    "furthermore",
    "moreover",
    "proven track record",
    "results-driven", "results driven",
    "detail-oriented", "detail oriented",
    "go-getter", "go getter",
    "self-starter", "self starter",
    "excellent communication skills",
    "excellent interpersonal skills",
    "strong work ethic",
    "think outside the box",
    "wear multiple hats",
    "hard-working professional", "hard working professional",
    "hard-working individual", "hard working individual",
    "leveraged", "leveraging",
]

@contextlib.asynccontextmanager
async def _lifespan(_app: FastAPI):
    db.init_db()
    _rehydrate_interrupted_runs()
    yield


app = FastAPI(lifespan=_lifespan)
app.include_router(auth_router)
app.include_router(master_cv_router)
app.include_router(applications_router)


def _own_run_or_404(run_id: str, user_id: int) -> dict:
    """Look up a run and 404 (not 403) if it doesn't exist or belongs to someone
    else -- avoids confirming a run_id belongs to another user."""
    run = RUNS.get(run_id)
    if run is None or run.get("user_id") != user_id:
        raise HTTPException(404, "Unknown run_id.")
    return run


# run_id -> {"step": str, "percent": int, "done": bool, "error": str|None, "output_file": Path|None,
#            "output_format": str, "download_name": str, "user_id": int}
RUNS: dict[str, dict] = {}


def _sanitize_filename(name: str) -> str:
    name = name.strip()
    if not name:
        return "tailored_cv"
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", name).strip().strip(".")
    return name[:100] or "tailored_cv"


def _filename_name_part(full_name: str) -> str:
    """First + last name token (keeps long names short and recognizable), underscore-joined."""
    tokens = re.findall(r"[A-Za-z0-9]+", full_name or "")
    if not tokens:
        return ""
    picked = tokens if len(tokens) <= 2 else [tokens[0], tokens[-1]]
    return "_".join(picked)


def _filename_slug(s: str) -> str:
    return "_".join(re.findall(r"[A-Za-z0-9]+", s or ""))


def _filename_acronym(s: str) -> str:
    return "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", s or "")).upper()


def _build_default_filename(full_name: str, company: str, role: str, max_len: int = 72) -> str:
    """The standard tailored-CV filename: [name]_[company]_[role]_cv. When that gets too long
    to stay readable, progressively shorten -- acronym the role (usually longest), then the
    company -- rather than truncating mid-word."""
    name = _filename_name_part(full_name)
    company_p = _filename_slug(company)
    role_p = _filename_slug(role)
    if not (name or company_p or role_p):
        return "tailored_cv"

    def assemble(c, r):
        return "_".join(p for p in (name, c, r) if p) + "_cv"

    base = assemble(company_p, role_p)
    if len(base) <= max_len:
        return _sanitize_filename(base)
    role_short = _filename_acronym(role) if len(role_p) > 6 else role_p
    base = assemble(company_p, role_short)
    if len(base) <= max_len:
        return _sanitize_filename(base)
    company_short = _filename_acronym(company) if len(company_p) > 6 else company_p
    return _sanitize_filename(assemble(company_short, role_short))


def _master_full_name(user_id: int) -> str:
    try:
        data = json.loads((master_cv_dir(user_id) / "content.json").read_text())
        return str(data.get("full_name") or "").strip()
    except (json.JSONDecodeError, OSError):
        return ""


def _save_upload_with_limit(upload: UploadFile, dest: Path, max_bytes: int, label: str) -> None:
    size = 0
    with dest.open("wb") as f:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                dest.unlink(missing_ok=True)
                raise HTTPException(400, f"{label} is too large (max {max_bytes // (1024 * 1024)} MB).")
            f.write(chunk)


def _sweep_stale_runs() -> None:
    now = time.time()
    for entry in RUNS_DIR.iterdir():
        if not entry.is_dir():
            continue
        try:
            # A resumable run (failed/interrupted, saved state present) gets the long TTL so
            # it can still be resumed after a usage limit resets; everything else uses the
            # short scratch TTL.
            state = run_state.load_state(entry)
            resumable = state is not None and state.get("phase") != run_state.PHASE_FINALIZED
            ttl = RESUMABLE_RETENTION_SECONDS if resumable else RUN_RETENTION_SECONDS
            if entry.stat().st_mtime < now - ttl:
                shutil.rmtree(entry, ignore_errors=True)
                RUNS.pop(entry.name, None)
        except FileNotFoundError:
            continue


def _delete_run_dir(run_dir: Path) -> None:
    shutil.rmtree(run_dir, ignore_errors=True)


def _rehydrate_run(run_dir: Path, state: dict) -> dict:
    """Rebuilds an in-memory RUNS entry from a persisted run_state.json (see run_state.py),
    for a run interrupted by a restart or left in a failed-but-resumable state. Presented as
    done+resumable so the UI offers Resume; we never try to reconnect to a possibly-orphaned
    subprocess."""
    fmt = state.get("output_format", "pdf")
    names = state.get("path_names", {})

    def p(key):
        name = names.get(key)
        return (run_dir / name) if name else None

    if state.get("phase") == run_state.PHASE_FAILED:
        error = state.get("error") or random.choice(FUNNY_ERROR_MESSAGES)
        cause = state.get("error_cause")
    else:
        error = "This run was interrupted before it finished."
        cause = INTERRUPTED_CAUSE

    return {
        "step": "Interrupted", "percent": 0, "done": True,
        "error": error, "error_cause": cause, "resumable": True,
        "output_file": p("output_file") or (run_dir / f"output.{fmt}"),
        "output_format": fmt,
        "download_name": state.get("download_name", "tailored_cv"),
        "auto_filename": state.get("auto_filename", False),
        "rationale": None, "proc": None, "cancelled": False,
        "revising": False, "revision_count": 0,
        "cover_letter_file": p("cl_output_file"),
        "cv_downloaded": False, "cover_letter_downloaded": False,
        "user_id": state.get("user_id"),
        "run_dir": run_dir,
        "metadata_file": run_dir / "metadata.json",
        "application": None,
        "job_url": state.get("job_url"),
        "job_text": state.get("job_text"),
    }


def _rehydrate_interrupted_runs() -> None:
    """On server startup, re-list every not-yet-finalized run folder still in the scratch
    dir as a resumable RUNS entry. A finalized run's folder has already been moved out of
    RUNS_DIR into permanent storage, so it's never scanned here. This is what fixes the
    orphan-on-restart failure: a run in flight when the server restarts is no longer lost."""
    if not RUNS_DIR.exists():
        return
    for entry in RUNS_DIR.iterdir():
        if not entry.is_dir():
            continue
        state = run_state.load_state(entry)
        if state is None or state.get("phase") == run_state.PHASE_FINALIZED:
            continue
        run_id = state.get("run_id") or entry.name
        try:
            RUNS[run_id] = _rehydrate_run(entry, state)
        except Exception:
            # A malformed state file must never block startup.
            print(f"[cv-tailor] could not rehydrate run {run_id}:\n{traceback.format_exc()}", file=sys.stderr)


def _furthest_completed_phase(state: dict) -> str:
    """The phase a resume should re-enter after: the recorded phase, or (for a failed run)
    the phase that had completed before the failure. Never earlier than 'drafted' -- resume
    from disk only happens when content.json exists."""
    phase = state.get("phase")
    if phase == run_state.PHASE_FAILED:
        return state.get("failed_phase") or run_state.PHASE_DRAFTED
    if phase in run_state.PHASE_ORDER and phase != run_state.PHASE_DRAFTING:
        return phase
    return run_state.PHASE_DRAFTED


# -- deterministic (no-Claude) rendering + the backend-orchestrated visual-QA/cut loop --------
#
# Claude never renders anything itself (no Bash access, see the constants above) -- it only
# ever writes/edits the content JSON (+ HTML for PDF), and this backend renders it. The loop
# below stands in for what used to be one continuous Bash-enabled Claude session running
# SKILL.md steps 6-8 itself; here the backend renders between short, narrowly-scoped Claude
# calls instead.

def _render_output(paths: dict) -> dict:
    """Deterministic backend render of the CV. Returns {"page_count": int} for PDF, or
    {"stats": {...}} (docx_stats proxy numbers) for DOCX -- whatever the QA prompt needs.
    Claude never writes the HTML itself (see fill_html.py) -- it only ever writes
    content_file; this fills the template from that record before rendering."""
    output_format = paths["output_format"]
    content = json.loads(paths["content_file"].read_text())
    if output_format == "pdf":
        html = fill_html.fill_cv_html(content)
        paths["html_file"].write_text(html)
        page_count = render_pdf.render(str(paths["html_file"]), str(paths["output_file"]))
        return {"page_count": page_count}
    render_docx.build(content, str(paths["output_file"]))
    return {"stats": docx_stats.compute_stats(paths["content_file"])}


def _render_cover_letter_output(paths: dict) -> None:
    """Same idea for the optional cover letter -- no iteration loop for it (its review is a
    single self-review before finalizing, per cover-letter-standards.md), just one
    deterministic render right after the CV's."""
    if paths.get("cl_output_file") is None:
        return
    cl_content_file = paths.get("cl_content_file")
    # A cover letter was requested, but Claude may not have produced its content record --
    # e.g. a run interrupted partway (a session/rate limit landing after content.json was
    # written but before cover_letter_content.json), or the skill deciding it couldn't write
    # the letter and noting that in summary.json instead. Either way, honor the "a cover
    # letter issue never blocks the CV" contract: skip the letter, still deliver the CV,
    # rather than crashing the whole run on a missing file.
    if cl_content_file is None or not cl_content_file.exists():
        return
    content = json.loads(cl_content_file.read_text())
    if paths["output_format"] == "pdf":
        html = fill_html.fill_cover_letter_html(content)
        paths["cl_html_file"].write_text(html)
        render_pdf.render(str(paths["cl_html_file"]), str(paths["cl_output_file"]))
    else:
        render_cover_letter_docx.build(content, str(paths["cl_output_file"]))


def _read_target_pages(summary_file: Path) -> int:
    """The candidate's page budget (1 or 2), per cv-standards.md's years-of-relevant-
    experience rule -- Claude determines and writes this during drafting (it's a judgment
    call informed by the CV, not something this backend should decide); this just reads it
    back so the render/QA loop knows what to compare the rendered page count against."""
    try:
        data = json.loads(summary_file.read_text())
        pages = int(data.get("target_pages", 1))
        return pages if pages in (1, 2) else 1
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        return 1


def _append_cut_note(summary_file: Path) -> None:
    """Records that a QA round actually cut content, without spending a whole extra Claude
    call just to update this one changelog line."""
    try:
        data = json.loads(summary_file.read_text())
    except (json.JSONDecodeError, OSError):
        return
    changes = data.get("changes")
    if not isinstance(changes, list):
        changes = []
    changes.append("Further condensed to fit the page budget.")
    data["changes"] = changes
    summary_file.write_text(json.dumps(data))


def _qa_prompt(paths: dict, render_result: dict, target_pages: int, qa_result_file: Path, round_num: int) -> str:
    is_last = round_num >= MAX_QA_ROUNDS
    if paths["output_format"] == "pdf":
        result_line = (
            f"Your CV just rendered to {render_result['page_count']} page(s) (target: {target_pages}). "
            f"Read the rendered PDF at {paths['output_file']} and check it per cv-standards.md step 8's "
            f"QA checklist: spacing, overlap/cutoff text, empty-field gaps, thin extra pages."
        )
        fix_instruction = (
            f"cut per cv-standards.md's cut order and ranking and/or fix the issue, rewrite "
            f"{paths['content_file']} -- the app re-fills the HTML and re-renders after you write it"
        )
    else:
        result_line = (
            f"Length-proxy stats for the rendered DOCX (target: {target_pages} page(s)): "
            f"{render_result['stats']}. Read {paths['content_file']} and sanity-check its structure "
            f"per cv-standards.md step 8 (comfortably-1-page tends to land ~550-650 narrative words / "
            f"<16-18 bullets)."
        )
        fix_instruction = f"cut per cv-standards.md's cut order and ranking, rewrite {paths['content_file']}"

    if is_last:
        verdict_instruction = (
            f"This is the final check (the cut-and-recheck cap is reached) -- if it's still over "
            f"budget or has an issue, {fix_instruction} one last time only if you're confident it "
            f"genuinely helps; otherwise proceed with the best version and note the unresolved "
            f'concern. Either way, write exactly {{"status": "pass"}} to {qa_result_file} when done '
            f"-- do not render yourself."
        )
    else:
        verdict_instruction = (
            f'If it\'s within budget and looks correct, write exactly {{"status": "pass"}} to '
            f"{qa_result_file}. Otherwise, {fix_instruction} -- do not render yourself, the app "
            f're-renders deterministically after you write the file -- then write exactly {{"status": '
            f'"revise"}} to {qa_result_file}.'
        )

    return f"{result_line}\n\n{verdict_instruction}"


def _classify_qa_event(tool_name: str, counts: dict) -> Optional[tuple[str, int]]:
    if tool_name == "Read" and counts["Read"] == 1:
        return ("Reading the rendered result…", 91)
    return None


def _try_css_fit(paths: dict, base_html: str, target_pages: int) -> Optional[tuple[int, int]]:
    """Tries fill_html.CSS_TWEAKS cumulatively against an already-rendered near-miss
    (exactly one page over budget), re-rendering after each, stopping at the first fit.
    Returns (page_count, tweaks_used) on success, None if no tweak combination fit --
    caller is responsible for restoring base_html and re-rendering in that case."""
    for count in range(1, len(fill_html.CSS_TWEAKS) + 1):
        tweaked = fill_html.apply_css_tweaks(base_html, count)
        paths["html_file"].write_text(tweaked)
        page_count = render_pdf.render(str(paths["html_file"]), str(paths["output_file"]))
        if page_count == target_pages:
            return page_count, count
    return None


def _load_json_or_empty(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _find_voice_issues(content) -> dict:
    """Recursively scans every string value in `content` for the em dash character and any
    VOICE_BANNED_PHRASES hit. Returns {"em_dash": bool, "phrases": [...]}; both empty/False
    means clean. Parses the already-loaded JSON structure rather than the file's raw text,
    so it's correct regardless of whether the em dash is stored as a literal UTF-8 character
    or a \\uXXXX escape -- json.loads decodes either the same way."""
    texts = []

    def walk(node):
        if isinstance(node, str):
            texts.append(node)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(content)
    blob = "\n".join(texts)
    blob_lower = blob.lower()
    return {
        "em_dash": EM_DASH in blob,
        "phrases": [p for p in VOICE_BANNED_PHRASES if p in blob_lower],
    }


def _strip_em_dash_in_place(content_file: Path) -> None:
    """Last-resort deterministic guarantee: if an em dash survives every fix round, replace
    it with ', ' throughout the parsed content and rewrite the file. Always grammatically
    safe (unlike deleting a banned phrase, which could break a sentence's grammar entirely),
    so this is the one finding type that gets a hard code-level guarantee rather than a
    logged best-effort. Re-serializes via json.dumps (default ensure_ascii=True, same
    convention _append_cut_note already uses) -- escaping non-ASCII to \\uXXXX round-trips
    losslessly through every reader in this codebase (all use json.loads), so it's safe."""
    data = _load_json_or_empty(content_file)
    if not data:
        return

    def fix(node):
        if isinstance(node, str):
            # Collapses any surrounding whitespace along with the dash itself -- a bare
            # replace(EM_DASH, ", ") would turn the common "X — Y" typographic pattern
            # (spaces on both sides) into "X ,  Y" (stray space before the comma, doubled
            # after).
            return re.sub(r"\s*" + EM_DASH + r"\s*", ", ", node)
        if isinstance(node, dict):
            return {k: fix(v) for k, v in node.items()}
        if isinstance(node, list):
            return [fix(v) for v in node]
        return node

    content_file.write_text(json.dumps(fix(data)))


def _run_voice_guard(run_id: str, run_dir: Path, paths: dict) -> bool:
    """Deterministic backup for cv-standards.md / cover-letter-standards.md's Voice sections:
    a free (no Claude call) scan of the just-drafted content record(s) for an em dash or any
    VOICE_BANNED_PHRASES hit, with up to MAX_QA_ROUNDS short Claude fix rounds if something's
    found. Runs before rendering -- fixing text after render would just mean re-rendering
    anyway. Returns True once clean (whether it started that way or a fix round got it there,
    or the round cap was hit -- see the terminal handling below); False only if a fix-round
    Claude call itself failed at the process level (already marked done+errored by
    run_single_call, same contract as _run_render_qa_loop) -- caller should just return."""
    content_file = paths["content_file"]
    cl_content_file = paths.get("cl_content_file")

    def scan():
        cv_issues = _find_voice_issues(_load_json_or_empty(content_file))
        cl_issues = (
            _find_voice_issues(_load_json_or_empty(cl_content_file))
            if cl_content_file and cl_content_file.exists()
            else {"em_dash": False, "phrases": []}
        )
        return (
            cv_issues["em_dash"] or cl_issues["em_dash"],
            sorted(set(cv_issues["phrases"] + cl_issues["phrases"])),
        )

    has_em_dash, phrases = False, []
    for round_num in range(MAX_QA_ROUNDS + 1):
        has_em_dash, phrases = scan()
        if not has_em_dash and not phrases:
            return True
        if round_num == MAX_QA_ROUNDS:
            break

        percent = max(RUNS[run_id]["percent"], 82)
        RUNS[run_id].update(step="Checking for AI-sounding phrasing…", percent=percent)

        targets = [str(content_file)]
        if cl_content_file and cl_content_file.exists():
            targets.append(str(cl_content_file))

        issue_lines = []
        if has_em_dash:
            issue_lines.append(f"- An em dash ({EM_DASH}) appears somewhere in the file(s).")
        if phrases:
            issue_lines.append(f"- These banned phrases appear: {', '.join(phrases)}.")

        prompt = (
            "The following text must never appear in a tailored CV or cover letter, per "
            "cv-standards.md / cover-letter-standards.md's Voice section:\n"
            + "\n".join(issue_lines)
            + f"\n\nFile(s) to check: {', '.join(targets)}\n\n"
            "Read each file, find every occurrence of the flagged issue(s) above, and rewrite "
            "ONLY the sentence(s) containing them to remove the issue while preserving meaning "
            "and every other rule in cv-standards.md/cover-letter-standards.md. Use a period, "
            "comma, or parentheses in place of any em dash. Do not touch anything else in the "
            "file(s). Overwrite the file(s) in place with the same JSON schema."
        )

        cmd = [
            "claude", "-p", prompt,
            "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", "bypassPermissions",
            "--allowedTools", QA_ALLOWED_TOOLS,
            "--disallowedTools", DISALLOWED_TOOLS,
            "--add-dir", str(run_dir),
        ]
        ok, _ = claude_runner.run_single_call(
            run_id, cmd, PROJECT_ROOT, run_dir, lambda block: None, RUNS,
            timeout_seconds=CLAUDE_TIMEOUT_SECONDS, fail_messages=FUNNY_ERROR_MESSAGES,
        )
        if not ok:
            return False

    # Round cap exhausted and still dirty. Em dash gets a safe deterministic fallback (see
    # _strip_em_dash_in_place); a lingering banned phrase isn't safely auto-fixable, so it's
    # logged and shipped as-is -- the same "best effort, note the residual" pattern the
    # render/QA loop's own final round already uses for a still-over-budget CV.
    if has_em_dash:
        _strip_em_dash_in_place(content_file)
        if cl_content_file and cl_content_file.exists():
            _strip_em_dash_in_place(cl_content_file)
        print(
            f"[cv-tailor] job {run_id}: em dash survived {MAX_QA_ROUNDS} fix rounds, stripped deterministically",
            file=sys.stderr,
        )
    if phrases:
        print(
            f"[cv-tailor] job {run_id}: banned phrase(s) survived {MAX_QA_ROUNDS} fix rounds, shipping as-is: {phrases}",
            file=sys.stderr,
        )
    return True


def _run_render_qa_loop(run_id: str, run_dir: Path, paths: dict) -> bool:
    """Deterministic backend render, then a Claude-driven visual-QA/cut loop (Read/Write
    only, no Bash) capped at MAX_QA_ROUNDS extra rounds, matching SKILL.md step 8's
    iteration cap. Returns True if the loop completed (the final round always ends with a
    "pass" write, whether or not it's genuinely within budget); False if a QA call itself
    failed to run -- the job has already been marked done+errored in that case, so the
    caller should just return."""
    summary_file = paths["summary_file"]
    target_pages = _read_target_pages(summary_file)
    qa_result_file = run_dir / "qa_result.json"

    RUNS[run_id].update(step="Rendering your document…", percent=85)
    render_result = _render_output(paths)
    _render_cover_letter_output(paths)

    if paths["output_format"] == "pdf" and render_result["page_count"] == target_pages + 1:
        # A one-page-over near-miss: try a cosmetic CSS squeeze before spending a whole
        # Claude QA call on what might just be spacing. Only the first two (imperceptible)
        # tweaks skip the QA call outright -- fitting via the harder tweaks (3-5) still
        # gets a real visual check below, since denser content is more likely to look
        # visibly cramped after that much squeeze.
        base_html = paths["html_file"].read_text()
        fit = _try_css_fit(paths, base_html, target_pages)
        if fit is not None:
            page_count, tweaks_used = fit
            render_result = {"page_count": page_count}
            if tweaks_used <= 2:
                return True
        else:
            paths["html_file"].write_text(base_html)
            page_count = render_pdf.render(str(paths["html_file"]), str(paths["output_file"]))
            render_result = {"page_count": page_count}

    for round_num in range(MAX_QA_ROUNDS + 1):
        qa_result_file.unlink(missing_ok=True)
        RUNS[run_id].update(step="Running visual QA…", percent=min(90 + round_num * 2, 97))

        cmd = [
            "claude", "-p",
            _qa_prompt(paths, render_result, target_pages, qa_result_file, round_num),
            "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", "bypassPermissions",
            "--allowedTools", QA_ALLOWED_TOOLS,
            "--disallowedTools", DISALLOWED_TOOLS,
            "--add-dir", str(run_dir),
        ]

        counts = {"Read": 0, "Write": 0}

        def on_event(block, counts=counts):
            if block.get("type") != "tool_use":
                return
            name = block.get("name")
            if name in counts:
                counts[name] += 1
            classification = _classify_qa_event(name, counts)
            if classification:
                step, percent = classification
                if percent > RUNS[run_id]["percent"]:
                    RUNS[run_id].update(step=step, percent=percent)

        ok, tail_output = claude_runner.run_single_call(
            run_id, cmd, PROJECT_ROOT, run_dir, on_event, RUNS,
            timeout_seconds=CLAUDE_TIMEOUT_SECONDS, fail_messages=FUNNY_ERROR_MESSAGES,
        )
        if not ok:
            return False
        if not qa_result_file.exists():
            error_file = run_dir / "error.txt"
            if error_file.exists():
                RUNS[run_id].update(done=True, error=error_file.read_text())
            else:
                print(
                    f"[cv-tailor] job {run_id} QA round {round_num} produced no result:\n{tail_output}",
                    file=sys.stderr,
                )
                RUNS[run_id].update(done=True, error=random.choice(FUNNY_ERROR_MESSAGES))
            return False

        try:
            qa = json.loads(qa_result_file.read_text())
        except (json.JSONDecodeError, OSError):
            qa = {}
        if qa.get("status") != "revise" or round_num == MAX_QA_ROUNDS:
            break

        RUNS[run_id].update(step="Re-rendering…", percent=93)
        render_result = _render_output(paths)
        _render_cover_letter_output(paths)
        _append_cut_note(summary_file)

    return True


def _finalize_application_run(run_id: str) -> None:
    """Runs after a fresh tailoring run finishes successfully, before `done` is set:
    moves the run's output out of ephemeral scratch space (runs/<run_id>/) into the
    user's permanent, organized storage, filing it under company/role if both are
    known or into a dated 'pending' inbox otherwise (see routes_applications.py)."""
    run = RUNS[run_id]
    user_id = run["user_id"]
    run_dir = run["run_dir"]

    company_name = role_name = ""
    metadata_file = run.get("metadata_file")
    if metadata_file and metadata_file.exists():
        try:
            meta = json.loads(metadata_file.read_text())
            company_name = str(meta.get("company_name") or "").strip()
            role_name = str(meta.get("role_name") or "").strip()
        except (json.JSONDecodeError, OSError):
            pass

    # Auto-name the download [name]_[company]_[role]_cv now that the company/role are resolved,
    # unless the candidate typed their own filename.
    if run.get("auto_filename"):
        auto = _build_default_filename(_master_full_name(user_id), company_name, role_name)
        if auto:
            run["download_name"] = auto

    now = datetime.now(timezone.utc)
    attempt_base = now.strftime("%Y-%m-%d_%H-%M-%S")
    created_at = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    is_filed = bool(company_name and role_name)
    if is_filed:
        company_slug, role_slug = _resolve_company_role_slugs(user_id, company_name, role_name)
        dest_root = DATA_DIR / "users" / str(user_id) / "applications" / company_slug / role_slug
    else:
        dest_root = DATA_DIR / "users" / str(user_id) / "pending"

    attempt_slug = attempt_base
    n = 2
    while (dest_root / attempt_slug).exists():
        attempt_slug = f"{attempt_base}-{n}"
        n += 1
    dest = dest_root / attempt_slug

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(run_dir), str(dest))
    except OSError as exc:
        print(f"[cv-tailor] finalize failed for run {run_id}: {exc}", file=sys.stderr)
        run["error"] = "Your CV was generated, but we couldn't save it to your applications folder. Please try again."
        return

    # Repoint every path this run's other endpoints (status/result/preview/revise) rely
    # on -- once this returns, run_dir is no longer under RUNS_DIR.
    run["run_dir"] = dest
    run["output_file"] = dest / run["output_file"].name
    if run.get("cover_letter_file") is not None:
        run["cover_letter_file"] = dest / run["cover_letter_file"].name

    output_format = run["output_format"]
    download_name = run["download_name"]
    has_cover_letter = run.get("cover_letter_file") is not None and run["cover_letter_file"].exists()
    folder_path = str(dest.relative_to(DATA_DIR))

    if is_filed:
        application = db.find_or_create_application(user_id, company_name, company_slug, role_name, role_slug)
        attempt = db.create_application_attempt(
            application["id"], output_format, download_name, folder_path, has_cover_letter, created_at
        )
        # Correct even on a repeat tailoring run for the same role -- keeps the most
        # recently pasted/fetched posting text.
        db.set_application_job_details(application["id"], run.get("job_url"), run.get("job_text"))
        run["application"] = {
            "pending": False, "company_name": company_name, "role_name": role_name, "created_at": created_at,
            # Lets the Tailor success screen offer the same apply/un-apply toggle the
            # Tailored CVs/Applications tables have, without a trip to another tab.
            # attempt_id stays correct across any later revision on this run -- a
            # revision overwrites the file in place, it never creates a new attempt row.
            "application_id": application["id"],
            "attempt_id": attempt["id"],
            "is_applied": application["applied_attempt_id"] == attempt["id"],
        }
    else:
        missing_fields = [name for name, value in (("company_name", company_name), ("role_name", role_name)) if not value]
        pending = db.create_pending_attempt(
            user_id, company_name or None, role_name or None, missing_fields,
            output_format, download_name, folder_path, has_cover_letter, created_at,
        )
        run["application"] = {
            "pending": True, "pending_id": pending["id"], "missing_fields": missing_fields,
            "company_name": company_name or None, "role_name": role_name or None, "created_at": created_at,
        }


def _read_rationale(run_dir: Path) -> Optional[dict]:
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        return None
    try:
        data = json.loads(summary_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    requirements = []
    for r in data.get("requirements", []):
        if not isinstance(r, dict):
            continue
        name = str(r.get("name", "")).strip()
        status = r.get("status")
        if not name or status not in ("matched", "listed_only", "missing"):
            continue
        requirements.append({
            "name": name, "status": status, "evidence": str(r.get("evidence", "")).strip(),
        })
    return {
        "summary": str(data.get("summary", "")).strip(),
        "changes": [str(c).strip() for c in data.get("changes", []) if str(c).strip()],
        "review_note": str(data.get("review_note", "")).strip(),
        "requirements": requirements,
    }


def _sample_recent_content(user_id: int) -> list[dict]:
    """The user's most-recent tailored content records (up to FIELD_MEMORY_SAMPLE_SIZE),
    newest first -- used to learn which optional contact fields they keep including. Reads
    both filed applications and the pending inbox."""
    user_root = DATA_DIR / "users" / str(user_id)
    candidates = list((user_root / "applications").glob("*/*/*/content.json"))
    candidates += list((user_root / "pending").glob("*/content.json"))
    candidates.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)

    records = []
    for path in candidates[:FIELD_MEMORY_SAMPLE_SIZE]:
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict):
            records.append(data)
    return records


def _recent_field_memory(user_id: int) -> list[dict]:
    """Learns, from the last few tailored CVs, which optional contact fields the candidate
    keeps including (and their most-recent value), so the tailor form can offer them as
    pre-checked, editable toggles instead of making the candidate retype them every run.
    Returns [{field, label, value, default_checked}] -- one entry per field seen in >=1
    recent run (photo: if a saved master photo exists). Empty when there's no history."""
    records = _sample_recent_content(user_id)
    suggestions = []

    for field, label in FIELD_MEMORY_FIELDS:
        present = [str(r.get(field, "")).strip() for r in records if str(r.get(field, "")).strip()]
        if not present:
            continue
        suggestions.append({
            "field": field,
            "label": label,
            "value": present[0],  # most recent non-empty value (records are newest-first)
            "default_checked": len(present) * 2 >= len(records),  # majority of the sample
        })

    # Photo is special: no text value, and it's offered only if the candidate has a saved
    # master photo to use. Default-checked by the same majority rule over recent runs.
    if next((master_cv_dir(user_id)).glob("photo.*"), None) is not None:
        photo_present = sum(1 for r in records if r.get("photo_path"))
        suggestions.append({
            "field": "photo",
            "label": "Include a photo",
            "value": "",
            "default_checked": bool(records) and photo_present * 2 >= len(records),
        })

    return suggestions


@app.get("/api/tailor/field-memory")
def tailor_field_memory(user=Depends(auth.get_current_user)):
    return {"fields": _recent_field_memory(user["id"])}


def _parse_field_overrides(raw: str) -> dict:
    """Parses the tailor form's field-memory toggles (a JSON list of
    {field, include, value}) into {field: {"include": bool, "value": str}} for known fields
    only. Values are the candidate's own CV data (same trust level as `notes`), so they're
    just length-capped and control-char-stripped, not otherwise validated."""
    known = {field for field, _ in FIELD_MEMORY_FIELDS} | {"photo"}
    result: dict = {}
    if not raw.strip():
        return result
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return result
    if not isinstance(data, list):
        return result
    for item in data:
        if not isinstance(item, dict):
            continue
        field = item.get("field")
        if field not in known or field in result:
            continue
        value = re.sub(r"[\x00-\x1f\x7f]", " ", str(item.get("value", ""))).strip()[:MAX_FIELD_VALUE_CHARS]
        result[field] = {"include": bool(item.get("include")), "value": value}
    return result


def _build_contact_directive(overrides: dict) -> str:
    """Turns the parsed field-memory toggles into an explicit prompt directive telling the
    draft which contact fields to include (with what exact value) or omit -- the mechanism
    that replaces the candidate re-typing phone/visa/etc. into the notes box every run. Only
    covers the value fields here; `photo` is handled via photo_line. Empty string when no
    value-field toggles were provided."""
    lines = []
    for field, label in FIELD_MEMORY_FIELDS:
        override = overrides.get(field)
        if override is None:
            continue
        if override["include"]:
            if override["value"]:
                lines.append(f'- {label}: include exactly "{override["value"]}".')
            else:
                lines.append(f"- {label}: include (use the value already in the master record).")
        else:
            lines.append(f"- {label}: omit entirely -- do not put a {label.lower()} anywhere in the CV.")
    if not lines:
        return ""
    return (
        "Contact/identity fields for this CV -- set the tailored content record's contact fields to "
        "match these exactly, overriding the master record where they differ. These are the "
        "candidate's own current details:\n" + "\n".join(lines)
    )


@app.get("/", response_class=HTMLResponse)
def index():
    return (PROJECT_ROOT / "static" / "index.html").read_text()


app.mount("/static/js", StaticFiles(directory=PROJECT_ROOT / "static" / "js"), name="static-js")


@app.post("/api/tailor/start")
async def start_tailor(
    job_url: str = Form(default=""),
    job_text: str = Form(default=""),
    output_format: str = Form(default="pdf"),
    filename: str = Form(default=""),
    notes: str = Form(default=""),
    include_cover_letter: str = Form(default="false"),
    cover_letter_notes: str = Form(default=""),
    cover_letter_template: Optional[UploadFile] = File(default=None),
    intelligent_cover_letter: str = Form(default="false"),
    company_name: str = Form(default=""),
    role_name: str = Form(default=""),
    field_overrides: str = Form(default=""),
    user=Depends(auth.require_master_cv),
):
    job_url = job_url.strip()
    job_text = job_text.strip()
    output_format = output_format.strip().lower()
    notes = notes.strip()
    company_name = company_name.strip()
    role_name = role_name.strip()
    wants_cover_letter = include_cover_letter.strip().lower() in ("true", "1", "on", "yes")
    # Only meaningful (and only granted the extra WebSearch tool) when a cover letter was
    # actually requested -- meaningless on its own.
    wants_company_research = wants_cover_letter and intelligent_cover_letter.strip().lower() in ("true", "1", "on", "yes")
    cover_letter_notes = cover_letter_notes.strip()
    # No explicit filename -> auto-name it [name]_[company]_[role]_cv once the company/role are
    # known (at finalize). A placeholder stands in until then.
    auto_filename = not filename.strip()
    download_name = _sanitize_filename(filename)
    overrides = _parse_field_overrides(field_overrides)
    if not job_url and not job_text:
        raise HTTPException(400, "Provide a job posting URL or pasted job description text.")
    if output_format not in ("pdf", "docx"):
        raise HTTPException(400, "output_format must be 'pdf' or 'docx'.")
    if len(job_text) > MAX_JOB_TEXT_CHARS:
        raise HTTPException(400, f"Job description text is too long (max {MAX_JOB_TEXT_CHARS:,} characters).")
    if len(notes) > MAX_NOTES_CHARS:
        raise HTTPException(400, f"Additional comments are too long (max {MAX_NOTES_CHARS:,} characters).")
    if len(cover_letter_notes) > MAX_COVER_LETTER_NOTES_CHARS:
        raise HTTPException(400, f"Cover letter guidance is too long (max {MAX_COVER_LETTER_NOTES_CHARS:,} characters).")

    cover_letter_template_suffix = None
    if cover_letter_template is not None and cover_letter_template.filename:
        cover_letter_template_suffix = Path(cover_letter_template.filename).suffix.lower()
        if cover_letter_template_suffix not in ALLOWED_COVER_LETTER_TEMPLATE_EXTENSIONS:
            raise HTTPException(400, "Cover letter template must be a PDF, .docx, or .txt file.")

    _sweep_stale_runs()

    run_id = uuid.uuid4().hex
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True)

    master_dir = master_cv_dir(user["id"])
    master_content_file = master_dir / "content.json"

    # Photo inclusion: the saved master photo is used unless a field-memory toggle explicitly
    # turned it off for this run. When no photo toggle was sent (older client / no field
    # memory), keep the prior default of including it if one exists.
    photo_line = ""
    master_photo = next(master_dir.glob("photo.*"), None)
    photo_override = overrides.get("photo")
    include_photo = photo_override["include"] if photo_override is not None else True
    if master_photo is not None and include_photo:
        photo_line = f"Photo to include: {master_photo}"
    elif master_photo is not None and not include_photo:
        photo_line = "Do not include a photo in this CV, even though one is on file."

    output_file = run_dir / f"output.{output_format}"
    content_file = run_dir / "content.json"
    html_file = run_dir / "output.html"
    summary_file = run_dir / "summary.json"
    metadata_file = run_dir / "metadata.json"
    cover_letter_file = run_dir / f"cover_letter.{output_format}"

    # Company/role are used to file the finished CV into a company/role folder
    # once tailoring succeeds (see _finalize_application_run). If the candidate
    # gave both explicitly, use them verbatim and skip asking Claude to guess.
    if company_name and role_name:
        metadata_file.write_text(json.dumps({"company_name": company_name, "role_name": role_name}))
        metadata_block = ""
    else:
        if company_name:
            override_guidance = f'the company is "{company_name}" -- use that verbatim; determine only the role/title from the job posting'
        elif role_name:
            override_guidance = f'the role/title is "{role_name}" -- use that verbatim; determine only the company from the job posting'
        else:
            override_guidance = "determine both from the job posting"
        metadata_block = f"""
Also determine this application's company and role so it can be filed automatically: {override_guidance}.
Write exactly this JSON to {metadata_file}: {{"company_name": "", "role_name": ""}}. Do this early,
right after getting the job description, before tailoring the content. If you genuinely cannot
determine a field from the job posting, leave it as "" rather than guessing -- do not invent a
plausible-looking company or role name."""

    if job_url and job_text:
        job_line = (
            f"Job posting URL: {job_url}\n"
            f"Candidate also pasted this job description text as a fallback/reference -- use it if "
            f"the URL fails or returns too little, and cross-check against it otherwise:\n{job_text}"
        )
    elif job_url:
        job_line = f"Job posting URL: {job_url}"
    else:
        job_line = f"Job description text:\n{job_text}"
    notes_line = (
        f"Additional notes/comments from the candidate (apply these to the CV content itself as "
        f"you tailor it — e.g. update contact/location fields or add the detail somewhere visible "
        f"in the output, not just as background context):\n{notes}"
        if notes else ""
    )
    contact_line = _build_contact_directive(overrides)

    cover_letter_block = ""
    cover_letter_content_file = cover_letter_html_file = None
    if wants_cover_letter:
        cover_letter_content_file = run_dir / "cover_letter_content.json"
        cover_letter_html_file = run_dir / "cover_letter.html"

        template_line = ""
        if cover_letter_template is not None and cover_letter_template.filename:
            template_suffix = cover_letter_template_suffix
            template_path = run_dir / f"cover_letter_template{template_suffix}"
            _save_upload_with_limit(cover_letter_template, template_path, MAX_CV_BYTES, "Cover letter template")
            if template_suffix == ".docx":
                # Extracted here (not by Claude via Bash) -- see the script-import note above.
                template_text_path = run_dir / "cover_letter_template.txt"
                template_text_path.write_text(extract_docx.extract(str(template_path)))
                template_read_line = f"Read tool on {template_text_path} (already extracted from the original .docx)"
            else:
                template_read_line = f"Read tool directly on {template_path}"
            template_line = (
                f"The candidate provided a previous cover letter as a style/structure reference -- "
                f"{template_read_line}. Use it as your model for voice, structure, and opening style, "
                f"but rewrite the substance for this specific job posting and this candidate's actual "
                f"tailored background -- never carry over the old letter's company name, role, or "
                f"specific claims into the new one."
            )

        guidance_line = ""
        if cover_letter_notes:
            guidance_line = (
                f"Candidate's guidance on what this cover letter should emphasize or include:\n"
                f"{cover_letter_notes}\n"
                f"Treat this as direction on tone/content/emphasis, not literal text to insert verbatim "
                f"unless it reads as an explicit quote."
            )

        research_line = ""
        if wants_company_research:
            research_line = """
Before writing, research the hiring company so the letter genuinely reflects it, not generic
phrasing:
- Identify the company name from the job posting.
- Use WebSearch and/or WebFetch to find the company's own About/Careers/Values/mission content,
  and/or recent, genuinely notable news or public statements about what they prioritize or how
  they describe their culture.
- Pull out 1-3 concrete, specific signals actually stated by or about the company -- not generic
  corporate language every company uses ("fast-paced", "innovative") unless the company itself
  frames it distinctively.
- Weave at most 1-2 of the strongest signals in naturally (e.g. connecting something genuine in
  the candidate's own background/working style to something the company has actually said about
  itself) -- don't name-drop the research process, and never state a specific claim about the
  company you didn't actually find. If research turns up little of substance, write a strong
  letter from the job posting alone rather than inventing culture claims.
- Add one short bullet to the summary JSON's "changes" list noting what was found and used (e.g.
  "Referenced the company's stated focus on sustainability from their careers page"), or skip
  that bullet entirely if research didn't surface anything worth using."""

        cover_letter_block = f"""
Also write a tailored cover letter for this same role, using the reviewed CV content record at
{content_file} and the job posting above -- reuse full_name/email/phone/location from that
record rather than re-reading the raw CV file.
{template_line}
{guidance_line}
{research_line}

Apply cover-letter-standards.md (same directory as SKILL.md) in full for content rules -- it's the
actual rule set, not background reading. Before finalizing, run its mandatory "Review" section
against your draft and fix anything that fails.

Structure it as this JSON: {{"full_name": "", "contact_line": "email | phone | location, same
style as the CV contact line", "date": "today's date, e.g. 'March 3, 2026'", "salutation": "",
"paragraphs": ["", "..."], "closing": "e.g. 'Sincerely,'", "signature_name": ""}}

Write that JSON to exactly {cover_letter_content_file} -- do not render it yourself and do not write
HTML, the app fills the template and renders it after you finish.

If the cover letter can't be produced for some reason, note it in {summary_file}'s "review_note"
and skip it -- don't fail the whole run over it."""

    prompt = f"""Use the tailor-cv skill's process to tailor this CV to this job posting.

Master CV content record (already parsed and reviewed once -- read this directly as step 3's
output; do NOT re-read a raw CV file): {master_content_file}
{job_line}
{photo_line}
{notes_line}
{contact_line}
Output format: {output_format}

Follow the skill's steps from step 2 through step 5 (get job description, tailor content,
independent review pass with fixes) -- skip step 1 and step 3's extraction, using the master CV
content record above as step 3's output directly. Do not render or QA anything yourself; the app
renders and runs the visual-QA/cut loop separately after you finish. Do not overwrite
{master_content_file} itself -- write your tailored result only to {content_file}. Also write the
reviewed content JSON record (after step 5's review) to exactly {content_file} regardless of
output format -- this is the source of truth for rendering and any later revision, including the
HTML the app fills and renders for PDF output; you never write HTML yourself.

Also determine, per cv-standards.md's years-of-relevant-experience rule, this candidate's page
budget for this application: 1 (under 10 years of relevant experience) or 2 (10+ years).

Also write the step 9 report as JSON to exactly {summary_file}, with this schema:
{{"summary": "one or two sentence overview of the tailoring approach", "changes": ["short bullet
describing one change, written for the candidate to read, under ~100 characters", "..."],
"review_note": "short note on what the independent review pass caught and fixed, or empty string
if nothing needed fixing", "target_pages": 1 or 2 as determined above,
"requirements": [{{"name": "requirement extracted in step 4", "status": "matched" (woven into a
bullet) | "listed_only" (in Skills but not demonstrated) | "missing" (no material for it),
"evidence": "short excerpt of the bullet that demonstrates it -- omit for listed_only/missing"}},
"..."] -- a rollup of the relevance-mapping already done in step 5, not a new judgment call}}
{metadata_block}
{cover_letter_block}

If you cannot proceed (e.g. the job URL is blocked and no job text was given), write a short
explanation to {run_dir / 'error.txt'} instead, and stop."""

    RUNS[run_id] = {
        "step": "Starting…",
        "percent": 3,
        "done": False,
        "error": None,
        "error_cause": None,
        "resumable": False,
        "queued": False,
        "output_file": output_file,
        "output_format": output_format,
        "download_name": download_name,
        "auto_filename": auto_filename,
        "rationale": None,
        "proc": None,
        "cancelled": False,
        "revising": False,
        "revision_count": 0,
        "cover_letter_file": cover_letter_file,
        "cv_downloaded": False,
        "cover_letter_downloaded": False,
        "user_id": user["id"],
        "run_dir": run_dir,
        "metadata_file": metadata_file,
        "application": None,
        # Already known server-side before the subprocess even starts -- persisted verbatim
        # in _finalize_application_run once the application row exists, no AI involved.
        "job_url": job_url or None,
        "job_text": job_text or None,
    }

    paths = {
        "output_file": output_file,
        "output_format": output_format,
        "content_file": content_file,
        "html_file": html_file if output_format == "pdf" else None,
        "summary_file": summary_file,
        "cl_content_file": cover_letter_content_file,
        "cl_html_file": cover_letter_html_file if output_format == "pdf" else None,
        "cl_output_file": cover_letter_file if wants_cover_letter else None,
    }

    run_allowed_tools = (
        TAILOR_FROM_MASTER_ALLOWED_TOOLS_WITH_WEB_SEARCH if wants_company_research else TAILOR_FROM_MASTER_ALLOWED_TOOLS
    )

    # Persist everything needed to rehydrate this run after a server restart and resume it
    # from its last completed phase without re-deriving anything (see run_state.py). The
    # built draft prompt is stored verbatim so a resume that must re-run the draft doesn't
    # have to rebuild it; it references absolute run-dir paths that stay valid.
    _persist_run_state(
        run_dir, run_id, user["id"], paths, prompt, run_allowed_tools,
        job_url or None, job_text or None, wants_cover_letter, download_name, auto_filename,
    )

    thread = threading.Thread(
        target=_run_claude, args=(run_id, prompt, run_dir, paths, run_allowed_tools), daemon=True
    )
    thread.start()

    return {"run_id": run_id}


def _persist_run_state(run_dir, run_id, user_id, paths, prompt, allowed_tools,
                       job_url, job_text, wants_cover_letter, download_name, auto_filename=False):
    """Snapshots a run's identity + inputs + phase to runs/<id>/run_state.json. `paths`
    Path values are stored as bare filenames (rebuilt on load via
    run_state.paths_from_state) since the run folder itself is what moves/persists."""
    path_names = {
        key: (value.name if isinstance(value, Path) else None)
        for key, value in paths.items()
        if key != "output_format"
    }
    run_state.write_state(run_dir, {
        "run_id": run_id,
        "user_id": user_id,
        "output_format": paths["output_format"],
        "download_name": download_name,
        "auto_filename": auto_filename,
        "job_url": job_url,
        "job_text": job_text,
        "wants_cover_letter": wants_cover_letter,
        "path_names": path_names,
        "draft_prompt": prompt,
        "allowed_tools": allowed_tools,
        "phase": run_state.PHASE_DRAFTING,
        "failed_phase": None,
        "error": None,
        "error_cause": None,
    })


def _media_type(output_format: str) -> str:
    return "application/pdf" if output_format == "pdf" else \
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@app.get("/api/tailor/{run_id}/status")
def tailor_status(run_id: str, user=Depends(auth.get_current_user)):
    run = _own_run_or_404(run_id, user["id"])
    return {
        "step": run["step"],
        "percent": run["percent"],
        "done": run["done"],
        "error": run["error"],
        "error_cause": run.get("error_cause"),
        "resumable": run.get("resumable", False),
        "queued": run.get("queued", False),
        "output_format": run.get("output_format"),
        "download_name": run.get("download_name"),
        "rationale": run.get("rationale"),
        "revision_count": run.get("revision_count", 0),
        "max_revisions": MAX_REVISIONS,
        "has_cover_letter": run.get("cover_letter_file") is not None and run["cover_letter_file"].exists(),
        "application": run.get("application"),
        "kind": run.get("kind", "tailor"),
        "results_count": run.get("results_count"),
    }


@app.get("/api/tailor/{run_id}/result")
def tailor_result(run_id: str, user=Depends(auth.get_current_user)):
    run = _own_run_or_404(run_id, user["id"])
    if not run["done"]:
        raise HTTPException(409, "Not finished yet.")
    if run["error"]:
        raise HTTPException(422, run["error"])
    output_format = run["output_format"]
    run["cv_downloaded"] = True
    return FileResponse(
        run["output_file"],
        filename=f"{run['download_name']}.{output_format}",
        media_type=_media_type(output_format),
    )


@app.get("/api/tailor/{run_id}/cover-letter/result")
def cover_letter_result(run_id: str, user=Depends(auth.get_current_user)):
    run = _own_run_or_404(run_id, user["id"])
    if not run["done"]:
        raise HTTPException(409, "Not finished yet.")
    if run["error"]:
        raise HTTPException(422, run["error"])
    cover_letter_file = run.get("cover_letter_file")
    if cover_letter_file is None or not cover_letter_file.exists():
        raise HTTPException(404, "No cover letter was generated for this run.")
    output_format = run["output_format"]
    run["cover_letter_downloaded"] = True
    return FileResponse(
        cover_letter_file,
        filename=f"{run['download_name']}_cover_letter.{output_format}",
        media_type=_media_type(output_format),
    )


@app.get("/api/tailor/{run_id}/preview")
def tailor_preview(run_id: str, user=Depends(auth.get_current_user)):
    run = _own_run_or_404(run_id, user["id"])
    if not run["done"]:
        raise HTTPException(409, "Not finished yet.")
    if run["error"]:
        raise HTTPException(422, run["error"])
    if run["output_format"] != "pdf":
        raise HTTPException(404, "Preview is only available for PDF output.")
    # No `filename` -> no Content-Disposition header, so the browser renders it inline
    # instead of downloading it. Doesn't trigger cleanup -- the real /result call does.
    return FileResponse(run["output_file"], media_type="application/pdf")


@app.get("/api/tailor/{run_id}/cover-letter/preview")
def cover_letter_preview(run_id: str, user=Depends(auth.get_current_user)):
    run = _own_run_or_404(run_id, user["id"])
    if not run["done"]:
        raise HTTPException(409, "Not finished yet.")
    if run["error"]:
        raise HTTPException(422, run["error"])
    if run["output_format"] != "pdf":
        raise HTTPException(404, "Preview is only available for PDF output.")
    cover_letter_file = run.get("cover_letter_file")
    if cover_letter_file is None or not cover_letter_file.exists():
        raise HTTPException(404, "No cover letter was generated for this run.")
    return FileResponse(cover_letter_file, media_type="application/pdf")


@app.post("/api/tailor/{run_id}/revise")
def revise_tailor(run_id: str, feedback: str = Form(...), user=Depends(auth.get_current_user)):
    feedback = feedback.strip()
    if not feedback:
        raise HTTPException(400, "Describe what you'd like changed.")
    if len(feedback) > MAX_FEEDBACK_CHARS:
        raise HTTPException(400, f"Feedback is too long (max {MAX_FEEDBACK_CHARS:,} characters).")

    run = _own_run_or_404(run_id, user["id"])
    if not run["done"] or run["error"]:
        raise HTTPException(409, "This run isn't ready to revise.")
    revision_count = run.get("revision_count", 0)
    if revision_count >= MAX_REVISIONS:
        raise HTTPException(400, f"You've used all {MAX_REVISIONS} revisions for this CV.")

    run_dir = run.get("run_dir") or (RUNS_DIR / run_id)
    content_file = run_dir / "content.json"
    output_file = run["output_file"]
    output_format = run["output_format"]
    if not content_file.exists() or not output_file.exists():
        raise HTTPException(409, "Nothing to revise — the previous result is missing.")

    (run_dir / "error.txt").unlink(missing_ok=True)
    html_file = run_dir / "output.html"
    summary_file = run_dir / "summary.json"

    cover_letter_file = run.get("cover_letter_file")
    cover_letter_content_file = run_dir / "cover_letter_content.json"
    has_cover_letter = cover_letter_content_file.exists() and cover_letter_file is not None
    cover_letter_html_file = run_dir / "cover_letter.html"
    cover_letter_revise_block = ""
    if has_cover_letter:
        cover_letter_revise_block = f"""

A cover letter was also generated for this run, with its own content record at
{cover_letter_content_file}. If (and only if) the feedback above relates to the cover letter,
apply the same targeted-edit approach there: read {cover_letter_content_file}, edit only what
the feedback addresses, overwrite it -- do not render it or write HTML yourself, the app fills the
template and renders it after you finish. Leave the cover letter file(s) untouched if the feedback
is only about the CV."""

    prompt = f"""You are revising a previously tailored CV based on specific candidate feedback. Do
not start over or re-tailor from scratch -- make the minimum edit that satisfies the feedback below
and leave everything else exactly as it was.

The current reviewed content record is at: {content_file}
Candidate feedback on the result:
{feedback}

Steps:
1. Read {content_file}.
2. Apply ONLY the change(s) described in the feedback above. Do not restructure, reorder,
   re-tailor, or rewrite anything the feedback doesn't address. If the feedback is ambiguous, take
   the most conservative, literal interpretation. Never invent experience, skills, titles, or
   claims not already present in the record or explicitly stated in the feedback -- if the
   feedback asks for something unsupported, write a short explanation to {run_dir / 'error.txt'}
   instead of guessing, and stop without touching any other file.
3. Overwrite {content_file} with the updated record (same JSON schema as before). Do not render,
   write HTML, or QA anything yourself -- the app fills the template, renders, and runs the
   visual-QA/cut loop separately after you finish.
4. Update {summary_file}: append one short bullet to "changes" describing what this revision did
   (don't remove or reword the existing entries, and don't touch "target_pages"), and update
   "review_note" only if this revision fixes something it previously flagged.
{cover_letter_revise_block}

If you cannot proceed, write a short explanation to {run_dir / 'error.txt'} instead of touching
{content_file}, and stop."""

    run.update(
        step="Applying your feedback…",
        percent=8,
        done=False,
        error=None,
        cancelled=False,
        revising=True,
        revision_count=revision_count + 1,
    )

    paths = {
        "output_file": output_file,
        "output_format": output_format,
        "content_file": content_file,
        "html_file": html_file if output_format == "pdf" else None,
        "summary_file": summary_file,
        "cl_content_file": cover_letter_content_file if has_cover_letter else None,
        "cl_html_file": cover_letter_html_file if has_cover_letter and output_format == "pdf" else None,
        "cl_output_file": cover_letter_file if has_cover_letter else None,
    }

    thread = threading.Thread(
        target=_run_revision, args=(run_id, prompt, run_dir, paths), daemon=True
    )
    thread.start()

    return {"run_id": run_id}


@app.post("/api/tailor/{run_id}/cancel")
def cancel_tailor(run_id: str, user=Depends(auth.get_current_user)):
    run = _own_run_or_404(run_id, user["id"])
    if run["done"]:
        return {"cancelled": False}
    proc = run.get("proc")
    if proc is not None:
        proc.kill()
    if run.get("revising"):
        # Mid-revision: the previous, still-valid output/content/summary are untouched on
        # disk -- keep them and leave the run usable (no persistent error, so /result,
        # /preview, and a future /revise all keep working against that prior version).
        # The frontend's cancel handler is what tells the user this specific attempt
        # was cancelled; it doesn't rely on this response for that.
        run.update(done=True, error=None, cancelled=True, revising=False)
    else:
        run.update(done=True, error="Cancelled.", cancelled=True)
        _delete_run_dir(run.get("run_dir") or (RUNS_DIR / run_id))
    return {"cancelled": True}


@app.post("/api/tailor/{run_id}/resume")
def resume_tailor(run_id: str, user=Depends(auth.get_current_user)):
    """Resumes a failed/interrupted run from its last completed phase. If the draft already
    produced content.json (the common, token-saving case), the expensive draft+review call
    is skipped and only the remaining cheap phases re-run; otherwise the stored draft prompt
    is re-run first."""
    run = _own_run_or_404(run_id, user["id"])
    if not run.get("resumable"):
        raise HTTPException(409, "This run can't be resumed.")
    if not run["done"]:
        raise HTTPException(409, "This run is still in progress.")

    run_dir = run.get("run_dir") or (RUNS_DIR / run_id)
    state = run_state.load_state(run_dir)
    if state is None:
        raise HTTPException(409, "This run's saved state is missing, so it can't be resumed. Start a new run.")

    paths = run_state.paths_from_state(run_dir, state)
    run.update(
        done=False, error=None, error_cause=None, resumable=False, cancelled=False,
        step="Resuming…", percent=8,
    )

    if paths["content_file"].exists():
        start_phase = _furthest_completed_phase(state)
        run_state.update_phase(run_dir, start_phase)
        thread = threading.Thread(
            target=_run_post_draft_phases, args=(run_id, run_dir, paths, start_phase, True), daemon=True
        )
    else:
        # No content.json -- the draft never completed (e.g. a usage limit mid-draft), so it
        # must be re-run. The stored prompt references still-valid absolute run-dir paths.
        run_state.update_phase(run_dir, run_state.PHASE_DRAFTING)
        thread = threading.Thread(
            target=_run_claude, args=(run_id, state["draft_prompt"], run_dir, paths, state.get("allowed_tools", TAILOR_FROM_MASTER_ALLOWED_TOOLS)), daemon=True
        )
    thread.start()
    return {"run_id": run_id}


@app.post("/api/tailor/{run_id}/discard")
def discard_tailor(run_id: str, user=Depends(auth.get_current_user)):
    """Deletes a failed/interrupted run the user doesn't want to resume, so kept-for-resume
    folders don't accumulate."""
    run = _own_run_or_404(run_id, user["id"])
    _delete_run_dir(run.get("run_dir") or (RUNS_DIR / run_id))
    RUNS.pop(run_id, None)
    return {"discarded": True}


@app.post("/api/tailor/{run_id}/unfile")
def unfile_tailor(run_id: str, user=Depends(auth.get_current_user)):
    """'Remove from Applications': a finished run is auto-filed into the user's Applications;
    this lets them take it back out if they didn't actually want it tracked there. Deletes the
    application/attempt DB rows and moves the folder out of applications/ into unfiled/ so the
    tailored CV stays downloadable from the result view -- it's just no longer an application."""
    run = _own_run_or_404(run_id, user["id"])
    app_info = run.get("application")
    if not app_info or app_info.get("pending") or app_info.get("removed") or not app_info.get("attempt_id"):
        raise HTTPException(409, "This run isn't filed in Applications.")

    result = db.delete_application_attempt(app_info["attempt_id"], user["id"])
    if result is None:
        raise HTTPException(404, "That application entry no longer exists.")

    # Keep the files (so the CV is still downloadable here) but move them out of applications/.
    src = run.get("run_dir") or (RUNS_DIR / run_id)
    dest = DATA_DIR / "users" / str(user["id"]) / "unfiled" / run_id
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        shutil.move(str(src), str(dest))
        run["run_dir"] = dest
        run["output_file"] = dest / run["output_file"].name
        if run.get("cover_letter_file") is not None:
            run["cover_letter_file"] = dest / run["cover_letter_file"].name
    except OSError as exc:
        # The DB rows are already gone (so it's untracked regardless); if the move fails, just
        # keep serving downloads from the original location.
        print(f"[cv-tailor] unfile move failed for {run_id}: {exc}", file=sys.stderr)

    run["application"] = {
        "removed": True,
        "company_name": app_info.get("company_name"),
        "role_name": app_info.get("role_name"),
    }
    return {"application": run["application"]}


def _run_summary(run_id: str, run: dict) -> dict:
    """Compact per-run status for the dock/monitor -- everything the frontend needs to render
    a chip and its list row without a separate /status call per run."""
    company = role = None
    metadata_file = run.get("metadata_file")
    if metadata_file and metadata_file.exists():
        try:
            meta = json.loads(metadata_file.read_text())
            company = (meta.get("company_name") or "").strip() or None
            role = (meta.get("role_name") or "").strip() or None
        except (json.JSONDecodeError, OSError):
            pass
    cover_letter_file = run.get("cover_letter_file")
    return {
        "run_id": run_id,
        "kind": run.get("kind", "tailor"),   # "tailor" | "search"
        "label": run.get("label"),           # dock label for a search run (no company/role)
        "company_name": company,
        "role_name": role,
        "step": run.get("step"),
        "percent": run.get("percent", 0),
        "done": run.get("done", False),
        "error": run.get("error"),
        "error_cause": run.get("error_cause"),
        "resumable": run.get("resumable", False),
        "queued": run.get("queued", False),
        "output_format": run.get("output_format"),
        "download_name": run.get("download_name"),
        "has_cover_letter": cover_letter_file is not None and getattr(cover_letter_file, "exists", lambda: False)(),
        "application": run.get("application"),
        "results_count": run.get("results_count"),
    }


@app.get("/api/tailor/runs")
def list_runs(user=Depends(auth.get_current_user)):
    """Every run of the current user that's still worth showing in the dock: in-progress or
    queued (not done), plus failed/interrupted (resumable). Successful-and-finalized runs are
    intentionally excluded -- those live in the Tailored CVs / Applications tabs. Used to
    rebuild the dock after a page reload."""
    return {"runs": [
        _run_summary(run_id, run)
        for run_id, run in list(RUNS.items())
        if run.get("user_id") == user["id"] and (not run.get("done") or run.get("resumable"))
    ]}


@app.get("/api/tailor/resumable")
def list_resumable(user=Depends(auth.get_current_user)):
    """Failed/interrupted runs the current user can resume (a subset of /runs)."""
    return {"runs": [
        _run_summary(run_id, run)
        for run_id, run in list(RUNS.items())
        if run.get("user_id") == user["id"] and run.get("resumable")
    ]}


# -- Job search (a background "search" run type; results persisted to job_leads) ----------

def _job_dedup_key(company: str, role: str, url: str) -> str:
    """Normalized fingerprint so the same opening found again (or across searches) upserts
    rather than duplicating. URL usually pins it; company+role guards near-identical reposts."""
    norm = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())
    return f"{norm(company)}|{norm(role)}|{norm(url)}"


def _read_job_results(results_file: Path) -> list:
    """Parse + validate the job-search skill's results.json into clean lead dicts. Anything
    malformed or missing a company/role/url is dropped -- the zero-false-results bar means we'd
    rather show fewer than surface a broken row."""
    try:
        data = json.loads(results_file.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    jobs = data.get("jobs") if isinstance(data, dict) else data
    if not isinstance(jobs, list):
        return []
    out = []
    for j in jobs:
        if not isinstance(j, dict):
            continue
        company = str(j.get("company", "")).strip()
        role = str(j.get("role", "")).strip()
        url = str(j.get("url", "")).strip()
        if not (company and role and url):
            continue
        requirements = []
        for r in j.get("requirements", []):
            if not isinstance(r, dict):
                continue
            name = str(r.get("name", "")).strip()
            status = r.get("status")
            if not name or status not in ("matched", "listed_only", "missing"):
                continue
            requirements.append({"name": name, "status": status, "evidence": str(r.get("evidence", "")).strip()})
        # Fit score (0-100) and level drive the descending sort below. Tolerate a missing/garbage
        # score (default 0) and a level outside the vocabulary (default '') rather than dropping.
        try:
            match_score = int(round(float(j.get("match_score"))))
        except (TypeError, ValueError):
            match_score = 0
        match_score = max(0, min(100, match_score))
        match_level = j.get("match_level")
        if match_level not in ("strong", "possible", "stretch"):
            match_level = ""
        out.append({
            "dedup_key": _job_dedup_key(company, role, url),
            "company_name": company, "role_name": role,
            "location": str(j.get("location", "")).strip(),
            "work_model": str(j.get("work_model", "")).strip(),
            "url": url, "source": str(j.get("source", "")).strip(),
            "posted_date": str(j.get("posted_date", "")).strip(),
            "match_score": match_score, "match_level": match_level,
            "why_fits": str(j.get("why_fits", "")).strip(),
            "requirements": requirements,
        })
    # Best fits first, so the list leads with the strongest matches.
    out.sort(key=lambda x: x["match_score"], reverse=True)
    return out


def _classify_job_search_event(tool_name: str, counts: dict) -> Optional[tuple[str, int]]:
    if tool_name == "Read" and counts["Read"] == 1:
        return ("Reading your CV…", 10)
    if tool_name == "WebSearch":
        return ("Searching for openings…", 35)
    if tool_name == "WebFetch":
        return ("Verifying real, live postings…", 65)
    if tool_name == "Agent":
        return ("Double-checking every result…", 85)
    return None


def _run_job_search(run_id: str, prompt: str, run_dir: Path, results_file: Path):
    """Background job-search orchestration: one Claude call (WebSearch/WebFetch/Agent, no Bash)
    following the job-search skill, which writes verified strong matches to results.json. On
    success the results are upserted into job_leads (persisted, deduped) and the run is marked
    done. Search runs aren't resumable -- re-running is cheap and idempotent."""
    cmd = [
        "claude", "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", "bypassPermissions",
        "--allowedTools", JOB_SEARCH_ALLOWED_TOOLS,
        "--disallowedTools", DISALLOWED_TOOLS,
        "--add-dir", str(run_dir),
    ]
    counts = {"Read": 0, "WebSearch": 0, "WebFetch": 0, "Agent": 0}

    def on_event(block):
        if block.get("type") != "tool_use":
            return
        name = block.get("name")
        if name in counts:
            counts[name] += 1
        classification = _classify_job_search_event(name, counts)
        if classification:
            step, percent = classification
            if percent > RUNS[run_id]["percent"]:
                RUNS[run_id].update(step=step, percent=percent)

    ok, tail_output = claude_runner.run_single_call(
        run_id, cmd, PROJECT_ROOT, run_dir, on_event, RUNS,
        timeout_seconds=CLAUDE_TIMEOUT_SECONDS, fail_messages=FUNNY_ERROR_MESSAGES,
    )
    if not ok:
        return

    try:
        if not results_file.exists():
            error_file = run_dir / "error.txt"
            if error_file.exists():
                RUNS[run_id].update(done=True, error=error_file.read_text())
            else:
                print(f"[cv-tailor] job search {run_id} produced no results file:\n{tail_output}", file=sys.stderr)
                RUNS[run_id].update(done=True, error=random.choice(FUNNY_ERROR_MESSAGES))
            return

        leads = _read_job_results(results_file)
        user_id = RUNS[run_id]["user_id"]
        for lead in leads:
            try:
                db.upsert_job_lead(user_id, lead)
            except Exception:
                print(f"[cv-tailor] job search {run_id}: failed to save a lead:\n{traceback.format_exc()}", file=sys.stderr)
        RUNS[run_id].update(step="Done!", percent=100, done=True, error=None, results_count=len(leads))
    except Exception:
        _fail_run(run_id, "during job-search finalize", resumable=False)


@app.post("/api/job-search/start")
async def start_job_search(
    location: str = Form(...),
    titles: str = Form(default=""),
    companies: str = Form(default=""),
    sites: str = Form(default=""),
    user=Depends(auth.require_master_cv),
):
    location = location.strip()
    titles = titles.strip()
    companies = companies.strip()
    sites = sites.strip()
    if not location:
        raise HTTPException(400, "A target location is required to search.")
    for label, value in (("Titles", titles), ("Companies", companies), ("Sites", sites)):
        if len(value) > MAX_JOB_SEARCH_LIST_CHARS:
            raise HTTPException(400, f"{label} is too long (max {MAX_JOB_SEARCH_LIST_CHARS:,} characters).")

    _sweep_stale_runs()

    run_id = uuid.uuid4().hex
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True)

    master_content_file = master_cv_dir(user["id"]) / "content.json"
    results_file = run_dir / "results.json"
    error_file = run_dir / "error.txt"

    titles_line = f"Target titles the candidate specified: {titles}" if titles else \
        "The candidate did not specify titles -- derive realistic target titles from the CV."
    companies_line = f"Target companies to prioritize: {companies}" if companies else ""
    sites_line = f"Also include these sites in discovery: {sites}" if sites else ""

    prompt = f"""Use the job-search skill's process to find real, currently-open job openings this
candidate has a plausible interview shot at -- strong AND partial matches, not only perfect fits.
Apply job-search-standards.md in full -- the zero-false-results verification protocol is mandatory; a
returned job that isn't a real, live posting is a failure.

Master CV content record (already parsed -- read this directly, do NOT re-parse a raw CV):
{master_content_file}
Target location: {location}
{titles_line}
{companies_line}
{sites_line}

Default discovery sources: company ATS boards ({', '.join(JOB_SEARCH_ATS_DOMAINS)}) for both
discovery and authoritative verification, plus the aggregators ({', '.join(JOB_SEARCH_AGGREGATOR_DOMAINS)})
for discovery only (an aggregator hit must be confirmed on a company's own ATS/careers source or
dropped). Cast a WIDE net with WebSearch, but WebFetch-verify only the ~{JOB_SEARCH_VERIFY_CAP} most
promising candidates to keep token use low.

Score each verified job's CV fit (match_score 0-100, match_level strong/possible/stretch) per
job-search-standards.md, keep everything scoring >= 35 (partial matches included), and sort the list
by match_score, highest first. Write it as JSON to exactly {results_file}, matching
job-search-standards.md's result schema (each object includes match_score, match_level, why_fits),
as: {{"jobs": [ ...each job object... ], "searched": {{"titles": [...], "location": "{location}",
"candidates_found": N, "verified": M}}}}. Include the honest counts; if nothing survives
verification, write {{"jobs": [], "searched": {{...}}}} rather than lowering the reality bar.

If you cannot proceed, write a short explanation to {error_file} instead, and stop."""

    label_titles = titles or "matching roles"
    RUNS[run_id] = {
        "kind": "search",
        "label": f"{label_titles} in {location}",
        "step": "Starting…",
        "percent": 3,
        "done": False,
        "error": None,
        "error_cause": None,
        "resumable": False,
        "queued": False,
        "user_id": user["id"],
        "run_dir": run_dir,
        "results_count": None,
        # Fields the shared status/summary readers touch -- search runs have no CV output.
        "output_format": None,
        "download_name": None,
        "cover_letter_file": None,
        "rationale": None,
        "revision_count": 0,
        "application": None,
        "proc": None,
        "cancelled": False,
        "revising": False,
        "metadata_file": None,
    }

    thread = threading.Thread(
        target=_run_job_search, args=(run_id, prompt, run_dir, results_file), daemon=True
    )
    thread.start()
    return {"run_id": run_id}


@app.get("/api/job-leads")
def list_job_leads_endpoint(user=Depends(auth.get_current_user)):
    return {"leads": db.list_job_leads(user["id"])}


@app.post("/api/job-leads/{lead_id}/dismiss")
def dismiss_job_lead_endpoint(lead_id: int, user=Depends(auth.get_current_user)):
    if not db.dismiss_job_lead(lead_id, user["id"]):
        raise HTTPException(404, "Lead not found.")
    return {"dismissed": True}


# Ordered so later matches only apply once earlier ones have already been seen once each,
# via the seen-counts state carried across the run in _run_claude.
def _classify_event(tool_name: str, counts: dict) -> Optional[tuple[str, int]]:
    if tool_name == "Read" and counts["Read"] == 1:
        return ("Reading your CV…", 12)
    if tool_name == "WebFetch":
        return ("Fetching the job posting…", 28)
    if tool_name == "WebSearch":
        return ("Researching the company…", 30)
    if tool_name == "Agent":
        if counts["Agent"] == 1:
            return ("Running an independent review pass…", 58)
        return ("Double-checking the revised draft…", 74)
    if tool_name == "Write":
        return ("Preparing the tailored document…", 80)
    return None


def _classify_revision_event(tool_name: str, counts: dict) -> Optional[tuple[str, int]]:
    if tool_name == "Read" and counts["Read"] == 1:
        return ("Reading your feedback into context…", 20)
    if tool_name == "Write":
        return ("Applying your changes…", 60)
    return None


def _fail_run(run_id: str, context: str, resumable: bool = True) -> None:
    """Marks a run done+errored after an unexpected exception in this backend's own
    post-draft orchestration (rendering, the QA loop, finalizing) -- none of that runs
    inside claude_runner's subprocess-streaming try/except, so without this a bug here
    (a corrupt content.json, a Playwright/render crash, a disk error) would otherwise
    leave the job stuck at done=False forever, exactly what A1 fixed for the streaming
    part alone."""
    job = RUNS.get(run_id)
    if job is None or job.get("cancelled"):
        return
    print(f"[cv-tailor] job {run_id} crashed {context}:\n{traceback.format_exc()}", file=sys.stderr)
    run_dir = job.get("run_dir") or (RUNS_DIR / run_id)
    funny = random.choice(FUNNY_ERROR_MESSAGES)
    if resumable:
        cause = _classify_failure("", run_dir, job.get("job_url"))
        job.update(done=True, error=funny, error_cause=cause, resumable=True)
        # Record the failure for resume; content.json exists here (a backend crash is always
        # post-draft), so the recorded phase is at least drafted.
        run_state.update_phase(run_dir, run_state.PHASE_FAILED, error=funny, error_cause=cause)
    else:
        job.update(done=True, error=funny)


def _run_claude(run_id: str, draft_prompt: str, run_dir: Path, paths: dict, allowed_tools: str = TAILOR_FROM_MASTER_ALLOWED_TOOLS):
    """Concurrency gate around a fresh tailoring run (see MAX_CONCURRENT_RUNS): the user can
    trigger runs in parallel, but only this many execute their Claude draft at once so the
    subscription's usage limit isn't hammered. Marks the run `queued` until a slot frees, then
    runs the real pipeline. Bails cleanly (without ever starting Claude) if the run is
    cancelled while still queued."""
    job = RUNS.get(run_id)
    if job is None or job.get("cancelled"):
        return
    job.update(queued=True, step="Waiting for a free slot…")
    # Poll-acquire (rather than a blocking acquire) so a cancel while queued breaks the wait.
    while not _run_semaphore.acquire(timeout=0.5):
        job = RUNS.get(run_id)
        if job is None or job.get("cancelled"):
            return
    try:
        job = RUNS.get(run_id)
        if job is None or job.get("cancelled"):
            return
        job.update(queued=False, step="Starting…", percent=max(job.get("percent", 0), 3))
        _run_claude_inner(run_id, draft_prompt, run_dir, paths, allowed_tools)
    finally:
        _run_semaphore.release()


def _run_claude_inner(run_id: str, draft_prompt: str, run_dir: Path, paths: dict, allowed_tools: str = TAILOR_FROM_MASTER_ALLOWED_TOOLS):
    """Orchestrates a fresh tailoring run: one Claude call to draft+review (no Bash --
    it only reads/writes files), then a backend-rendered, backend-orchestrated visual-QA/
    cut loop (see _run_render_qa_loop), then the existing move-to-permanent-storage finalize."""
    cmd = [
        "claude", "-p", draft_prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", "bypassPermissions",
        "--allowedTools", allowed_tools,
        "--disallowedTools", DISALLOWED_TOOLS,
        "--add-dir", str(run_dir),
    ]

    counts = {"Read": 0, "WebFetch": 0, "WebSearch": 0, "Agent": 0, "Write": 0}
    state = {"tailoring_step_shown": False, "seen_fetch_or_read": False}

    def on_event(block):
        btype = block.get("type")
        if btype == "tool_use":
            name = block.get("name")
            if name in counts:
                counts[name] += 1
                if name in ("Read", "WebFetch"):
                    state["seen_fetch_or_read"] = True
            classification = _classify_event(name, counts)
            if classification:
                step, percent = classification
                if percent > RUNS[run_id]["percent"]:
                    RUNS[run_id].update(step=step, percent=percent)
        elif btype == "text" and state["seen_fetch_or_read"] and not state["tailoring_step_shown"] and counts["Agent"] == 0:
            if block.get("text", "").strip():
                state["tailoring_step_shown"] = True
                if 42 > RUNS[run_id]["percent"]:
                    RUNS[run_id].update(step="Tailoring your content to the role…", percent=42)

    ok, tail_output = claude_runner.run_single_call(
        run_id, cmd, PROJECT_ROOT, run_dir, on_event, RUNS,
        timeout_seconds=CLAUDE_TIMEOUT_SECONDS, fail_messages=FUNNY_ERROR_MESSAGES,
    )
    if not ok:
        return

    if not paths["content_file"].exists():
        error_file = run_dir / "error.txt"
        if error_file.exists():
            # A deliberate, actionable message the skill wrote itself -- show it as-is, and
            # it's not resumable in the cheap sense (no draft to skip), but keep the run so a
            # fresh attempt can reuse the folder.
            RUNS[run_id].update(done=True, error=error_file.read_text(), resumable=True)
            run_state.update_phase(run_dir, run_state.PHASE_FAILED, failed_phase=run_state.PHASE_DRAFTING,
                                   error=error_file.read_text())
        else:
            print(f"[cv-tailor] job {run_id} failed without an output file:\n{tail_output}", file=sys.stderr)
            cause = _classify_failure(tail_output, run_dir, RUNS[run_id].get("job_url"))
            funny = random.choice(FUNNY_ERROR_MESSAGES)
            RUNS[run_id].update(done=True, error=funny, error_cause=cause, resumable=True)
            run_state.update_phase(run_dir, run_state.PHASE_FAILED, failed_phase=run_state.PHASE_DRAFTING,
                                   error=funny, error_cause=cause)
        return

    run_state.update_phase(run_dir, run_state.PHASE_DRAFTED)
    _run_post_draft_phases(run_id, run_dir, paths, start_phase=run_state.PHASE_DRAFTED, finalize=True)


def _summary_has_requirements(summary_file: Path) -> bool:
    try:
        data = json.loads(summary_file.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    reqs = data.get("requirements")
    return isinstance(reqs, list) and len(reqs) > 0


def _ensure_summary_report(run_id: str, run_dir: Path, paths: dict) -> bool:
    """A run whose draft was interrupted after writing content.json but before the step-9
    report reaches resume/render with a missing (or requirement-less) summary.json -- so the
    success view loses its 'what changed' summary and requirement-coverage checklist. When
    that happens, regenerate JUST the step-9 report from the already-tailored content record
    plus the job posting (a cheap Read/Write call, not the whole draft). A no-op for a normal
    run whose summary is already complete, so it costs nothing in the common case.

    Best-effort: the report is a nice-to-have, so a failure to regenerate never fails the run
    (the CV itself is fine). Returns False only if the run was cancelled mid-regeneration."""
    summary_file = paths.get("summary_file")
    content_file = paths.get("content_file")
    if summary_file is None or content_file is None:
        return True
    if not content_file.exists() or _summary_has_requirements(summary_file):
        return True

    job = RUNS.get(run_id, {})
    job_text = job.get("job_text")
    job_url = job.get("job_url")
    if job_url and job_text:
        jd_line = f"Job posting URL: {job_url}\nPasted job description:\n{job_text}"
    elif job_url:
        jd_line = f"Job posting URL: {job_url}"
    elif job_text:
        jd_line = f"Job description:\n{job_text}"
    else:
        return True  # no job posting on hand to map requirements against -- ship without it

    RUNS[run_id].update(step="Rebuilding the summary…", percent=max(RUNS[run_id].get("percent", 0), 80))
    # Only re-fetch the posting (WebFetch) when a URL is all we have; otherwise Read/Write only.
    allowed = "Read Write WebFetch" if (job_url and not job_text) else "Read Write"
    prompt = f"""The reviewed, already-tailored CV content record is at {content_file}.
Job posting:
{jd_line}

Produce the "what changed" report for this CV (it is already tailored -- do not re-tailor or modify
{content_file} or any other file, and do not render anything). Read {content_file}, extract the job
posting's 8-10 most critical requirements, and for each decide, from what's actually in the content
record, whether it is "matched" (demonstrated inside an experience bullet), "listed_only" (present in
Skills but not demonstrated in a bullet), or "missing" (no material for it).

Write exactly this JSON to {summary_file}:
{{"summary": "one or two sentence overview of how this CV is tailored to the role", "changes": ["a
few short bullets a candidate can read describing the tailoring", "..."], "review_note": "",
"target_pages": 1 or 2, "requirements": [{{"name": "requirement", "status": "matched"|"listed_only"|
"missing", "evidence": "short excerpt of the bullet that demonstrates it -- omit for listed_only/
missing"}}, "..."]}}"""

    cmd = [
        "claude", "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", "bypassPermissions",
        "--allowedTools", allowed,
        "--disallowedTools", DISALLOWED_TOOLS,
        "--add-dir", str(run_dir),
    ]
    ok, _ = claude_runner.run_single_call(
        run_id, cmd, PROJECT_ROOT, run_dir, lambda block: None, RUNS,
        timeout_seconds=CLAUDE_TIMEOUT_SECONDS, fail_messages=FUNNY_ERROR_MESSAGES,
    )
    if not ok:
        job = RUNS.get(run_id)
        if job is None or job.get("cancelled"):
            return False  # genuinely stop -- the run was cancelled
        # The regen call failed but the CV is fine: clear the errored state run_single_call set
        # and continue without the checklist rather than failing the whole resume.
        job.update(done=False, error=None, error_cause=None)
    return True


def _run_post_draft_phases(run_id: str, run_dir: Path, paths: dict, start_phase: str, finalize: bool):
    """Runs the cheap phases after a successful draft -- voice guard, render/QA, and
    (for a fresh run) finalize -- updating run_state after each so a later failure or a
    restart can resume from the furthest completed phase. `start_phase` lets a resume skip
    phases already done on disk. None of this runs inside claude_runner's subprocess
    try/except, so it carries its own safety net (see _fail_run)."""
    order = run_state.PHASE_ORDER
    start_idx = order.index(start_phase)
    # A revision (finalize=False) runs against the already-finalized permanent-storage folder,
    # so its failures must NOT be marked resumable -- resuming would try to re-finalize/re-move
    # that folder. Only fresh runs are resumable (revisions are cheap and out of scope here).
    resumable = finalize
    try:
        # Fresh runs already have a complete step-9 report; a resumed run whose draft was
        # interrupted before writing it may not -- regenerate it so the success view's
        # "what changed" + requirement checklist isn't lost. No-op (no Claude call) when the
        # summary is already complete.
        if finalize and not _ensure_summary_report(run_id, run_dir, paths):
            return

        if start_idx < order.index(run_state.PHASE_VOICE_CHECKED):
            if not _run_voice_guard(run_id, run_dir, paths):
                if resumable:
                    _mark_resumable_failure(run_id, run_dir, run_state.PHASE_DRAFTED)
                return
            run_state.update_phase(run_dir, run_state.PHASE_VOICE_CHECKED)

        if start_idx < order.index(run_state.PHASE_RENDERED):
            if not _run_render_qa_loop(run_id, run_dir, paths):
                if resumable:
                    _mark_resumable_failure(run_id, run_dir, run_state.PHASE_VOICE_CHECKED)
                return
            run_state.update_phase(run_dir, run_state.PHASE_RENDERED)

        if not finalize:
            # Revision path: the output stays in place, no move-to-permanent-storage.
            RUNS[run_id]["rationale"] = _read_rationale(run_dir)
            RUNS[run_id].update(step="Done!", percent=100, done=True, error=None)
            return

        _finalize_application_run(run_id)
        if RUNS[run_id].get("error"):
            # The move to permanent storage failed -- keep the run resumable so finalize can
            # be retried (the rendered output is still on disk).
            RUNS[run_id].update(done=True, resumable=True,
                                error_cause="Your CV was generated but couldn't be filed. Resume to retry.")
            run_state.update_phase(run_dir, run_state.PHASE_FAILED, failed_phase=run_state.PHASE_RENDERED)
            return
        # run_dir was moved by finalize -- record the terminal phase at its new location.
        run_state.update_phase(RUNS[run_id]["run_dir"], run_state.PHASE_FINALIZED)
        RUNS[run_id]["rationale"] = _read_rationale(RUNS[run_id]["run_dir"])
        RUNS[run_id].update(step="Done!", percent=100, done=True, error=None)
    except Exception:
        _fail_run(run_id, "during rendering/QA/finalize", resumable=resumable)


def _mark_resumable_failure(run_id: str, run_dir: Path, completed_phase: str) -> None:
    """Adds the resumable/cause markers to a run that a post-draft phase already marked
    done+errored (via run_single_call's funny message), without overwriting that message.
    Post-draft failures always have content.json on disk, so they're cheaply resumable."""
    run = RUNS.get(run_id)
    if run is None or run.get("cancelled"):
        return
    cause = run.get("error_cause") or _classify_failure("", run_dir, run.get("job_url"))
    run["error_cause"] = cause
    run["resumable"] = True
    run_state.update_phase(run_dir, run_state.PHASE_FAILED, failed_phase=completed_phase,
                           error=run.get("error"), error_cause=cause)


def _run_revision(run_id: str, edit_prompt: str, run_dir: Path, paths: dict):
    """Orchestrates a revision: one Claude call to apply the targeted edit (Read/Write
    only), then the same backend render + visual-QA/cut loop fresh tailoring uses."""
    try:
        _run_revision_inner(run_id, edit_prompt, run_dir, paths)
    finally:
        # Always reset, regardless of which branch/return below was hit -- this used to
        # happen unconditionally after the single call to run_process_and_finalize; the
        # multi-call orchestration below has several exit points, so a try/finally is what
        # actually guarantees that instead of a reset duplicated at each return site.
        if run_id in RUNS:
            RUNS[run_id]["revising"] = False


def _run_revision_inner(run_id: str, edit_prompt: str, run_dir: Path, paths: dict):
    content_file = paths["content_file"]
    cl_content_file = paths["cl_content_file"]
    # Captured before the attempt starts: content_file already exists (it's the previous,
    # still-valid result), so "did this attempt do anything" has to be judged by whether
    # it actually got rewritten, not just whether it exists. Feedback might target only the
    # cover letter, so a change to either content record (not just the CV's) counts as a
    # real attempt.
    pre_mtime = content_file.stat().st_mtime if content_file.exists() else None
    cl_pre_mtime = cl_content_file.stat().st_mtime if cl_content_file is not None and cl_content_file.exists() else None

    cmd = [
        "claude", "-p", edit_prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", "bypassPermissions",
        "--allowedTools", REVISE_ALLOWED_TOOLS,
        "--disallowedTools", DISALLOWED_TOOLS,
        "--add-dir", str(run_dir),
    ]

    counts = {"Read": 0, "Write": 0}

    def on_event(block):
        if block.get("type") != "tool_use":
            return
        name = block.get("name")
        if name in counts:
            counts[name] += 1
        classification = _classify_revision_event(name, counts)
        if classification:
            step, percent = classification
            if percent > RUNS[run_id]["percent"]:
                RUNS[run_id].update(step=step, percent=percent)

    ok, tail_output = claude_runner.run_single_call(
        run_id, cmd, PROJECT_ROOT, run_dir, on_event, RUNS,
        timeout_seconds=CLAUDE_TIMEOUT_SECONDS, fail_messages=FUNNY_ERROR_MESSAGES,
    )
    if not ok:
        return

    cv_changed = content_file.exists() and (pre_mtime is None or content_file.stat().st_mtime > pre_mtime)
    cl_changed = (
        cl_content_file is not None and cl_content_file.exists()
        and (cl_pre_mtime is None or cl_content_file.stat().st_mtime > cl_pre_mtime)
    )
    if not (cv_changed or cl_changed):
        error_file = run_dir / "error.txt"
        if error_file.exists():
            RUNS[run_id].update(done=True, error=error_file.read_text())
        else:
            print(f"[cv-tailor] job {run_id} revision made no change:\n{tail_output}", file=sys.stderr)
            RUNS[run_id].update(done=True, error=random.choice(FUNNY_ERROR_MESSAGES))
        return

    # Reuse the same post-draft phase runner fresh tailoring uses (voice guard -> render/QA),
    # but without finalize -- a revision edits an already-filed record in place. The content
    # record was just rewritten, so this is effectively a fresh "drafted" state to run forward
    # from; failures here are handled with the same resumable markers.
    _run_post_draft_phases(run_id, run_dir, paths, start_phase=run_state.PHASE_DRAFTED, finalize=False)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8420)
