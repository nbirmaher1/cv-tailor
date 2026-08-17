# cv-tailor

Tailors your CV to a specific job posting and renders it as a PDF or DOCX — with an independent review pass to catch fabricated claims or weak tailoring before you see the result. Available two ways: as a Claude Code skill, or as a local web app with accounts, a master CV you upload once and edit in-app, and every tailored CV automatically organized into a company/role folder tree.

Runs entirely on your own Claude Code subscription — no separate API key, no extra billing. Anyone with Claude Code installed and logged in can clone this repo and run it on their own machine the same way — each person runs their own instance against their own local `claude` login; "accounts" are local profiles on that instance, not a hosted/shared service.

## Setup

```
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/playwright install chromium
```

Requires the `claude` CLI installed and logged in (used for both usage modes below).

## Usage: web app

```
./venv/bin/python3 app.py
```

Then open http://127.0.0.1:8420. First time: create a local account (just an email/password on your own instance — see the setup note above) and upload your CV once as your **master CV**; it's parsed into a structured record you can edit anytime from the "My CV" page (every field — experience, education, skills, languages, other sections — plus an optional photo) without ever re-uploading.

To tailor: paste a job posting URL (or the job description text if the URL can't be fetched), optionally override the company/role it'll be filed under (auto-detected from the posting if you leave these blank), choose PDF or DOCX, optionally give the output file a custom name (defaults to `tailored_cv` if left blank; unsafe characters are stripped server-side), and submit. You can cancel a run in progress at any time. Once done, the app shows what changed and why (and what the independent review pass caught, if anything), plus an inline PDF preview (Word output doesn't support in-browser preview — download to view it), and confirms where it was filed (or, if the company/role genuinely couldn't be determined, prompts you for them so it can still be saved rather than lost).

Not happy with something? Describe the change in the "Want something changed?" box (e.g. "shorten the summary", "remove the certification bullet") and the app applies just that edit — it loads the already-tailored record and makes the targeted change you asked for, rather than re-tailoring from scratch, and re-checks the length/formatting afterward. Up to 3 revisions per CV; if a revision is cancelled or fails, your previous version is kept untouched.

Check "Also generate a matching cover letter" (under "More options") to get one alongside the CV, in the same output format. You can optionally add guidance on what it should focus on (e.g. "mention my passion for climate tech", "keep it under 250 words") and/or upload a cover letter you've used before for the app to match the voice/structure of — it rewrites the substance for this job and candidate rather than reusing the old content. Once ready, a CV/Cover Letter tab switches the preview and download between the two; feedback in the revision box can target either one.

Checking "Intelligent cover letter" (nested under the cover letter checkbox) has the app research the hiring company first — its stated values, culture, mission, notable initiatives — and work 1-2 genuinely specific findings into the letter instead of generic flattery. It only cites things it actually finds; if research turns up nothing substantive, it falls back to a strong letter based on the job posting alone rather than inventing company culture claims. Takes a bit longer since it's doing real web research.

Every application you've tailored for lives on the **Applications** page, organized exactly like a filesystem — a folder per company, and inside that a folder per role, with each attempt dated so you can see at a glance when it was done (re-tailoring for a company/role you've already applied to adds a new dated attempt rather than overwriting the old one). Anything that finished but couldn't be auto-filed shows up in a "Needs info" inbox there until you tell it the company/role.

A tailoring run shells out to `claude -p` headlessly in an ephemeral `runs/<id>/` scratch folder, with tool access scoped to just what the skill needs (Read/Write/WebFetch/Agent and the render scripts) — it does not have general Bash access, which matters since job posting content is untrusted external text. WebSearch is granted only for a run with "Intelligent cover letter" checked, since ordinary tailoring never needs to search the open web. A revision request is scoped even further (Read/Write and the render scripts only, no WebFetch/WebSearch/Agent), since it's editing an already-tailored record rather than starting over. On success, the scratch folder is moved into your permanent, organized storage under `data/users/<you>/applications/...` (or `data/users/<you>/pending/...` if unfiled) and kept there indefinitely — nothing is deleted after download. Abandoned or failed scratch runs are swept on a 2-hour timer; your saved master CV and filed applications never are.

