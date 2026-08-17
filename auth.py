"""Local, single-user-per-instance auth: real password hashing and server-side
sessions, deliberately without hosted-deployment hardening (no CSRF token, no
rate limiting, no `secure` cookie flag) -- this app only ever binds to
127.0.0.1, and each user runs their own instance rather than sharing a server.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Cookie, Depends, HTTPException

import db

SESSION_COOKIE_NAME = "cvt_session"
SESSION_LIFETIME_DAYS = 30
PBKDF2_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, iterations, salt_hex, digest_hex = stored_hash.split("$")
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
    )
    return hmac.compare_digest(candidate.hex(), digest_hex)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _iso_now_plus(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def create_session_token(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    db.create_session(user_id, _hash_token(token), _iso_now_plus(SESSION_LIFETIME_DAYS))
    return token


def destroy_session_token(token: str) -> None:
    db.delete_session(_hash_token(token))


def user_from_token(token: Optional[str]):
    if not token:
        return None
    return db.get_session_user(_hash_token(token))


def get_current_user(cvt_session: Optional[str] = Cookie(default=None)):
    user = user_from_token(cvt_session)
    if user is None:
        raise HTTPException(401, "Not logged in.")
    return user


def require_master_cv(user=Depends(get_current_user)):
    """Composite dependency: 409s if the current user hasn't saved a master CV yet."""
    if not db.has_master_cv(user["id"]):
        raise HTTPException(409, "No master CV saved yet.")
    return user
