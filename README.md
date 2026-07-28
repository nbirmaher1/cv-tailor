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

Not happy with something? Describe the change in the "Want something changed?" box (e.g. "shorten the summary", "remove the certification bullet") and the app applies just that edit — it loads the already-tailored record and makes the targeted change you asked for, rather than re-tailoring from scratch, and re-checks the length/formatting afterward. Up to 3 revisions per CV; if a revision is cancelled or fails, your previous version is kept untouched.

Check "Also generate a matching cover letter" (under "More options") to get one alongside the CV, in the same output format. You can optionally add guidance on what it should focus on (e.g. "mention my passion for climate tech", "keep it under 250 words") and/or upload a cover letter you've used before for the app to match the voice/structure of — it rewrites the substance for this job and candidate rather than reusing the old content. Once ready, a CV/Cover Letter tab switches the preview and download between the two; feedback in the revision box can target either one.

Checking "Intelligent cover letter" (nested under the cover letter checkbox) has the app research the hiring company first — its stated values, culture, mission, notable initiatives — and work 1-2 genuinely specific findings into the letter instead of generic flattery. It only cites things it actually finds; if research turns up nothing substantive, it falls back to a strong letter based on the job posting alone rather than inventing company culture claims. Takes a bit longer since it's doing real web research.

Each request runs in an isolated `runs/<id>/` folder and shells out to `claude -p` headlessly with tool access scoped to just what the skill needs (Read/Write/WebFetch/Agent and the render scripts) — it does not have general Bash access, which matters since job posting content is untrusted external text. WebSearch is granted only for a run with "Intelligent cover letter" checked, since ordinary tailoring never needs to search the open web. A revision request is scoped even further (Read/Write and the render scripts only, no WebFetch/WebSearch/Agent), since it's editing an already-tailored record rather than starting over. Run folders (your uploaded CV/photo/cover letter template and the output) are deleted right after you download the CV result or cancel; abandoned or failed runs are swept on a 2-hour timer.

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
7. A follow-up revision request re-loads the reviewed JSON record straight from disk and edits only what the feedback asked for, then re-renders and re-runs the length/QA check — it doesn't re-read the CV, re-fetch the job posting, or re-run the independent review.
8. If requested, a cover letter is generated from the already-tailored CV record and the job posting (not the raw CV file), optionally guided by your notes and/or an old cover letter used as a style/structure reference — never carrying over that old letter's company, role, or claims. It's rendered from its own simple template (`templates/cover_letter.html` for PDF, `scripts/render_cover_letter_docx.py` for DOCX) rather than the CV's, since a letter isn't shaped like a CV. A cover letter issue never blocks the CV from being delivered.
9. In "Intelligent" mode, before writing the letter Claude identifies the company from the job posting and uses WebSearch/WebFetch to find their own About/Careers/Values content or genuinely notable public statements, then works at most 1-2 concrete, actually-found signals into the letter — logged as a bullet in the "what we changed" summary when used.

## Customizing the look

Edit `templates/default.html` (plain HTML/CSS) for the PDF layout, or `scripts/render_docx.py` for the DOCX layout.

## Running tests

```
./venv/bin/pip install -r requirements-dev.txt
./venv/bin/pytest
```

Covers the deterministic Python code — the render/extract scripts and the web app's request handling — with no `claude` login or network access required. A GitHub Actions workflow (`.github/workflows/ci.yml`) runs the same suite on every push/PR to `main`.

This does **not** cover the actual CV tailoring (SKILL.md's parsing/tailoring/review steps) — that only runs through a live, authenticated `claude` CLI session, which CI intentionally doesn't have (see "Runs entirely on your own Claude Code subscription" above). Verify tailoring quality manually, e.g. by running the web app locally.
