import re
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel

import auth
import db

router = APIRouter(prefix="/api/auth", tags=["auth"])

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8


class Credentials(BaseModel):
    email: str
    password: str


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        auth.SESSION_COOKIE_NAME, token,
        max_age=auth.SESSION_LIFETIME_DAYS * 24 * 60 * 60,
        httponly=True, samesite="lax", secure=False,
    )


def _user_payload(user) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "has_master_cv": db.has_master_cv(user["id"]),
    }


@router.post("/register")
def register(credentials: Credentials, response: Response):
    email = credentials.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Enter a valid email address.")
    if len(credentials.password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if db.get_user_by_email(email) is not None:
        raise HTTPException(409, "An account with that email already exists.")

    user = db.create_user(email, auth.hash_password(credentials.password))
    token = auth.create_session_token(user["id"])
    _set_session_cookie(response, token)
    return {"user": _user_payload(user)}


@router.post("/login")
def login(credentials: Credentials, response: Response):
    email = credentials.email.strip().lower()
    user = db.get_user_by_email(email)
    if user is None or not auth.verify_password(credentials.password, user["password_hash"]):
        raise HTTPException(401, "Incorrect email or password.")

    token = auth.create_session_token(user["id"])
    _set_session_cookie(response, token)
    return {"user": _user_payload(user)}


@router.post("/logout")
def logout(response: Response, cvt_session: Optional[str] = Cookie(default=None)):
    if cvt_session:
        auth.destroy_session_token(cvt_session)
    response.delete_cookie(auth.SESSION_COOKIE_NAME)
    return {"ok": True}


@router.get("/me")
def me(user=Depends(auth.get_current_user)):
    return _user_payload(user)
