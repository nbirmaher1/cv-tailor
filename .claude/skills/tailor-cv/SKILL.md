---
name: tailor-cv
description: Tailor a CV/resume to a specific job posting and render it as a styled PDF or DOCX. Use when the user gives a CV file (PDF/docx), a job posting (URL or pasted text), and wants a tailored version of their resume for that job.
---

# Tailor CV to a job posting

Inputs: CV file (PDF or .docx) *or* an already-parsed content record (see step 1), job description (URL, or pasted text if no URL works), optional photo file, optional candidate notes (contact/location/visa updates, extra detail not in the CV), optional output format (`pdf`/`docx`, default `pdf`) and/or explicit output paths. Ask for whatever required input is missing (notes are optional — don't block on them).

Project root: `~/projects/cv-tailor`. Venv (`playwright`, `python-docx`, `pypdf`): `~/projects/cv-tailor/venv/bin/python3`. Industry standards reference: `cv-standards.md` (same directory as this file) — read it before step 4, and pass it to the review subagent in step 5. If a cover letter is requested (the web app's prompt handles this, not a numbered step here), `cover-letter-standards.md` (same directory) is the equivalent rule set and review checklist for it.

## Steps

1. **Read the CV.** `.pdf` → Read tool directly. `.docx` → `venv/bin/python3 scripts/extract_docx.py <path>`.

   If the caller instead points you at an already-parsed content record (the web app does this once a candidate has saved a master CV — no raw CV file is given), skip this step and step 3's extraction entirely: read that record directly and treat it as step 3's output.

2. **Get the job description.** URL → WebFetch. If it fails, returns near-nothing, or looks like a login wall (common on LinkedIn/ATS pages), ask the user to paste the text instead — don't guess at job content. Pasted text → use as-is.

   If the caller also gave a metadata JSON path (the web app does, so it can file the result under a company/role folder), determine the company name and role/title from the job posting now, respecting any override the caller already gave verbatim, and write `{"company_name": "", "role_name": ""}` there. Leave a field `""` if it genuinely can't be determined — never guess a plausible-looking name.

3. **Extract the CV into a canonical JSON content record** (single source of truth for both output formats; preserve the CV's actual section structure rather than forcing it into fixed buckets):

   ```json
   {
     "full_name": "", "target_title": "", "email": "", "phone": "", "location": "", "links": "",
     "work_authorization": "",
     "photo_path": null,
     "summary": "",
     "experience": [{"title": "", "company": "", "location": "", "dates": "", "bullets": [""]}],
     "education": [{"degree": "", "school": "", "location": "", "dates": ""}],
     "skills": [{"category": null, "items": [""]}],
     "languages": [""],
     "extra_sections": [{"heading": "", "items": [""]}]
   }
   ```

   - `work_authorization`: short inline contact-line note (e.g. "EU citizen — no visa sponsorship needed"); leave `""` unless the CV or notes actually say something about it.
   - `skills`: multiple groups if the source categorizes them, else one group with `category: null`.
   - `languages`: only if the source has a distinct languages section/subsection; otherwise leave empty (don't fold into skills).
   - `extra_sections`: any other distinct source section (certifications, projects, publications, etc.) — never merged into a similar-looking bucket.
   - Set `photo_path` if a photo was provided, else `null`.
   - **Apply candidate notes here**, before step 4: update fields they explicitly override (location, contact info, work authorization), fold the rest into the closest existing field/bullet. Treat notes as first-person authoritative statements to include, not claims to verify — but they must actually appear in the output, not just inform your thinking.
   - **Long "master CV" sources**: extract everything faithfully at this stage, no pre-filtering — selection happens in step 4, and anything dropped here can't be recovered later.
   - Normalize `location` to "City, Country" (or "City, State" for the US); drop street address/postal code. This is formatting, not tailoring — apply regardless of the job, never invent a location.

4. **Tailor the content to the job posting** (draft — gets reviewed in step 5). Apply `cv-standards.md`'s "Relevance, prioritization, and duplication" and "Content quality" sections in full — they're the actual rule set for this step, not background reading. Work in this order:
   - Extract the JD's 8-10 most critical required skills/tools/responsibilities — this list drives every decision below and is the primary place keywords should live once woven into rewritten bullets.
   - Triage every role and bullet per the standards' relevance test; cut what fails it.
   - Deduplicate per the standards' zero-tolerance rule (within a role) and near-duplicate rule (across roles).
   - Apply the bullet caps, ranking competing bullets per the standards' ranking order when cutting to the cap, and order survivors strongest-and-most-relevant-first within each role.
   - Rewrite every surviving bullet per the standards' achievement-framing, tense, verb, metric, and length-cap rules.
   - Rewrite the summary per the standards' summary rule.
   - Apply the Skills-section cap per the standards.
   - Apply the page-length rule per the standards (compute years of relevant experience as defined there).
   - **Never invent experience, skills, titles, or dates.** Omit what's missing rather than fabricate it.

5. **Independent review.** Use the Agent tool (`subagent_type: general-purpose`, foreground) with a self-contained prompt: full original CV text, full JD text, any candidate notes, the draft JSON, and the contents of `cv-standards.md`. Ask it to check and report `PASS` or a specific, quoted, actionable fix list for:
   - **Accuracy**: every claim traceable to the CV or notes; flag anything added/exaggerated/unsupported, including JD phrasing that overstates what the original bullet said.
   - **Relevance mapping — a mandatory, explicit check, not optional feedback.** For every bullet in every kept role, the reviewer must state which extracted JD requirement it supports, or why it's a strong scope/seniority signal per cv-standards.md's bullet relevance test. Any bullet without a stated justification is a required fix: cut it or replace it with better-supporting material from the source. This check must be performed and its result stated even when everything else passes.
   - **Selection ranking**: for any role at or near its bullet cap, confirm competing bullets were cut using cv-standards.md's ranking order (requirement match > quantifiable outcome > recency/seniority > uniqueness), not arbitrarily; and that surviving bullets are ordered strongest-first.
   - **Duplicate bullets — a mandatory, explicit check, not optional feedback.** For every role with 2+ bullets, the reviewer must directly compare each bullet against every other bullet in that role and state whether any pair covers the same underlying deliverable/project/system, even when phrased very differently (e.g. "built X" vs. "owned X" vs. "drove adoption of X" — all the same X). Any such pair is a required fix: specify exactly which bullets to merge and what the merged bullet should say. Also check for identical achievements restated across different roles. This check must be performed and its result stated even when everything else passes — do not let it get absorbed into a general "bullet quality" skim.
   - **Voice/AI-tell check — a mandatory, explicit check, not optional feedback.** Scan the full draft (summary and every bullet) for any em dash, any of cv-standards.md's Voice section banned phrases, or a "robust"/"dynamic"/"leverage" used as vague self-description rather than a source-backed technical claim. Any hit is a required fix: quote it and state the replacement. This check must be performed and its result stated even when everything else passes.
   - **Bullet quality/caps**: duty-framing, no-impact bullets, grandiose/buzzword verbs that don't read like plain human language, anything that'll wrap past 2 lines, roles over their cap, Skills over its cap per cv-standards.md.
   - **Standards compliance** (per `cv-standards.md`): length matches the candidate's years of relevant experience (1 page under 10 years, exactly 2 at 10+, never a ~1.5-page spill); skills are demonstrated in bullets rather than only listed; the summary is genuinely re-tailored to this posting, not generic.
   - **Completeness (of what's relevant, not the whole source)**: contact info or still-relevant sections missing; notes not reflected. Intentional dropping of low-relevance content from a long source is expected, not a completeness bug — only flag a drop that removed the CV's only evidence for an explicit JD requirement.
   
   Have it report as a compact list (role / bullet / issue / fix) rather than open prose, so its
   findings are quicker to apply.

   Revise and re-review once more if fixes are returned (max 2 rounds total). If issues remain after that, proceed with the best version and note the unresolved concern in step 9. If the caller specified a content JSON path (the web app does, so it can later apply a targeted candidate revision without re-tailoring from scratch), write the final reviewed record there.

6. **Determine output format** (`pdf`/`docx`, default `pdf`) and paths (default `output/<job-slug>.<ext>` unless given).

7. **Render:**
   - **PDF**: fill `templates/default.html` placeholders with the reviewed JSON (repeat entry blocks per item, most-recent-first). Drop the photo markup entirely if no photo; drop empty contact-line fields (don't leave a stray separator). Don't introduce multi-column layouts, tables, icons, or graphical skill bars — single-column with standard headers is deliberate for ATS parsing. Write `<output>.html`, then:
     `venv/bin/python3 scripts/render_pdf.py <output>.html <output>.pdf` — note the printed page count for step 8.
   - **DOCX**: write `<output>.json` (same schema), then:
     `venv/bin/python3 scripts/render_docx.py <output>.json <output>.docx`

8. **QA the actual rendered output before presenting it, and enforce the length policy against it — not the JSON.**
   - **PDF**: Read the rendered PDF and look at the page(s) — check spacing, overlap/cutoff text, empty-field gaps, thin extra pages. Fix `templates/default.html` if it's a template bug. Treat the page count as a strict gate per `cv-standards.md`'s length rule — a spilled ~1.5 pages or 3+ pages is never acceptable regardless of experience.
   - **DOCX**: run `venv/bin/python3 scripts/docx_stats.py <output>.json` (bullet count, narrative word count) as a length proxy — comfortably-1-page tends to land ~550-650 words / <16-18 bullets. Apply the same 1-page/2-page test. Also sanity-check the JSON for structural issues (empty required fields, a section blank when the source had content).
   - **If cutting further**, follow `cv-standards.md`'s cut order and ranking. Re-render and recheck after each round, capped at 2 additional cut-and-recheck iterations. If still over after that: proceed and note it honestly if genuinely justified, otherwise cut harder rather than ship an unjustified overage.

9. **Report back**: where the output file is, a brief summary of what changed and why (e.g. "led with your data-pipeline experience since the posting emphasizes ETL work; trimmed the unrelated retail bullet"), a short QA note from step 5 (e.g. "review caught and fixed one bullet that overstated scope"), and — for master-CV sources — a high-level note on what was condensed (e.g. "condensed from 6 roles to the 3 most relevant, ~40 bullets down to ~12"). If the caller specified a summary JSON path (the web app does), also write this same report there as `{"summary": "...", "changes": ["...", ...], "review_note": "...", "requirements": [...]}` — each `changes` bullet one short line, written for the candidate to read, not an internal note. `requirements`: for each requirement extracted in step 4, `{"name": "...", "status": "matched"|"listed_only"|"missing", "evidence": "short excerpt of the bullet that demonstrates it, omit for listed_only/missing"}` — a rollup of the relevance-mapping already done in step 5, not a new judgment call.
