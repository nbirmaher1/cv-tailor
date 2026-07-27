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
import json
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from starlette.background import BackgroundTask

PROJECT_ROOT = Path(__file__).resolve().parent
RUNS_DIR = PROJECT_ROOT / "runs"
RUNS_DIR.mkdir(exist_ok=True)

# Abandoned/errored run folders (uploaded CV, photo, any partial output) are swept on
# a best-effort basis at the start of each new request -- fine for a single-user local
# tool; a hosted multi-tenant version would want a real TTL/lifecycle policy instead.
RUN_RETENTION_SECONDS = 2 * 60 * 60

MAX_CV_BYTES = 10 * 1024 * 1024
MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_JOB_TEXT_CHARS = 50_000
MAX_NOTES_CHARS = 2_000
ALLOWED_CV_EXTENSIONS = {".pdf", ".docx"}
ALLOWED_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

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

VENV_PY = PROJECT_ROOT / "venv" / "bin" / "python3"
EXTRACT_SCRIPT = PROJECT_ROOT / "scripts" / "extract_docx.py"
RENDER_PDF_SCRIPT = PROJECT_ROOT / "scripts" / "render_pdf.py"
RENDER_DOCX_SCRIPT = PROJECT_ROOT / "scripts" / "render_docx.py"
DOCX_STATS_SCRIPT = PROJECT_ROOT / "scripts" / "docx_stats.py"

ALLOWED_TOOLS = (
    "Read Write WebFetch Agent "
    f'Bash({VENV_PY} {EXTRACT_SCRIPT} *) '
    f'Bash({VENV_PY} {RENDER_PDF_SCRIPT} *) '
    f'Bash({VENV_PY} {RENDER_DOCX_SCRIPT} *) '
    f'Bash({VENV_PY} {DOCX_STATS_SCRIPT} *)'
)

CLAUDE_TIMEOUT_SECONDS = 720

app = FastAPI()

# run_id -> {"step": str, "percent": int, "done": bool, "error": str|None, "output_file": Path|None,
#            "output_format": str, "download_name": str}
RUNS: dict[str, dict] = {}


def _sanitize_filename(name: str) -> str:
    name = name.strip()
    if not name:
        return "tailored_cv"
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", name).strip().strip(".")
    return name[:100] or "tailored_cv"


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
    cutoff = time.time() - RUN_RETENTION_SECONDS
    for entry in RUNS_DIR.iterdir():
        if not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                RUNS.pop(entry.name, None)
        except FileNotFoundError:
            continue


def _delete_run_dir(run_dir: Path) -> None:
    shutil.rmtree(run_dir, ignore_errors=True)


def _cleanup_run(run_id: str, run_dir: Path) -> None:
    _delete_run_dir(run_dir)
    RUNS.pop(run_id, None)


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
    return {
        "summary": str(data.get("summary", "")).strip(),
        "changes": [str(c).strip() for c in data.get("changes", []) if str(c).strip()],
        "review_note": str(data.get("review_note", "")).strip(),
    }


@app.get("/", response_class=HTMLResponse)
def index():
    return (PROJECT_ROOT / "static" / "index.html").read_text()


