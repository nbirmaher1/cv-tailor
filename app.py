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
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

PROJECT_ROOT = Path(__file__).resolve().parent
RUNS_DIR = PROJECT_ROOT / "runs"
RUNS_DIR.mkdir(exist_ok=True)

VENV_PY = PROJECT_ROOT / "venv" / "bin" / "python3"
EXTRACT_SCRIPT = PROJECT_ROOT / "scripts" / "extract_docx.py"
RENDER_PDF_SCRIPT = PROJECT_ROOT / "scripts" / "render_pdf.py"
RENDER_DOCX_SCRIPT = PROJECT_ROOT / "scripts" / "render_docx.py"

ALLOWED_TOOLS = (
    "Read Write WebFetch Agent "
    f'Bash({VENV_PY} {EXTRACT_SCRIPT} *) '
    f'Bash({VENV_PY} {RENDER_PDF_SCRIPT} *) '
    f'Bash({VENV_PY} {RENDER_DOCX_SCRIPT} *)'
)

CLAUDE_TIMEOUT_SECONDS = 480

app = FastAPI()

# run_id -> {"step": str, "percent": int, "done": bool, "error": str|None, "output_file": Path|None, "output_format": str}
RUNS: dict[str, dict] = {}


@app.get("/", response_class=HTMLResponse)
def index():
    return (PROJECT_ROOT / "static" / "index.html").read_text()


@app.post("/api/tailor/start")
async def start_tailor(
    cv_file: UploadFile,
    job_url: str = Form(default=""),
    job_text: str = Form(default=""),
    output_format: str = Form(default="pdf"),
    photo: Optional[UploadFile] = File(default=None),
):
    job_url = job_url.strip()
    job_text = job_text.strip()
    output_format = output_format.strip().lower()
    if not job_url and not job_text:
        raise HTTPException(400, "Provide a job posting URL or pasted job description text.")
    if output_format not in ("pdf", "docx"):
        raise HTTPException(400, "output_format must be 'pdf' or 'docx'.")

    run_id = uuid.uuid4().hex
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True)

    cv_suffix = Path(cv_file.filename or "cv").suffix or ".pdf"
    cv_path = run_dir / f"cv{cv_suffix}"
    with cv_path.open("wb") as f:
        shutil.copyfileobj(cv_file.file, f)

    photo_line = ""
    if photo is not None and photo.filename:
        photo_suffix = Path(photo.filename).suffix or ".jpg"
        photo_path = run_dir / f"photo{photo_suffix}"
        with photo_path.open("wb") as f:
            shutil.copyfileobj(photo.file, f)
        photo_line = f"Photo to include: {photo_path}"

    output_file = run_dir / f"output.{output_format}"
    job_line = f"Job posting URL: {job_url}" if job_url else f"Job description text:\n{job_text}"

    prompt = f"""Use the tailor-cv skill's process to tailor this CV to this job posting.

CV file: {cv_path}
{job_line}
{photo_line}
Output format: {output_format}

Follow the skill's steps exactly (extract CV into the canonical JSON content record, get job
description, tailor content, independent review pass with fixes, render, visually/structurally
QA the result), with one override: instead of the default output/<slug> naming, write the final
{output_format.upper()} to exactly {output_file} (and any intermediate .html/.json file next to
it in {run_dir}).

If you cannot proceed (e.g. the job URL is blocked and no job text was given), write a short
explanation to {run_dir / 'error.txt'} instead of an output file, and stop."""

    RUNS[run_id] = {
        "step": "Starting…",
        "percent": 3,
        "done": False,
        "error": None,
        "output_file": output_file,
        "output_format": output_format,
    }

    thread = threading.Thread(target=_run_claude, args=(run_id, prompt, run_dir, output_file), daemon=True)
    thread.start()

    return {"run_id": run_id}


@app.get("/api/tailor/{run_id}/status")
def tailor_status(run_id: str):
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(404, "Unknown run_id.")
    return {"step": run["step"], "percent": run["percent"], "done": run["done"], "error": run["error"]}


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
    return FileResponse(run["output_file"], filename=f"tailored_cv.{output_format}", media_type=media_type)


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

    start_time = time.time()
    for line in proc.stdout:
        tail_output.append(line)
        tail_output[:] = tail_output[-200:]

        if time.time() - start_time > CLAUDE_TIMEOUT_SECONDS:
            proc.kill()
            break

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

    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()

    error_file = run_dir / "error.txt"
    if output_file.exists():
        RUNS[run_id].update(step="Done!", percent=100, done=True, error=None)
    elif error_file.exists():
        RUNS[run_id].update(done=True, error=error_file.read_text())
    else:
        detail = "".join(tail_output)[-2000:]
        RUNS[run_id].update(done=True, error=f"Tailoring failed without producing an output file.\n{detail}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8420)
