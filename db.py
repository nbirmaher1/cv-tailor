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
    _migrate_applications_columns()


# schema.sql's CREATE TABLE IF NOT EXISTS is a no-op against a database that
# already has an `applications` table from before one of these columns
# existed -- add each by hand for anyone upgrading an existing local install.
_APPLICATIONS_COLUMN_MIGRATIONS = [
    ("applied_attempt_id", "INTEGER REFERENCES application_attempts(id) ON DELETE SET NULL"),
    ("applied_at", "TEXT"),
    ("stage", "TEXT NOT NULL DEFAULT 'tailored'"),
    ("archived_reason", "TEXT"),
    ("salary_min", "INTEGER"),
    ("salary_max", "INTEGER"),
    ("salary_currency", "TEXT DEFAULT 'USD'"),
    ("location", "TEXT"),
    ("work_model", "TEXT"),
    ("job_url", "TEXT"),
    ("job_description_raw", "TEXT"),
    ("notes", "TEXT"),
    ("follow_up_due_date", "TEXT"),
    # No NOT NULL/default here even though schema.sql's fresh-install CREATE TABLE has
    # one -- SQLite's ALTER TABLE ADD COLUMN rejects a non-constant default (a strftime()
    # call) even under NOT NULL; backfilled separately below instead.
    ("updated_at", "TEXT"),
]


def _migrate_applications_columns() -> None:
    with _conn() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(applications)")}
        for name, ddl in _APPLICATIONS_COLUMN_MIGRATIONS:
            if name not in columns:
                conn.execute(f"ALTER TABLE applications ADD COLUMN {name} {ddl}")
        conn.execute("UPDATE applications SET updated_at = created_at WHERE updated_at IS NULL")
        # One-time backfill for anyone upgrading from before `stage` existed: promote
        # the legacy status='applied' rows so they don't silently vanish from the new
        # stage-aware Applications view. Naturally idempotent -- a row already migrated
        # has stage != 'tailored' (its just-added default), so re-running is a no-op.
        conn.execute("UPDATE applications SET stage = 'applied' WHERE status = 'applied' AND stage = 'tailored'")


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
                "id": a["id"],
                "role_name": a["role_name"],
                "role_slug": a["role_slug"],
                "status": a["status"],
                "stage": a["stage"],
                "archived_reason": a["archived_reason"],
                "applied_attempt_id": a["applied_attempt_id"],
                "applied_at": a["applied_at"],
                "salary_min": a["salary_min"],
                "salary_max": a["salary_max"],
                "salary_currency": a["salary_currency"],
                "location": a["location"],
                "work_model": a["work_model"],
                "job_url": a["job_url"],
                "job_description_raw": a["job_description_raw"],
                "notes": a["notes"],
                "follow_up_due_date": a["follow_up_due_date"],
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
        # attempts are already created_at DESC (see the query above), so each
        # role's own attempts[0] is its latest -- sort roles within a company,
        # then companies themselves, by that same "most recently tailored" key.
        def _latest(role):
            return role["attempts"][0]["created_at"] if role["attempts"] else ""

        result = list(companies.values())
        for company in result:
            company["roles"].sort(key=_latest, reverse=True)
        result.sort(key=lambda c: max((_latest(r) for r in c["roles"]), default=""), reverse=True)
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


def find_application_by_id(application_id: int, user_id: int) -> "sqlite3.Row | None":
    """Ownership-checked lookup by primary key."""
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM applications WHERE id = ? AND user_id = ?", (application_id, user_id)
        ).fetchone()


def mark_application_applied(application_id: int, attempt_id: int) -> "sqlite3.Row | None":
    """Records that `attempt_id` is the tailored CV actually used to apply, and
    stamps *now* as the applied date. Safe to call again on an already-applied
    application to switch which attempt is "the" applied one (this also
    refreshes applied_at to now) -- callers are responsible for verifying both
    ids belong to the same user/application first.

    Promotes stage to 'applied' only if it's still at the default 'tailored' --
    re-picking which attempt counts as "the applied one" for a role already
    further along (e.g. interviewing) must not regress its stage."""
    with _conn() as conn:
        row = conn.execute("SELECT stage FROM applications WHERE id = ?", (application_id,)).fetchone()
        was_tailored = row is not None and row["stage"] == "tailored"
        conn.execute(
            """UPDATE applications SET status = 'applied', applied_attempt_id = ?,
               applied_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
               stage = CASE WHEN stage = 'tailored' THEN 'applied' ELSE stage END,
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?""",
            (attempt_id, application_id),
        )
        if was_tailored:
            conn.execute(
                "INSERT INTO application_activities (application_id, activity_type, description) "
                "VALUES (?, 'stage_change', 'Moved to applied')",
                (application_id,),
            )
        return conn.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()


def unmark_application_applied(application_id: int) -> "sqlite3.Row | None":
    """Full undo, matching its existing framing in the UI ("Applied -- remove"):
    resets status/attempt/date and drops stage back to 'tailored' unconditionally,
    however far along it had gotten."""
    with _conn() as conn:
        conn.execute(
            """UPDATE applications SET status = NULL, applied_attempt_id = NULL, applied_at = NULL,
               stage = 'tailored', updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?""",
            (application_id,),
        )
        return conn.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()


def update_application_stage(application_id: int, stage: str, description: "str | None" = None) -> "sqlite3.Row | None":
    """Free-form stage transition (any stage to any stage) -- real job searches don't
    move linearly (ghosting, re-opened roles, correcting a mis-click). Callers are
    responsible for only allowing this once an application has actually been applied
    (see routes_applications.py's guard); this function itself doesn't re-check that."""
    with _conn() as conn:
        conn.execute(
            "UPDATE applications SET stage = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (stage, application_id),
        )
        conn.execute(
            "INSERT INTO application_activities (application_id, activity_type, description) VALUES (?, 'stage_change', ?)",
            (application_id, description or f"Moved to {stage}"),
        )
        return conn.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()


def update_application_details(application_id: int, **fields) -> "sqlite3.Row | None":
    """Partial update over the manual CRM fields (salary/location/work_model/notes/
    follow_up_due_date/archived_reason) -- only touches columns actually passed."""
    allowed = {
        "salary_min", "salary_max", "salary_currency", "location", "work_model",
        "notes", "follow_up_due_date", "archived_reason",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    with _conn() as conn:
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(
                f"UPDATE applications SET {set_clause}, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                (*updates.values(), application_id),
            )
        return conn.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()


def set_application_job_details(application_id: int, job_url: "str | None", job_description_raw: "str | None") -> None:
    """Persists the job posting text/URL verbatim, no AI involved -- called once when the
    application row is created (and again on any later re-tailor, keeping the most recent
    posting text)."""
    with _conn() as conn:
        conn.execute(
            "UPDATE applications SET job_url = ?, job_description_raw = ? WHERE id = ?",
            (job_url or None, job_description_raw or None, application_id),
        )


def list_application_activities(application_id: int) -> list:
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM application_activities WHERE application_id = ? ORDER BY event_date DESC",
            (application_id,),
        ).fetchall()


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
