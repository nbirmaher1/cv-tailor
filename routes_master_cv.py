"""Save-once, edit-in-app master CV: parse an uploaded CV into the canonical
content JSON exactly once (no job posting involved), then let the user edit
that record directly through a form -- no further Claude calls needed for
ordinary field edits, the same "load a JSON record, edit just that" precedent
app.py's revision flow already established.
"""
import json
import shutil
import sys
import threading
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

import auth
import claude_runner
import db

router = APIRouter(prefix="/api/master-cv", tags=["master-cv"])

PROJECT_ROOT = Path(__file__).resolve().parent
RUNS_DIR = PROJECT_ROOT / "runs"
RUNS_DIR.mkdir(exist_ok=True)
DATA_DIR = PROJECT_ROOT / "data"

MAX_CV_BYTES = 10 * 1024 * 1024
MAX_PHOTO_BYTES = 5 * 1024 * 1024
ALLOWED_CV_EXTENSIONS = {".pdf", ".docx"}
ALLOWED_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

# .docx text extraction runs directly in this process (see the sys.path/import below)
# rather than via a Claude Bash call -- --allowedTools scoping of Bash was verified
# empirically to not be reliably enforced by the CLI, so Bash is dropped from Claude's
# tool access entirely here (see app.py's matching note for the full context).
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import extract_docx  # noqa: E402

# No WebFetch (no job posting involved), no Bash (extraction happens server-side above;
# this step never renders a document), no WebSearch.
MASTER_CV_PARSE_ALLOWED_TOOLS = "Read Write Agent"
DISALLOWED_TOOLS = "Bash"

CLAUDE_TIMEOUT_SECONDS = 720

# job_id -> {"step", "percent", "done", "error", "proc", "cancelled", "user_id"}
# Kept separate from app.py's RUNS so the six tailor endpoints never need to
# branch on "what kind of job is this".
MASTER_CV_JOBS: dict = {}


def master_cv_dir(user_id: int) -> Path:
    return DATA_DIR / "users" / str(user_id) / "master_cv"


class ExperienceEntry(BaseModel):
    title: str = ""
    company: str = ""
    location: str = ""
    dates: str = ""
    bullets: List[str] = []


class EducationEntry(BaseModel):
    degree: str = ""
    school: str = ""
    location: str = ""
    dates: str = ""


class SkillGroup(BaseModel):
    category: Optional[str] = None
    items: List[str] = []


class ExtraSection(BaseModel):
    heading: str = ""
    items: List[str] = []


class MasterCVContent(BaseModel):
    full_name: str
    target_title: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    links: str = ""
    work_authorization: str = ""
    photo_path: Optional[str] = None
    summary: str = ""
    experience: List[ExperienceEntry] = []
    education: List[EducationEntry] = []
    skills: List[SkillGroup] = []
    languages: List[str] = []
    extra_sections: List[ExtraSection] = []


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


def _bump(job_id: str, step: str, percent: int) -> None:
    job = MASTER_CV_JOBS.get(job_id)
    if job is not None and percent > job["percent"]:
        job.update(step=step, percent=percent)


