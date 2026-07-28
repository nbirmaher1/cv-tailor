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
MAX_FEEDBACK_CHARS = 2_000
MAX_COVER_LETTER_NOTES_CHARS = 2_000
MAX_REVISIONS = 3
ALLOWED_CV_EXTENSIONS = {".pdf", ".docx"}
ALLOWED_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ALLOWED_COVER_LETTER_TEMPLATE_EXTENSIONS = {".pdf", ".docx", ".txt"}

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
RENDER_COVER_LETTER_DOCX_SCRIPT = PROJECT_ROOT / "scripts" / "render_cover_letter_docx.py"
DOCX_STATS_SCRIPT = PROJECT_ROOT / "scripts" / "docx_stats.py"

ALLOWED_TOOLS = (
    "Read Write WebFetch Agent "
    f'Bash({VENV_PY} {EXTRACT_SCRIPT} *) '
    f'Bash({VENV_PY} {RENDER_PDF_SCRIPT} *) '
    f'Bash({VENV_PY} {RENDER_DOCX_SCRIPT} *) '
    f'Bash({VENV_PY} {RENDER_COVER_LETTER_DOCX_SCRIPT} *) '
    f'Bash({VENV_PY} {DOCX_STATS_SCRIPT} *)'
)

# Only granted for a run that opted into "intelligent" cover letter research -- WebSearch
# isn't needed for ordinary tailoring (WebFetch already covers the one known job-posting
# URL), so it stays out of the default tool set.
ALLOWED_TOOLS_WITH_WEB_SEARCH = ALLOWED_TOOLS + " WebSearch"

