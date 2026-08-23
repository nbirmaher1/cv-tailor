-- SQLite schema for cv-tailor's account/persistence layer.
-- Actual CV/cover-letter files and JSON content records live on the filesystem
-- under data/users/<user_id>/... ; this only tracks accounts, sessions, and the
-- metadata needed to organize and look those files up.

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,          -- sha256 hex of the opaque cookie token
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  expires_at TEXT NOT NULL              -- fixed at login (now + 30 days)
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- Row's mere existence = "user has saved a master CV". Actual JSON/photo live
-- on disk at data/users/<user_id>/master_cv/{content.json,photo.<ext>}.
CREATE TABLE IF NOT EXISTS master_cv (
  user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  company_name TEXT NOT NULL,
  company_slug TEXT NOT NULL,
  role_name TEXT NOT NULL,
  role_slug TEXT NOT NULL,
  status TEXT,                          -- NULL, or 'applied' (moved to the Applications tab); more values
                                         -- (interviewing/rejected/offer) reserved for future tracking
  applied_attempt_id INTEGER REFERENCES application_attempts(id) ON DELETE SET NULL,
                                         -- which tailored CV was actually used to apply, when status='applied'
  applied_at TEXT,                      -- when it was moved to the Applications tab; NULL until then
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE(user_id, company_slug, role_slug)
);
CREATE INDEX IF NOT EXISTS idx_applications_user ON applications(user_id);

-- No version-number column: created_at (and the attempt_slug folder name derived
-- from it) is the only ordering/identity concept for an attempt, so the date is
-- visible everywhere -- filesystem path, DB row, and UI -- instead of an opaque v2.
CREATE TABLE IF NOT EXISTS application_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
  output_format TEXT NOT NULL,
  download_name TEXT NOT NULL,
  folder_path TEXT NOT NULL UNIQUE,     -- relative to PROJECT_ROOT; folder name itself is the timestamp
  has_cover_letter INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL              -- same timestamp used in the folder name
);
CREATE INDEX IF NOT EXISTS idx_attempts_application ON application_attempts(application_id);

-- A run that finished successfully but couldn't be filed because company_name
-- and/or role_name couldn't be determined. Not nested under applications/
-- application_attempts since it has no slug pair yet. Resolved via a PATCH that
-- deletes this row and inserts the normal applications/application_attempts pair.
CREATE TABLE IF NOT EXISTS pending_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  company_name TEXT,                    -- NULL if this is one of the missing fields
  role_name TEXT,                       -- NULL if this is one of the missing fields
  missing_fields TEXT NOT NULL,         -- JSON array, e.g. ["company_name"]
  output_format TEXT NOT NULL,
  download_name TEXT NOT NULL,
  folder_path TEXT NOT NULL UNIQUE,     -- data/users/<user_id>/pending/<attempt_slug>/
  has_cover_letter INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL              -- same timestamp as the folder's attempt_slug
);
CREATE INDEX IF NOT EXISTS idx_pending_user ON pending_attempts(user_id);