Only reachable from your own machine (`127.0.0.1`) — it's not a hosted/multi-tenant service. If you want to use it from another device, you'd need to run it there too, with that machine's own Claude Code login and its own local accounts/data.

## Usage: Claude Code skill

In Claude Code, from anywhere:

```
/tailor-cv
```

then give it your CV file, a job posting URL or pasted text, and optionally a photo and/or output format (PDF or DOCX).

## How it works

1. CV parsing (once, when you first save your master CV — either mode): PDFs are read directly; `.docx` files are extracted via `scripts/extract_docx.py`. The web app reuses that saved record for every future tailoring run instead of re-parsing; the standalone skill still does this per run unless the caller points it at an already-parsed record.
2. Job posting: fetched via WebFetch; falls back to asking you to paste the text if the site blocks fetching (common on LinkedIn). The web app also determines the company/role here (or uses your override) so the result can be filed automatically.
3. The CV is turned into (or, for the web app, loaded as) a canonical structured JSON (name, summary, experience, education, skills, languages, any other distinct sections, photo path) and tailored to the job — rephrasing/reordering/re-emphasizing only, never inventing experience.
4. An independent subagent reviews that JSON against the original CV and the job posting for unsupported claims, weak relevance, or dropped content, and the draft is revised (up to 2 review rounds) before proceeding. Saving a master CV runs a lighter, one-time structural consistency check instead (impossible dates, garbled parsing, duplicates) since there's no job posting to tailor against yet.
5. Rendering:
   - **PDF**: the JSON fills `templates/default.html`, converted to PDF via Playwright/Chromium (`scripts/render_pdf.py`).
   - **DOCX**: the JSON is rendered directly via `python-docx` (`scripts/render_docx.py`) — styling is simpler than the PDF since Word doesn't give the same CSS-level control.
6. The rendered output is visually (PDF) or structurally (DOCX) checked for issues before being handed back, along with a short summary of what changed and why (surfaced in the web app's success screen; reported in chat for the skill).
7. A follow-up revision request re-loads the reviewed JSON record straight from disk and edits only what the feedback asked for, then re-renders and re-runs the length/QA check — it doesn't re-read the CV, re-fetch the job posting, or re-run the independent review.
8. On success, the web app moves the run's output out of ephemeral scratch space into your permanent, organized storage (`data/users/<you>/applications/<company>/<role>/<date>/`, or a dated `pending/` entry if the company/role couldn't be determined) and records it in a local SQLite database (`data/cvtailor.db`) so the Applications page can list it.
9. If requested, a cover letter is generated from the already-tailored CV record and the job posting (not the raw CV file), optionally guided by your notes and/or an old cover letter used as a style/structure reference — never carrying over that old letter's company, role, or claims. It's rendered from its own simple template (`templates/cover_letter.html` for PDF, `scripts/render_cover_letter_docx.py` for DOCX) rather than the CV's, since a letter isn't shaped like a CV. A cover letter issue never blocks the CV from being delivered.
10. In "Intelligent" mode, before writing the letter Claude identifies the company from the job posting and uses WebSearch/WebFetch to find their own About/Careers/Values content or genuinely notable public statements, then works at most 1-2 concrete, actually-found signals into the letter — logged as a bullet in the "what we changed" summary when used.

## Customizing the look

Edit `templates/default.html` (plain HTML/CSS) for the PDF layout, or `scripts/render_docx.py` for the DOCX layout.

## Running tests

```
./venv/bin/pip install -r requirements-dev.txt
./venv/bin/pytest
```

Covers the deterministic Python code — the render/extract scripts and the web app's request handling — with no `claude` login or network access required. A GitHub Actions workflow (`.github/workflows/ci.yml`) runs the same suite on every push/PR to `main`.

This does **not** cover the actual CV tailoring (SKILL.md's parsing/tailoring/review steps) — that only runs through a live, authenticated `claude` CLI session, which CI intentionally doesn't have (see "Runs entirely on your own Claude Code subscription" above). Verify tailoring quality manually, e.g. by running the web app locally.
