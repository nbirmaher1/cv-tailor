"""Browsing and filing of a user's organized, permanent application output --
the company > role > dated-attempt folder tree, plus the "pending" inbox for
attempts that finished successfully but couldn't be filed automatically
because the company and/or role couldn't be determined.
"""
import json
import re
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

import auth
import db

router = APIRouter(prefix="/api/applications", tags=["applications"])

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"


def _media_type(output_format: str) -> str:
    return "application/pdf" if output_format == "pdf" else \
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


# Canonical home for slug generation/resolution -- app.py's _finalize_application_run
# imports these rather than duplicating them, since both it and resolve_pending() below
# need to compute the exact same company/role slug for the same inputs.
def _slugify(text: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")[:60].strip("-")
    return slug or fallback


def _resolve_company_role_slugs(user_id: int, company_name: str, role_name: str) -> tuple:
    existing = db.list_applications_for_slug_lookup(user_id)

    company_key = company_name.strip().casefold()
    company_slug = next(
        (row["company_slug"] for row in existing if row["company_name"].strip().casefold() == company_key), None
    )
    if company_slug is None:
        base = _slugify(company_name, "company")
        taken = {row["company_slug"] for row in existing}
        company_slug = base
        n = 2
        while company_slug in taken:
            company_slug = f"{base}-{n}"
            n += 1

    role_key = role_name.strip().casefold()
    role_slug = next(
        (row["role_slug"] for row in existing
         if row["company_slug"] == company_slug and row["role_name"].strip().casefold() == role_key),
        None,
    )
    if role_slug is None:
        base = _slugify(role_name, "role")
        taken = {row["role_slug"] for row in existing if row["company_slug"] == company_slug}
        role_slug = base
        n = 2
        while role_slug in taken:
            role_slug = f"{base}-{n}"
            n += 1

    return company_slug, role_slug


class ResolvePending(BaseModel):
    company_name: str
    role_name: str


class ApplyAttempt(BaseModel):
    attempt_id: int


class UpdateStage(BaseModel):
    stage: str  # 'applied' | 'screening' | 'interviewing' | 'offer' | 'archived'
    note: Optional[str] = None


class UpdateDetails(BaseModel):
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    salary_currency: Optional[str] = None
    location: Optional[str] = None
    work_model: Optional[str] = None
    notes: Optional[str] = None
    follow_up_due_date: Optional[str] = None
    archived_reason: Optional[str] = None


# -- filed applications ---------------------------------------------------------

@router.get("")
def list_applications(user=Depends(auth.get_current_user)):
    return db.list_applications_tree(user["id"])


def _get_owned_attempt(attempt_id: int, user_id: int):
    attempt = db.get_application_attempt(attempt_id, user_id)
    if attempt is None:
        raise HTTPException(404, "Unknown attempt.")
    return attempt


def _get_owned_application(application_id: int, user_id: int):
    application = db.find_application_by_id(application_id, user_id)
    if application is None:
        raise HTTPException(404, "Unknown application.")
    return application


@router.post("/{application_id}/apply")
def apply_application(application_id: int, body: ApplyAttempt, user=Depends(auth.get_current_user)):
    _get_owned_application(application_id, user["id"])
    attempt = _get_owned_attempt(body.attempt_id, user["id"])
    if attempt["application_id"] != application_id:
        raise HTTPException(400, "That tailored CV doesn't belong to this company/role.")
    db.mark_application_applied(application_id, body.attempt_id)
    return {"ok": True}


@router.delete("/{application_id}/apply")
def unapply_application(application_id: int, user=Depends(auth.get_current_user)):
    _get_owned_application(application_id, user["id"])
    db.unmark_application_applied(application_id)
    return {"ok": True}


VALID_STAGES = ("applied", "screening", "interviewing", "offer", "archived")


@router.patch("/{application_id}/stage")
def update_stage(application_id: int, body: UpdateStage, user=Depends(auth.get_current_user)):
    application = _get_owned_application(application_id, user["id"])
    if body.stage not in VALID_STAGES:
        raise HTTPException(400, "Invalid stage.")
    if application["applied_attempt_id"] is None:
        # Moving out of 'tailored' only ever happens through the attempt-specific /apply
        # action above -- "which tailored CV did I apply with" stays a deliberate choice
        # rather than a side effect of picking a stage from a dropdown.
        raise HTTPException(400, "Mark this application as applied first (pick which tailored CV you used).")
    db.update_application_stage(application_id, body.stage, body.note)
    return {"ok": True}


@router.patch("/{application_id}/details")
def update_details(application_id: int, body: UpdateDetails, user=Depends(auth.get_current_user)):
    _get_owned_application(application_id, user["id"])
    db.update_application_details(application_id, **body.model_dump(exclude_unset=True))
    return {"ok": True}


@router.get("/{application_id}/activities")
def get_activities(application_id: int, user=Depends(auth.get_current_user)):
    _get_owned_application(application_id, user["id"])
    return db.list_application_activities(application_id)


@router.get("/attempts/{attempt_id}/result")
def attempt_result(attempt_id: int, user=Depends(auth.get_current_user)):
    attempt = _get_owned_attempt(attempt_id, user["id"])
    folder = DATA_DIR / attempt["folder_path"]
    return FileResponse(
        folder / f"output.{attempt['output_format']}",
        filename=f"{attempt['download_name']}.{attempt['output_format']}",
        media_type=_media_type(attempt["output_format"]),
    )


@router.get("/attempts/{attempt_id}/cover-letter/result")
def attempt_cover_letter_result(attempt_id: int, user=Depends(auth.get_current_user)):
    attempt = _get_owned_attempt(attempt_id, user["id"])
    if not attempt["has_cover_letter"]:
        raise HTTPException(404, "No cover letter was generated for this attempt.")
    folder = DATA_DIR / attempt["folder_path"]
    return FileResponse(
        folder / f"cover_letter.{attempt['output_format']}",
        filename=f"{attempt['download_name']}_cover_letter.{attempt['output_format']}",
        media_type=_media_type(attempt["output_format"]),
    )


@router.get("/attempts/{attempt_id}/preview")
def attempt_preview(attempt_id: int, user=Depends(auth.get_current_user)):
    attempt = _get_owned_attempt(attempt_id, user["id"])
    if attempt["output_format"] != "pdf":
        raise HTTPException(404, "Preview is only available for PDF output.")
    folder = DATA_DIR / attempt["folder_path"]
    return FileResponse(folder / "output.pdf", media_type="application/pdf")


@router.get("/attempts/{attempt_id}/cover-letter/preview")
def attempt_cover_letter_preview(attempt_id: int, user=Depends(auth.get_current_user)):
    attempt = _get_owned_attempt(attempt_id, user["id"])
    if attempt["output_format"] != "pdf":
        raise HTTPException(404, "Preview is only available for PDF output.")
    if not attempt["has_cover_letter"]:
        raise HTTPException(404, "No cover letter was generated for this attempt.")
    folder = DATA_DIR / attempt["folder_path"]
    return FileResponse(folder / "cover_letter.pdf", media_type="application/pdf")


# -- pending (unfiled) attempts -------------------------------------------------

def _get_owned_pending(pending_id: int, user_id: int):
    pending = db.get_pending_attempt(pending_id, user_id)
    if pending is None:
        raise HTTPException(404, "Unknown pending attempt.")
    return pending


@router.get("/pending")
def list_pending(user=Depends(auth.get_current_user)):
    rows = db.list_pending_attempts(user["id"])
    return [
        {
            "id": r["id"],
            "company_name": r["company_name"],
            "role_name": r["role_name"],
            "missing_fields": json.loads(r["missing_fields"]),
            "output_format": r["output_format"],
            "has_cover_letter": bool(r["has_cover_letter"]),
            "download_name": r["download_name"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


@router.patch("/pending/{pending_id}")
def resolve_pending(pending_id: int, body: ResolvePending, user=Depends(auth.get_current_user)):
    pending = _get_owned_pending(pending_id, user["id"])
    company_name = body.company_name.strip()
    role_name = body.role_name.strip()
    if not company_name or not role_name:
        raise HTTPException(400, "Both company and role are required to file this application.")

    user_id = user["id"]
    company_slug, role_slug = _resolve_company_role_slugs(user_id, company_name, role_name)
    src = DATA_DIR / pending["folder_path"]
    attempt_slug = src.name
    dest = DATA_DIR / "users" / str(user_id) / "applications" / company_slug / role_slug / attempt_slug

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
    except OSError:
        raise HTTPException(500, "Couldn't file this application. Please try again.")

    application = db.find_or_create_application(user_id, company_name, company_slug, role_name, role_slug)
    db.create_application_attempt(
        application["id"], pending["output_format"], pending["download_name"],
        str(dest.relative_to(DATA_DIR)), bool(pending["has_cover_letter"]), pending["created_at"],
    )
    db.delete_pending_attempt(pending_id)
    return {"ok": True, "company_slug": company_slug, "role_slug": role_slug}


@router.get("/pending/{pending_id}/result")
def pending_result(pending_id: int, user=Depends(auth.get_current_user)):
    pending = _get_owned_pending(pending_id, user["id"])
    folder = DATA_DIR / pending["folder_path"]
    return FileResponse(
        folder / f"output.{pending['output_format']}",
        filename=f"{pending['download_name']}.{pending['output_format']}",
        media_type=_media_type(pending["output_format"]),
    )


@router.get("/pending/{pending_id}/cover-letter/result")
def pending_cover_letter_result(pending_id: int, user=Depends(auth.get_current_user)):
    pending = _get_owned_pending(pending_id, user["id"])
    if not pending["has_cover_letter"]:
        raise HTTPException(404, "No cover letter was generated for this attempt.")
    folder = DATA_DIR / pending["folder_path"]
    return FileResponse(
        folder / f"cover_letter.{pending['output_format']}",
        filename=f"{pending['download_name']}_cover_letter.{pending['output_format']}",
        media_type=_media_type(pending["output_format"]),
    )


@router.get("/pending/{pending_id}/preview")
def pending_preview(pending_id: int, user=Depends(auth.get_current_user)):
    pending = _get_owned_pending(pending_id, user["id"])
    if pending["output_format"] != "pdf":
        raise HTTPException(404, "Preview is only available for PDF output.")
    folder = DATA_DIR / pending["folder_path"]
    return FileResponse(folder / "output.pdf", media_type="application/pdf")


@router.get("/pending/{pending_id}/cover-letter/preview")
def pending_cover_letter_preview(pending_id: int, user=Depends(auth.get_current_user)):
    pending = _get_owned_pending(pending_id, user["id"])
    if pending["output_format"] != "pdf":
        raise HTTPException(404, "Preview is only available for PDF output.")
    if not pending["has_cover_letter"]:
        raise HTTPException(404, "No cover letter was generated for this attempt.")
    folder = DATA_DIR / pending["folder_path"]
    return FileResponse(folder / "cover_letter.pdf", media_type="application/pdf")