@app.post("/api/tailor/start")
async def start_tailor(
    cv_file: UploadFile,
    job_url: str = Form(default=""),
    job_text: str = Form(default=""),
    output_format: str = Form(default="pdf"),
    filename: str = Form(default=""),
    notes: str = Form(default=""),
    photo: Optional[UploadFile] = File(default=None),
):
    job_url = job_url.strip()
    job_text = job_text.strip()
    output_format = output_format.strip().lower()
    notes = notes.strip()
    download_name = _sanitize_filename(filename)
    if not job_url and not job_text:
        raise HTTPException(400, "Provide a job posting URL or pasted job description text.")
    if output_format not in ("pdf", "docx"):
        raise HTTPException(400, "output_format must be 'pdf' or 'docx'.")
    if len(job_text) > MAX_JOB_TEXT_CHARS:
        raise HTTPException(400, f"Job description text is too long (max {MAX_JOB_TEXT_CHARS:,} characters).")
    if len(notes) > MAX_NOTES_CHARS:
        raise HTTPException(400, f"Additional comments are too long (max {MAX_NOTES_CHARS:,} characters).")

    cv_suffix = Path(cv_file.filename or "").suffix.lower()
    if cv_suffix not in ALLOWED_CV_EXTENSIONS:
        raise HTTPException(400, "CV must be a PDF or .docx file.")
    photo_suffix = None
    if photo is not None and photo.filename:
        photo_suffix = Path(photo.filename).suffix.lower()
        if photo_suffix not in ALLOWED_PHOTO_EXTENSIONS:
            raise HTTPException(400, "Photo must be a JPG, PNG, WEBP, or GIF file.")

    _sweep_stale_runs()

    run_id = uuid.uuid4().hex
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True)

    cv_path = run_dir / f"cv{cv_suffix}"
    _save_upload_with_limit(cv_file, cv_path, MAX_CV_BYTES, "CV file")

    photo_line = ""
    if photo is not None and photo.filename:
        photo_path = run_dir / f"photo{photo_suffix}"
        _save_upload_with_limit(photo, photo_path, MAX_PHOTO_BYTES, "Photo")
        photo_line = f"Photo to include: {photo_path}"

    output_file = run_dir / f"output.{output_format}"
    job_line = f"Job posting URL: {job_url}" if job_url else f"Job description text:\n{job_text}"
    notes_line = (
        f"Additional notes/comments from the candidate (apply these to the CV content itself per "
        f"the skill's step 3 — e.g. update contact/location fields or add the detail somewhere "
        f"visible in the output, not just as background context):\n{notes}"
        if notes else ""
    )

    summary_file = run_dir / "summary.json"
    prompt = f"""Use the tailor-cv skill's process to tailor this CV to this job posting.

CV file: {cv_path}
{job_line}
{photo_line}
{notes_line}
Output format: {output_format}

Follow the skill's steps exactly (extract CV into the canonical JSON content record, get job
description, tailor content, independent review pass with fixes, render, visually/structurally
QA the result), with one override: instead of the default output/<slug> naming, write the final
{output_format.upper()} to exactly {output_file} (and any intermediate .html/.json file next to
it in {run_dir}).

Also write the step 9 report as JSON to exactly {summary_file}, with this schema:
{{"summary": "one or two sentence overview of the tailoring approach", "changes": ["short bullet
describing one change, written for the candidate to read, under ~100 characters", "..."],
"review_note": "short note on what the independent review pass caught and fixed, or empty string
if nothing needed fixing"}}

If you cannot proceed (e.g. the job URL is blocked and no job text was given), write a short
explanation to {run_dir / 'error.txt'} instead of an output file, and stop."""

    RUNS[run_id] = {
        "step": "Starting…",
        "percent": 3,
        "done": False,
        "error": None,
        "output_file": output_file,
        "output_format": output_format,
        "download_name": download_name,
        "rationale": None,
        "proc": None,
        "cancelled": False,
    }

    thread = threading.Thread(target=_run_claude, args=(run_id, prompt, run_dir, output_file), daemon=True)
    thread.start()

    return {"run_id": run_id}


@app.get("/api/tailor/{run_id}/status")
def tailor_status(run_id: str):
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(404, "Unknown run_id.")
    return {
        "step": run["step"],
        "percent": run["percent"],
        "done": run["done"],
        "error": run["error"],
        "rationale": run.get("rationale"),
    }


@app.get("/api/tailor/{run_id}/result")
def tailor_result(run_id: str):
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(404, "Unknown run_id.")
    if not run["done"]:
        raise HTTPException(409, "Not finished yet.")
    if run["error"]:
        raise HTTPException(422, run["error"])
    output_format = run["output_format"]
    media_type = "application/pdf" if output_format == "pdf" else \
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return FileResponse(
        run["output_file"],
        filename=f"{run['download_name']}.{output_format}",
        media_type=media_type,
        background=BackgroundTask(_cleanup_run, run_id, RUNS_DIR / run_id),
    )


@app.get("/api/tailor/{run_id}/preview")
def tailor_preview(run_id: str):
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(404, "Unknown run_id.")
    if not run["done"]:
        raise HTTPException(409, "Not finished yet.")
    if run["error"]:
        raise HTTPException(422, run["error"])
    if run["output_format"] != "pdf":
        raise HTTPException(404, "Preview is only available for PDF output.")
    # No `filename` -> no Content-Disposition header, so the browser renders it inline
    # instead of downloading it. Doesn't trigger cleanup -- the real /result call does.
    return FileResponse(run["output_file"], media_type="application/pdf")


