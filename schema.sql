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
  status TEXT,                          -- legacy; superseded by `stage` below, no longer written to.
                                         -- kept (rather than dropped) since SQLite ALTER TABLE DROP
                                         -- COLUMN support varies by version -- an unused column costs
                                         -- nothing, a failed migration on someone's existing DB would.
  stage TEXT NOT NULL DEFAULT 'tailored', -- 'tailored' | 'applied' | 'screening' | 'interviewing' |
                                         -- 'offer' | 'archived'
  archived_reason TEXT,                 -- 'rejected' | 'ghosted' | 'declined' | 'withdrawn';
                                         -- only meaningful when stage='archived'
  applied_attempt_id INTEGER REFERENCES application_attempts(id) ON DELETE SET NULL,
                                         -- which tailored CV was actually used to apply, once stage
                                         -- has reached 'applied' or beyond
  applied_at TEXT,                      -- when it was moved to the Applications tab; NULL until then
  salary_min INTEGER,
  salary_max INTEGER,
  salary_currency TEXT DEFAULT 'USD',
  location TEXT,
  work_model TEXT,                      -- 'remote' | 'hybrid' | 'onsite'
  job_url TEXT,
  job_description_raw TEXT,             -- persisted verbatim at tailoring time; no AI involved
  notes TEXT,
  follow_up_due_date TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
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

-- Auto-logged timeline for an application -- v1 only ever writes 'stage_change' entries
-- (see db.update_application_stage), but activity_type is free text so future entry
-- kinds (manual notes, follow-ups sent, ...) don't need a schema change.
CREATE TABLE IF NOT EXISTS application_activities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
  activity_type TEXT NOT NULL,
  description TEXT NOT NULL,
  event_date TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_activities_app ON application_activities(application_id);

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

-- Verified, CV-matched job openings found by the job-search helper. Persisted so the Job
-- Search screen survives reloads and later searches de-dupe against what's already found
-- (dedup_key = a normalized company|role|url fingerprint). The heavy detail (why_fits,
-- requirements) is small JSON kept inline -- there are no files on disk for a lead.
CREATE TABLE IF NOT EXISTS job_leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  dedup_key TEXT NOT NULL,              -- normalized fingerprint; UNIQUE per user (upsert on re-find)
  company_name TEXT NOT NULL,
  role_name TEXT NOT NULL,
  location TEXT,
  work_model TEXT,                      -- 'remote' | 'hybrid' | 'onsite' | ''
  url TEXT NOT NULL,                    -- authoritative apply/posting URL (verified live)
  source TEXT,                          -- greenhouse | lever | ashby | workday | ... | company-site
  posted_date TEXT,
  match_score INTEGER NOT NULL DEFAULT 0,  -- CV-fit score 0-100; list is sorted by this, highest first
  match_level TEXT,                     -- 'strong' | 'possible' | 'stretch' | ''
  why_fits TEXT,
  requirements TEXT,                    -- JSON array of {name,status,evidence}
  dismissed INTEGER NOT NULL DEFAULT 0,
  tailored_run_id TEXT,                 -- set once the user tailors a CV for this lead
  found_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE(user_id, dedup_key)
);
CREATE INDEX IF NOT EXISTS idx_job_leads_user ON job_leads(user_id);
