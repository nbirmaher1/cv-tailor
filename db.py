"""SQLite persistence layer for accounts, sessions, and job-application metadata.

Actual CV/cover-letter files and JSON content records stay on the filesystem
(data/users/<user_id>/...) -- this module only tracks the metadata needed to
authenticate users and organize/look up those files.

Opens a fresh connection per call rather than sharing one across threads --
simplest way to stay safe given app.py runs background threads per tailoring
run, and WAL mode keeps concurrent reads from blocking on the occasional write.
"""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DB_PATH = PROJECT_ROOT / "data" / "cvtailor.db"
SCHEMA_PATH = PROJECT_ROOT / "schema.sql"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def _conn():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(SCHEMA_PATH.read_text())


# -- users --------------------------------------------------------------------

def create_user(email: str, password_hash: str) -> sqlite3.Row:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)", (email, password_hash)
        )
        return conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()


def get_user_by_email(email: str) -> "sqlite3.Row | None":
    with _conn() as conn:
        return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def get_user_by_id(user_id: int) -> "sqlite3.Row | None":
    with _conn() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


# -- sessions -------------------------------------------------------------------

def create_session(user_id: int, token_hash: str, expires_at: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (token_hash, user_id, expires_at),
        )


def get_session_user(token_hash: str) -> "sqlite3.Row | None":
    with _conn() as conn:
        return conn.execute(
            """SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id
               WHERE sessions.token_hash = ?
               AND sessions.expires_at > strftime('%Y-%m-%dT%H:%M:%fZ', 'now')""",
            (token_hash,),
        ).fetchone()


def delete_session(token_hash: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


# -- master_cv --------------------------------------------------------------------

def has_master_cv(user_id: int) -> bool:
    with _conn() as conn:
        row = conn.execute("SELECT 1 FROM master_cv WHERE user_id = ?", (user_id,)).fetchone()
        return row is not None


def touch_master_cv(user_id: int) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO master_cv (user_id, updated_at)
               VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
               ON CONFLICT(user_id) DO UPDATE SET updated_at = excluded.updated_at""",
            (user_id,),
        )


def delete_master_cv(user_id: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM master_cv WHERE user_id = ?", (user_id,))


# -- applications / application_attempts -------------------------------------------

def find_application(user_id: int, company_slug: str, role_slug: str) -> "sqlite3.Row | None":
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM applications WHERE user_id = ? AND company_slug = ? AND role_slug = ?",
            (user_id, company_slug, role_slug),
        ).fetchone()


def list_applications_for_slug_lookup(user_id: int) -> list:
    """All of a user's applications, for the exact-string company/role slug
    collision resolution in app.py's _resolve_company_role_slugs."""
    with _conn() as conn:
        return conn.execute(
            "SELECT company_name, company_slug, role_name, role_slug FROM applications WHERE user_id = ?",
            (user_id,),
        ).fetchall()


def find_or_create_application(user_id: int, company_name: str, company_slug: str,
                                role_name: str, role_slug: str) -> sqlite3.Row:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM applications WHERE user_id = ? AND company_slug = ? AND role_slug = ?",
            (user_id, company_slug, role_slug),
        ).fetchone()
        if row is not None:
            return row
        cur = conn.execute(
            """INSERT INTO applications (user_id, company_name, company_slug, role_name, role_slug)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, company_name, company_slug, role_name, role_slug),
        )
        return conn.execute("SELECT * FROM applications WHERE id = ?", (cur.lastrowid,)).fetchone()


def create_application_attempt(application_id: int, output_format: str, download_name: str,
                                folder_path: str, has_cover_letter: bool, created_at: str) -> sqlite3.Row:
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO application_attempts
               (application_id, output_format, download_name, folder_path, has_cover_letter, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (application_id, output_format, download_name, folder_path, int(has_cover_letter), created_at),
        )
        return conn.execute("SELECT * FROM application_attempts WHERE id = ?", (cur.lastrowid,)).fetchone()


def list_applications_tree(user_id: int) -> list:
    """Nested company -> role -> attempts tree, most-recent-first at every level."""
    with _conn() as conn:
        apps = conn.execute(
            "SELECT * FROM applications WHERE user_id = ? ORDER BY company_name, role_name",
            (user_id,),
        ).fetchall()
        companies: dict = {}
        for a in apps:
            attempts = conn.execute(
                "SELECT * FROM application_attempts WHERE application_id = ? ORDER BY created_at DESC",
                (a["id"],),
            ).fetchall()
            company = companies.setdefault(a["company_slug"], {
                "company_name": a["company_name"], "company_slug": a["company_slug"], "roles": [],
            })
            company["roles"].append({
                "role_name": a["role_name"],
                "role_slug": a["role_slug"],
                "attempts": [
                    {
                        "id": at["id"],
                        "created_at": at["created_at"],
                        "output_format": at["output_format"],
                        "has_cover_letter": bool(at["has_cover_letter"]),
                        "download_name": at["download_name"],
                    }
                    for at in attempts
                ],
            })
        result = list(companies.values())
        result.sort(key=lambda c: max(
            (r["attempts"][0]["created_at"] for r in c["roles"] if r["attempts"]), default=""
        ), reverse=True)
        return result


def get_application_attempt(attempt_id: int, user_id: int) -> "sqlite3.Row | None":
    """Ownership-checked lookup, joined through applications.user_id."""
    with _conn() as conn:
        return conn.execute(
            """SELECT application_attempts.* FROM application_attempts
               JOIN applications ON applications.id = application_attempts.application_id
               WHERE application_attempts.id = ? AND applications.user_id = ?""",
            (attempt_id, user_id),
        ).fetchone()


# -- pending_attempts ------------------------------------------------------------

def create_pending_attempt(user_id: int, company_name: "str | None", role_name: "str | None",
                            missing_fields: list, output_format: str, download_name: str,
                            folder_path: str, has_cover_letter: bool, created_at: str) -> sqlite3.Row:
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO pending_attempts
               (user_id, company_name, role_name, missing_fields, output_format, download_name,
                folder_path, has_cover_letter, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, company_name, role_name, json.dumps(missing_fields), output_format,
             download_name, folder_path, int(has_cover_letter), created_at),
        )
        return conn.execute("SELECT * FROM pending_attempts WHERE id = ?", (cur.lastrowid,)).fetchone()


def list_pending_attempts(user_id: int) -> list:
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM pending_attempts WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ).fetchall()


def get_pending_attempt(pending_id: int, user_id: int) -> "sqlite3.Row | None":
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM pending_attempts WHERE id = ? AND user_id = ?", (pending_id, user_id)
        ).fetchone()


def delete_pending_attempt(pending_id: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM pending_attempts WHERE id = ?", (pending_id,))