# Revisions only edit an already-tailored record and re-render it -- no WebFetch (must not
# re-fetch/re-derive from the job posting) and no Agent (no full independent-review redo),
# which also structurally enforces "targeted edit, not a fresh tailoring pass."
REVISE_ALLOWED_TOOLS = (
    "Read Write "
    f'Bash({VENV_PY} {RENDER_PDF_SCRIPT} *) '
    f'Bash({VENV_PY} {RENDER_DOCX_SCRIPT} *) '
    f'Bash({VENV_PY} {RENDER_COVER_LETTER_DOCX_SCRIPT} *) '
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


def _cleanup_run_if_fully_downloaded(run_id: str, run_dir: Path) -> None:
    """Only wipe the run once every document it produced has actually been downloaded --
    downloading the CV must not delete a cover letter the user hasn't fetched yet, and vice
    versa. Falls back to the retention sweep for anything left half-downloaded."""
    run = RUNS.get(run_id)
    if run is None:
        return
    cover_letter_file = run.get("cover_letter_file")
    has_cover_letter = cover_letter_file is not None and cover_letter_file.exists()
    cv_done = run.get("cv_downloaded", False)
    cover_letter_done = (not has_cover_letter) or run.get("cover_letter_downloaded", False)
    if cv_done and cover_letter_done:
        _cleanup_run(run_id, run_dir)


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
    include_cover_letter: str = Form(default="false"),
    cover_letter_notes: str = Form(default=""),
    cover_letter_template: Optional[UploadFile] = File(default=None),
    intelligent_cover_letter: str = Form(default="false"),
):
    job_url = job_url.strip()
    job_text = job_text.strip()
    output_format = output_format.strip().lower()
    notes = notes.strip()
    wants_cover_letter = include_cover_letter.strip().lower() in ("true", "1", "on", "yes")
    # Only meaningful (and only granted the extra WebSearch tool) when a cover letter was
    # actually requested -- meaningless on its own.
    wants_company_research = wants_cover_letter and intelligent_cover_letter.strip().lower() in ("true", "1", "on", "yes")
    cover_letter_notes = cover_letter_notes.strip()
    download_name = _sanitize_filename(filename)
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

    cv_suffix = Path(cv_file.filename or "").suffix.lower()
    if cv_suffix not in ALLOWED_CV_EXTENSIONS:
        raise HTTPException(400, "CV must be a PDF or .docx file.")
    photo_suffix = None
    if photo is not None and photo.filename:
        photo_suffix = Path(photo.filename).suffix.lower()
        if photo_suffix not in ALLOWED_PHOTO_EXTENSIONS:
            raise HTTPException(400, "Photo must be a JPG, PNG, WEBP, or GIF file.")
    cover_letter_template_suffix = None
    if cover_letter_template is not None and cover_letter_template.filename:
        cover_letter_template_suffix = Path(cover_letter_template.filename).suffix.lower()
        if cover_letter_template_suffix not in ALLOWED_COVER_LETTER_TEMPLATE_EXTENSIONS:
            raise HTTPException(400, "Cover letter template must be a PDF, .docx, or .txt file.")

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
    content_file = run_dir / "content.json"
    html_file = run_dir / "output.html"
    summary_file = run_dir / "summary.json"
    cover_letter_file = run_dir / f"cover_letter.{output_format}"
    job_line = f"Job posting URL: {job_url}" if job_url else f"Job description text:\n{job_text}"
    notes_line = (
        f"Additional notes/comments from the candidate (apply these to the CV content itself per "
        f"the skill's step 3 — e.g. update contact/location fields or add the detail somewhere "
        f"visible in the output, not just as background context):\n{notes}"
        if notes else ""
    )

    cover_letter_block = ""
    if wants_cover_letter:
        cover_letter_content_file = run_dir / "cover_letter_content.json"
        cover_letter_html_file = run_dir / "cover_letter.html"

        template_line = ""
        if cover_letter_template is not None and cover_letter_template.filename:
            template_suffix = cover_letter_template_suffix
            template_path = run_dir / f"cover_letter_template{template_suffix}"
            _save_upload_with_limit(cover_letter_template, template_path, MAX_CV_BYTES, "Cover letter template")
            template_line = (
                f"The candidate provided a previous cover letter as a style/structure reference at "
                f"{template_path} (.docx -> extract with {EXTRACT_SCRIPT} first, .txt/.pdf read directly). "
                f"Use it as your model for voice, structure, and opening style, but rewrite the substance "
                f"for this specific job posting and this candidate's actual tailored background -- never "
                f"carry over the old letter's company name, role, or specific claims into the new one."
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

        if output_format == "pdf":
            cover_letter_render_step = (
                f"fill templates/cover_letter.html placeholders with this record, write "
                f"{cover_letter_html_file}, then run "
                f"`{VENV_PY} {RENDER_PDF_SCRIPT} {cover_letter_html_file} {cover_letter_file}`"
            )
        else:
            cover_letter_render_step = (
                f"run `{VENV_PY} {RENDER_COVER_LETTER_DOCX_SCRIPT} {cover_letter_content_file} {cover_letter_file}`"
            )

        cover_letter_block = f"""
Also write a tailored cover letter for this same role, using the reviewed CV content record at
{content_file} and the job posting above -- reuse full_name/email/phone/location from that
record rather than re-reading the raw CV file.
{template_line}
{guidance_line}
{research_line}

Cover letter content rules:
- 3-4 body paragraphs, ~250-400 words total. Professional, direct tone -- no generic filler
  ("I am writing to express my interest...").
- Open by naming the specific role and one concrete reason it's a strong fit.
- Body: the 2-3 strongest, most concrete matches between the candidate's actual background (per
  the content record) and this job's requirements -- specific outcomes/scope, not restated bullet
  points. Never invent achievements, numbers, or experience not already in the content record or
  explicitly stated in the candidate's guidance above.
- Close with a brief, confident call to action.
- Address a named hiring manager/company if the job posting gives one, otherwise "Dear Hiring
  Manager,".

Structure it as this JSON: {{"full_name": "", "contact_line": "email | phone | location, same
style as the CV contact line", "date": "today's date, e.g. 'March 3, 2026'", "salutation": "",
"paragraphs": ["", "..."], "closing": "e.g. 'Sincerely,'", "signature_name": ""}}

Write that JSON to exactly {cover_letter_content_file}. Then {cover_letter_render_step} to
produce exactly {cover_letter_file} ({output_format.upper()} format).

If the cover letter can't be produced for some reason, note it in {summary_file}'s "review_note"
and skip it -- don't fail the whole run over it."""

    prompt = f"""Use the tailor-cv skill's process to tailor this CV to this job posting.

CV file: {cv_path}
{job_line}
{photo_line}
{notes_line}
Output format: {output_format}

Follow the skill's steps exactly (extract CV into the canonical JSON content record, get job
description, tailor content, independent review pass with fixes, render, visually/structurally
QA the result), with these overrides on file locations: instead of the default output/<slug>
naming, write the final {output_format.upper()} to exactly {output_file}. Also write the reviewed
content JSON record (from step 3, after step 5's review) to exactly {content_file} regardless of
output format -- this is the source of truth if the candidate later asks for a revision. For PDF
output, write the filled HTML to exactly {html_file} before rendering (so a later revision can
find and re-fill it).

Also write the step 9 report as JSON to exactly {summary_file}, with this schema:
{{"summary": "one or two sentence overview of the tailoring approach", "changes": ["short bullet
describing one change, written for the candidate to read, under ~100 characters", "..."],
"review_note": "short note on what the independent review pass caught and fixed, or empty string
if nothing needed fixing"}}
{cover_letter_block}

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
        "revising": False,
        "revision_count": 0,
        "cover_letter_file": cover_letter_file,
        "cv_downloaded": False,
        "cover_letter_downloaded": False,
    }

    run_allowed_tools = ALLOWED_TOOLS_WITH_WEB_SEARCH if wants_company_research else ALLOWED_TOOLS
    thread = threading.Thread(
        target=_run_claude, args=(run_id, prompt, run_dir, output_file, run_allowed_tools), daemon=True
    )
    thread.start()

    return {"run_id": run_id}


def _media_type(output_format: str) -> str:
    return "application/pdf" if output_format == "pdf" else \
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


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
        "revision_count": run.get("revision_count", 0),
        "max_revisions": MAX_REVISIONS,
        "has_cover_letter": run.get("cover_letter_file") is not None and run["cover_letter_file"].exists(),
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
    run["cv_downloaded"] = True
    return FileResponse(
        run["output_file"],
        filename=f"{run['download_name']}.{output_format}",
        media_type=_media_type(output_format),
        background=BackgroundTask(_cleanup_run_if_fully_downloaded, run_id, RUNS_DIR / run_id),
    )


@app.get("/api/tailor/{run_id}/cover-letter/result")
def cover_letter_result(run_id: str):
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(404, "Unknown run_id.")
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
        background=BackgroundTask(_cleanup_run_if_fully_downloaded, run_id, RUNS_DIR / run_id),
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


@app.get("/api/tailor/{run_id}/cover-letter/preview")
def cover_letter_preview(run_id: str):
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(404, "Unknown run_id.")
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
def revise_tailor(run_id: str, feedback: str = Form(...)):
    feedback = feedback.strip()
    if not feedback:
        raise HTTPException(400, "Describe what you'd like changed.")
    if len(feedback) > MAX_FEEDBACK_CHARS:
        raise HTTPException(400, f"Feedback is too long (max {MAX_FEEDBACK_CHARS:,} characters).")

    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(404, "Unknown run_id.")
    if not run["done"] or run["error"]:
        raise HTTPException(409, "This run isn't ready to revise.")
    revision_count = run.get("revision_count", 0)
    if revision_count >= MAX_REVISIONS:
        raise HTTPException(400, f"You've used all {MAX_REVISIONS} revisions for this CV.")

    run_dir = RUNS_DIR / run_id
    content_file = run_dir / "content.json"
    output_file = run["output_file"]
    output_format = run["output_format"]
    if not content_file.exists() or not output_file.exists():
        raise HTTPException(409, "Nothing to revise — the previous result is missing.")

    (run_dir / "error.txt").unlink(missing_ok=True)
    html_file = run_dir / "output.html"
    summary_file = run_dir / "summary.json"

    if output_format == "pdf":
        render_step = (
            f"refill {html_file} with the updated record and run "
            f"`{VENV_PY} {RENDER_PDF_SCRIPT} {html_file} {output_file}`"
        )
    else:
        render_step = f"run `{VENV_PY} {RENDER_DOCX_SCRIPT} {content_file} {output_file}`"

    cover_letter_file = run.get("cover_letter_file")
    cover_letter_content_file = run_dir / "cover_letter_content.json"
    cover_letter_revise_block = ""
    if cover_letter_content_file.exists() and cover_letter_file is not None:
        if output_format == "pdf":
            cl_html_file = run_dir / "cover_letter.html"
            cl_render_step = (
                f"refill {cl_html_file} with the updated record and run "
                f"`{VENV_PY} {RENDER_PDF_SCRIPT} {cl_html_file} {cover_letter_file}`"
            )
        else:
            cl_render_step = (
                f"run `{VENV_PY} {RENDER_COVER_LETTER_DOCX_SCRIPT} "
                f"{cover_letter_content_file} {cover_letter_file}`"
            )
        cover_letter_revise_block = f"""

A cover letter was also generated for this run, with its own content record at
{cover_letter_content_file}. If (and only if) the feedback above relates to the cover letter,
apply the same targeted-edit approach there: read {cover_letter_content_file}, edit only what
the feedback addresses, overwrite it, then {cl_render_step} to produce exactly
{cover_letter_file}. Leave the cover letter file untouched if the feedback is only about the CV."""

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
3. Overwrite {content_file} with the updated record (same JSON schema as before).
4. Re-render to exactly {output_file} ({output_format.upper()} format): {render_step}.
5. Re-run the length/QA check the skill's step 8 describes (the edit may have changed length) --
   cut only if this edit pushed it over the page limit, and cut from what the edit added first,
   not unrelated content that was already fine.
6. Update {summary_file}: append one short bullet to "changes" describing what this revision did
   (don't remove or reword the existing entries), and update "review_note" only if this revision
   fixes something it previously flagged.
{cover_letter_revise_block}

If you cannot proceed, write a short explanation to {run_dir / 'error.txt'} instead of touching
{output_file}, and stop."""

    run.update(
        step="Applying your feedback…",
        percent=8,
        done=False,
        error=None,
        cancelled=False,
        revising=True,
        revision_count=revision_count + 1,
    )

    thread = threading.Thread(
        target=_run_revision, args=(run_id, prompt, run_dir, output_file, cover_letter_file), daemon=True
    )
    thread.start()

    return {"run_id": run_id}


@app.post("/api/tailor/{run_id}/cancel")
def cancel_tailor(run_id: str):
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(404, "Unknown run_id.")
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
        _delete_run_dir(RUNS_DIR / run_id)
    return {"cancelled": True}


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
        return ("Preparing the tailored document…", 85)
    if tool_name == "Bash":
        return ("Rendering your document…", 94)
    return None


def _classify_revision_event(tool_name: str, counts: dict) -> Optional[tuple[str, int]]:
    if tool_name == "Read" and counts["Read"] == 1:
        return ("Reading your feedback into context…", 20)
    if tool_name == "Write":
        return ("Applying your changes…", 60)
    if tool_name == "Bash":
        return ("Re-rendering your CV…", 90)
    return None


def _run_process_and_finalize(run_id, cmd, run_dir, on_event, success_check):
    """Shared subprocess/streaming/cancellation plumbing for both a fresh tailoring run
    and a revision. `on_event` gets each assistant content block (for progress updates);
    `success_check` decides whether the attempt produced a usable result."""
    # The cancel endpoint may run (on the request thread) before this background thread
    # even gets here -- don't start the subprocess for an attempt that's already cancelled.
    if RUNS.get(run_id, {}).get("cancelled"):
        return

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
            on_event(block)

    watchdog.cancel()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()

    if RUNS.get(run_id, {}).get("cancelled"):
        # The cancel endpoint already finalized this attempt's state (and, for a fresh
        # run, deleted its directory); nothing left to do here.
        return

    error_file = run_dir / "error.txt"
    if success_check():
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


def _run_claude(run_id: str, prompt: str, run_dir: Path, output_file: Path, allowed_tools: str = ALLOWED_TOOLS):
    cmd = [
        "claude", "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", "bypassPermissions",
        "--allowedTools", allowed_tools,
        "--add-dir", str(run_dir),
    ]

    counts = {"Read": 0, "WebFetch": 0, "WebSearch": 0, "Agent": 0, "Write": 0, "Bash": 0}
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

    _run_process_and_finalize(run_id, cmd, run_dir, on_event, success_check=output_file.exists)


def _run_revision(run_id: str, prompt: str, run_dir: Path, output_file: Path, cover_letter_file: Optional[Path] = None):
    # Captured before the attempt starts: output_file already exists (it's the previous,
    # still-valid result), so "did this attempt do anything" has to be judged by whether
    # it actually got rewritten, not just whether it exists. Feedback might target only the
    # cover letter, so a change to either file (not just the CV) counts as a real attempt.
    pre_mtime = output_file.stat().st_mtime if output_file.exists() else None
    cl_pre_mtime = None
    if cover_letter_file is not None and cover_letter_file.exists():
        cl_pre_mtime = cover_letter_file.stat().st_mtime

    cmd = [
        "claude", "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", "bypassPermissions",
        "--allowedTools", REVISE_ALLOWED_TOOLS,
        "--add-dir", str(run_dir),
    ]

    counts = {"Read": 0, "Write": 0, "Bash": 0}

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

    def success_check():
        cv_changed = output_file.exists() and (pre_mtime is None or output_file.stat().st_mtime > pre_mtime)
        cl_changed = (
            cover_letter_file is not None and cover_letter_file.exists()
            and (cl_pre_mtime is None or cover_letter_file.stat().st_mtime > cl_pre_mtime)
        )
        return cv_changed or cl_changed

    _run_process_and_finalize(run_id, cmd, run_dir, on_event, success_check)
    if run_id in RUNS:
        RUNS[run_id]["revising"] = False


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8420)
