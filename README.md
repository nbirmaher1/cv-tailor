# cv-tailor

Tailors your CV to a specific job posting and renders it as a PDF or DOCX — with an independent review pass to catch fabricated claims or weak tailoring before you see the result. Available two ways: as a Claude Code skill, or as a local web app (drag-and-drop CV + paste a job URL).

Runs entirely on your own Claude Code subscription — no separate API key, no extra billing. Anyone with Claude Code installed and logged in can clone this repo and run it on their own machine the same way.

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

Then open http://127.0.0.1:8420 — drag in your CV (PDF or .docx, up to 10 MB), paste a job posting URL (or the job description text if the URL can't be fetched), optionally add a photo (up to 5 MB), choose PDF or DOCX, optionally give the output file a custom name (defaults to `tailored_cv` if left blank; unsafe characters are stripped server-side), and submit. You can cancel a run in progress at any time. Once done, the app shows what changed and why (and what the independent review pass caught, if anything), plus an inline PDF preview (Word output doesn't support in-browser preview — download to view it), before you commit to downloading.

Each request runs in an isolated `runs/<id>/` folder and shells out to `claude -p` headlessly with tool access scoped to just what the skill needs (Read/Write/WebFetch/Agent and the two render scripts) — it does not have general Bash access, which matters since job posting content is untrusted external text. Run folders (your uploaded CV/photo and the output) are deleted right after you download the result or cancel; abandoned or failed runs are swept on a 2-hour timer.

Only reachable from your own machine (`127.0.0.1`) — it's not a hosted/multi-tenant service. If you want to use it from another device, you'd need to run it there too, with that machine's own Claude Code login.

## Usage: Claude Code skill

In Claude Code, from anywhere:

```
/tailor-cv
```

then give it your CV file, a job posting URL or pasted text, and optionally a photo and/or output format (PDF or DOCX).

## How it works

1. CV parsing: PDFs are read directly; `.docx` files are extracted via `scripts/extract_docx.py`.
2. Job posting: fetched via WebFetch; falls back to asking you to paste the text if the site blocks fetching (common on LinkedIn).
3. The CV is turned into a canonical structured JSON (name, summary, experience, education, skills, languages, any other distinct sections, photo path) and tailored to the job — rephrasing/reordering/re-emphasizing only, never inventing experience.
4. An independent subagent reviews that JSON against the original CV and the job posting for unsupported claims, weak relevance, or dropped content, and the draft is revised (up to 2 review rounds) before proceeding.
5. Rendering:
   - **PDF**: the JSON fills `templates/default.html`, converted to PDF via Playwright/Chromium (`scripts/render_pdf.py`).
   - **DOCX**: the JSON is rendered directly via `python-docx` (`scripts/render_docx.py`) — styling is simpler than the PDF since Word doesn't give the same CSS-level control.
6. The rendered output is visually (PDF) or structurally (DOCX) checked for issues before being handed back, along with a short summary of what changed and why (surfaced in the web app's success screen; reported in chat for the skill).

## Customizing the look

Edit `templates/default.html` (plain HTML/CSS) for the PDF layout, or `scripts/render_docx.py` for the DOCX layout.

## Running tests

```
./venv/bin/pip install -r requirements-dev.txt
./venv/bin/pytest
```

Covers the deterministic Python code — the render/extract scripts and the web app's request handling — with no `claude` login or network access required. A GitHub Actions workflow (`.github/workflows/ci.yml`) runs the same suite on every push/PR to `main`.

This does **not** cover the actual CV tailoring (SKILL.md's parsing/tailoring/review steps) — that only runs through a live, authenticated `claude` CLI session, which CI intentionally doesn't have (see "Runs entirely on your own Claude Code subscription" above). Verify tailoring quality manually, e.g. by running the web app locally.