def _run_master_cv_parse(job_id: str, prompt: str, run_dir: Path, content_file: Path,
                          user_id: int, photo_ext: Optional[str]) -> None:
    cmd = [
        "claude", "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", "bypassPermissions",
        "--allowedTools", MASTER_CV_PARSE_ALLOWED_TOOLS,
        "--disallowedTools", DISALLOWED_TOOLS,
        "--add-dir", str(run_dir),
    ]

    counts = {"Read": 0, "Agent": 0}

    def on_event(block):
        if block.get("type") != "tool_use":
            return
        name = block.get("name")
        if name in counts:
            counts[name] += 1
        if name == "Read" and counts["Read"] == 1:
            _bump(job_id, "Reading your CV…", 25)
        elif name == "Agent":
            _bump(job_id, "Double-checking the details…", 70)
        elif name == "Write":
            _bump(job_id, "Saving your master CV…", 90)

    def success_check() -> bool:
        return content_file.exists()

    def on_success() -> None:
        dest_dir = master_cv_dir(user_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_content = dest_dir / "content.json"
        shutil.move(str(content_file), str(dest_content))
        if photo_ext:
            photo_src = run_dir / f"photo{photo_ext}"
            if photo_src.exists():
                for old in dest_dir.glob("photo.*"):
                    old.unlink()
                shutil.move(str(photo_src), str(dest_dir / f"photo{photo_ext}"))
        # photo_path (if the model set one) pointed at the now-deleted scratch
        # dir -- null it out. Whoever renders from this record later (tailor
        # start, revision) resolves the real photo path themselves by checking
        # for a photo.* file in this directory, the same way app.py already
        # does for a per-run upload, rather than trusting a stored path here.
        record = json.loads(dest_content.read_text())
        record["photo_path"] = None
        dest_content.write_text(json.dumps(record, indent=2))
        db.touch_master_cv(user_id)
        shutil.rmtree(run_dir, ignore_errors=True)

    claude_runner.run_process_and_finalize(
        job_id, cmd, PROJECT_ROOT, run_dir, on_event, success_check, MASTER_CV_JOBS,
        timeout_seconds=CLAUDE_TIMEOUT_SECONDS,
        fail_messages=["We couldn't read that CV. Please double-check the file and try again."],
        on_success=on_success,
    )


@router.post("/upload")
async def upload_master_cv(
    cv_file: UploadFile,
    photo: Optional[UploadFile] = File(default=None),
    user=Depends(auth.get_current_user),
):
    cv_suffix = Path(cv_file.filename or "").suffix.lower()
    if cv_suffix not in ALLOWED_CV_EXTENSIONS:
        raise HTTPException(400, "CV must be a PDF or .docx file.")
    photo_suffix = None
    if photo is not None and photo.filename:
        photo_suffix = Path(photo.filename).suffix.lower()
        if photo_suffix not in ALLOWED_PHOTO_EXTENSIONS:
            raise HTTPException(400, "Photo must be a JPG, PNG, WEBP, or GIF file.")

    job_id = uuid.uuid4().hex
    run_dir = RUNS_DIR / f"master-cv-{job_id}"
    run_dir.mkdir(parents=True)

    cv_path = run_dir / f"cv{cv_suffix}"
    _save_upload_with_limit(cv_file, cv_path, MAX_CV_BYTES, "CV file")

    photo_line = ""
    if photo is not None and photo.filename:
        photo_path = run_dir / f"photo{photo_suffix}"
        _save_upload_with_limit(photo, photo_path, MAX_PHOTO_BYTES, "Photo")
        photo_line = f"Photo to reference (set photo_path to this exact path): {photo_path}"

    content_file = run_dir / "content.json"

    if cv_suffix == ".pdf":
        read_step = f"Read tool directly on {cv_path}"
    else:
        # Extracted here (not by Claude via Bash) -- see the sys.path/import note above.
        extracted_text_path = run_dir / "cv.txt"
        extracted_text_path.write_text(extract_docx.extract(str(cv_path)))
        read_step = f"Read tool on {extracted_text_path} (already extracted from the original .docx)"

    prompt = f"""Parse this CV into the canonical JSON content record used throughout cv-tailor.
This is a one-time save of the candidate's full "master" CV -- there is no job posting involved,
so do NOT tailor, trim, or select content for relevance. Extract everything faithfully, exactly
as SKILL.md's step 3 describes for a "long master CV source": no pre-filtering, preserve the CV's
actual section structure rather than forcing it into fixed buckets.

1. Read the CV: {read_step}
{photo_line}

2. Extract into exactly this JSON schema:
{{"full_name": "", "target_title": "", "email": "", "phone": "", "location": "", "links": "",
"work_authorization": "", "photo_path": null, "summary": "",
"experience": [{{"title": "", "company": "", "location": "", "dates": "", "bullets": [""]}}],
"education": [{{"degree": "", "school": "", "location": "", "dates": ""}}],
"skills": [{{"category": null, "items": [""]}}], "languages": [""],
"extra_sections": [{{"heading": "", "items": [""]}}]}}
   - `target_title`: the candidate's current/most recent headline, not tailored to anything.
   - `summary`: rewrite only for grammar/clarity if needed -- keep it the candidate's own summary,
     not a tailored one. Preserve the candidate's actual word choices as closely as possible; fix
     only genuine grammar errors. Never introduce a buzzword, cliche, or em dash (--) that wasn't
     already there -- this record becomes the base material every future tailored summary is
     rewritten from, so AI-sounding phrasing introduced here would propagate to every application.
   - Normalize `location` to "City, Country" (or "City, State" for the US); drop street
     address/postal code.
   - Never invent experience, skills, titles, or dates. Omit what's missing rather than fabricate.

3. Independent consistency check: use the Agent tool (subagent_type: general-purpose) with the
   full CV text and your draft JSON. Ask it to check ONLY for structural/factual consistency --
   impossible or contradictory dates, obviously garbled/truncated parsing artifacts, duplicate
   entries, or empty required fields -- and report PASS or a specific, quoted fix list. This is
   NOT a relevance/keyword/tailoring review (there's no job posting to tailor against); do not
   flag anything about relevance, trimming, or keyword coverage. Apply any fixes by correcting
   parsing errors or normalizing formatting only -- never by inventing or guessing content.

4. Write the final record to exactly {content_file}. Do not render any document -- this step only
   produces the JSON record.

If you cannot proceed (e.g. the file is unreadable or empty), write a short explanation to
{run_dir / 'error.txt'} instead of writing {content_file}, and stop."""

    MASTER_CV_JOBS[job_id] = {
        "step": "Starting…",
        "percent": 3,
        "done": False,
        "error": None,
        "proc": None,
        "cancelled": False,
        "user_id": user["id"],
    }

    thread = threading.Thread(
        target=_run_master_cv_parse,
        args=(job_id, prompt, run_dir, content_file, user["id"], photo_suffix),
        daemon=True,
    )
    thread.start()

    return {"job_id": job_id}


@router.get("/parse/{job_id}/status")
def master_cv_parse_status(job_id: str, user=Depends(auth.get_current_user)):
    job = MASTER_CV_JOBS.get(job_id)
    if job is None or job.get("user_id") != user["id"]:
        raise HTTPException(404, "Unknown job_id.")
    return {
        "step": job["step"],
        "percent": job["percent"],
        "done": job["done"],
        "error": job["error"],
    }


def _has_photo(user_id: int) -> bool:
    return any(master_cv_dir(user_id).glob("photo.*"))


@router.get("")
def get_master_cv(user=Depends(auth.get_current_user)):
    content_file = master_cv_dir(user["id"]) / "content.json"
    if not content_file.exists():
        raise HTTPException(404, "No master CV saved yet.")
    content = MasterCVContent.model_validate_json(content_file.read_text())
    return {**content.model_dump(), "has_photo": _has_photo(user["id"])}


@router.put("")
def update_master_cv(content: MasterCVContent, user=Depends(auth.require_master_cv)):
    content_file = master_cv_dir(user["id"]) / "content.json"
    content_file.write_text(content.model_dump_json(indent=2))
    db.touch_master_cv(user["id"])
    return {**content.model_dump(), "has_photo": _has_photo(user["id"])}


PHOTO_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


@router.get("/photo")
def get_master_cv_photo(user=Depends(auth.get_current_user)):
    photo = next(master_cv_dir(user["id"]).glob("photo.*"), None)
    if photo is None:
        raise HTTPException(404, "No photo saved.")
    return FileResponse(photo, media_type=PHOTO_MEDIA_TYPES.get(photo.suffix.lower(), "application/octet-stream"))


@router.post("/photo")
async def upload_master_cv_photo(photo: UploadFile, user=Depends(auth.require_master_cv)):
    suffix = Path(photo.filename or "").suffix.lower()
    if suffix not in ALLOWED_PHOTO_EXTENSIONS:
        raise HTTPException(400, "Photo must be a JPG, PNG, WEBP, or GIF file.")
    dest_dir = master_cv_dir(user["id"])
    for old in dest_dir.glob("photo.*"):
        old.unlink()
    _save_upload_with_limit(photo, dest_dir / f"photo{suffix}", MAX_PHOTO_BYTES, "Photo")
    db.touch_master_cv(user["id"])
    return {"ok": True}


@router.delete("/photo")
def delete_master_cv_photo(user=Depends(auth.require_master_cv)):
    dest_dir = master_cv_dir(user["id"])
    for old in dest_dir.glob("photo.*"):
        old.unlink()
    db.touch_master_cv(user["id"])
    return {"ok": True}