@app.post("/api/tailor/{run_id}/cancel")
def cancel_tailor(run_id: str):
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(404, "Unknown run_id.")
    if run["done"]:
        return {"cancelled": False}
    run.update(done=True, error="Cancelled.", cancelled=True)
    proc = run.get("proc")
    if proc is not None:
        proc.kill()
    _delete_run_dir(RUNS_DIR / run_id)
    return {"cancelled": True}


# Ordered so later matches only apply once earlier ones have already been seen once each,
# via the seen-counts state carried across the run in _run_claude.
def _classify_event(tool_name: str, counts: dict) -> Optional[tuple[str, int]]:
    if tool_name == "Read" and counts["Read"] == 1:
        return ("Reading your CV…", 12)
    if tool_name == "WebFetch":
        return ("Fetching the job posting…", 28)
    if tool_name == "Agent":
        if counts["Agent"] == 1:
            return ("Running an independent review pass…", 58)
        return ("Double-checking the revised draft…", 74)
    if tool_name == "Write":
        return ("Preparing the tailored document…", 85)
    if tool_name == "Bash":
        return ("Rendering your document…", 94)
    return None


def _run_claude(run_id: str, prompt: str, run_dir: Path, output_file: Path):
    # The cancel endpoint may run (on the request thread) before this background thread
    # even gets here -- don't start the subprocess for a run that's already cancelled.
    if RUNS.get(run_id, {}).get("cancelled"):
        return

    cmd = [
        "claude", "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", "bypassPermissions",
        "--allowedTools", ALLOWED_TOOLS,
        "--add-dir", str(run_dir),
    ]

    counts = {"Read": 0, "WebFetch": 0, "Agent": 0, "Write": 0, "Bash": 0}
    tailoring_step_shown = False
    seen_fetch_or_read = False
    tail_output = []

    try:
        proc = subprocess.Popen(cmd, cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    except FileNotFoundError:
        RUNS[run_id].update(done=True, error="The 'claude' CLI was not found on PATH.")
        return
    RUNS[run_id]["proc"] = proc

    # `for line in proc.stdout` blocks whenever the subprocess goes quiet (e.g. a hung
    # nested API call), so a timeout check inside the loop body never fires during that
    # silence. A watchdog timer kills the process on a wall-clock deadline regardless.
    watchdog = threading.Timer(CLAUDE_TIMEOUT_SECONDS, proc.kill)
    watchdog.start()
    for line in proc.stdout:
        tail_output.append(line)
        tail_output[:] = tail_output[-200:]

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        if event.get("type") != "assistant":
            continue
        for block in event.get("message", {}).get("content", []):
            btype = block.get("type")
            if btype == "tool_use":
                name = block.get("name")
                if name in counts:
                    counts[name] += 1
                    if name in ("Read", "WebFetch"):
                        seen_fetch_or_read = True
                classification = _classify_event(name, counts)
                if classification:
                    step, percent = classification
                    if percent > RUNS[run_id]["percent"]:
                        RUNS[run_id].update(step=step, percent=percent)
            elif btype == "text" and seen_fetch_or_read and not tailoring_step_shown and counts["Agent"] == 0:
                if block.get("text", "").strip():
                    tailoring_step_shown = True
                    if 42 > RUNS[run_id]["percent"]:
                        RUNS[run_id].update(step="Tailoring your content to the role…", percent=42)

    watchdog.cancel()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()

    if RUNS.get(run_id, {}).get("cancelled"):
        # The cancel endpoint already finalized this run's state and deleted its
        # directory; nothing left to do (and output_file/error_file no longer exist).
        return

    error_file = run_dir / "error.txt"
    if output_file.exists():
        RUNS[run_id].update(step="Done!", percent=100, done=True, error=None, rationale=_read_rationale(run_dir))
    elif error_file.exists():
        # A deliberate, actionable message the skill wrote itself (e.g. "the job URL is
        # blocked, please paste the text instead") -- show it as-is, not as a joke.
        RUNS[run_id].update(done=True, error=error_file.read_text())
    else:
        # An unexplained internal failure (crash, timeout, non-zero exit). Not actionable
        # for the user, so log the real detail server-side and show a friendly message instead.
        detail = "".join(tail_output)[-4000:]
        print(f"[cv-tailor] run {run_id} failed without an output file:\n{detail}", file=sys.stderr)
        RUNS[run_id].update(done=True, error=random.choice(FUNNY_ERROR_MESSAGES))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8420)
