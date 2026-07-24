---
name: tailor-cv
description: Tailor a CV/resume to a specific job posting and render it as a styled PDF or DOCX. Use when the user gives a CV file (PDF/docx), a job posting (URL or pasted text), and wants a tailored version of their resume for that job.
---

# Tailor CV to a job posting

Inputs: CV file (PDF or .docx), job description (URL, or pasted text if no URL works), optional photo file, optional candidate notes (contact/location/visa updates, extra detail not in the CV), optional output format (`pdf`/`docx`, default `pdf`) and/or explicit output paths. Ask for whatever required input is missing (notes are optional — don't block on them).

Project root: `~/projects/cv-tailor`. Venv (`playwright`, `python-docx`, `pypdf`): `~/projects/cv-tailor/venv/bin/python3`. Industry standards reference: `cv-standards.md` (same directory as this file) — read it before step 4, and pass it to the review subagent in step 5.

## Steps

1. **Read the CV.** `.pdf` → Read tool directly. `.docx` → `venv/bin/python3 scripts/extract_docx.py <path>`.

2. **Get the job description.** URL → WebFetch. If it fails, returns near-nothing, or looks like a login wall (common on LinkedIn/ATS pages), ask the user to paste the text instead — don't guess at job content. Pasted text → use as-is.

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

4. **Tailor the content to the job posting** (draft — gets reviewed in step 5):
   - **Read `cv-standards.md` first** — it's the industry quality bar for the rest of this step, not optional background.
   - Extract the JD's 8-10 most critical required skills/tools/responsibilities — this is what you'll weave into rewritten bullets (the primary place keywords should live) and the skills section, only where the CV genuinely supports it. Adopting JD terminology the CV doesn't back up is keyword-stuffing, not tailoring.
   - `summary`: 3-4 tight sentences — mirror the JD's title/domain, name genuinely-supported competencies from the JD list, include one concrete proof point (metric/scale/outcome) from the CV.
   - Judge every role and bullet against this job: **core** (keep, lead with it), **tangential** (keep trimmed), **irrelevant** (cut entirely). Cut whole irrelevant roles when other material covers the page.
   - **Bullet caps: 3-5 for core roles, 1-2 for tangential/older roles.** Rank by fit, cut to the cap — don't keep a bullet for being impressive if it's not a strong match.
   - **Merge bullets that describe the same underlying work** (e.g. building a dashboard and its adoption) into one stronger bullet — never keep both, even if worded differently.
   - Do relevance-based cutting first; tighten wording for space only after that.
   - **Every surviving bullet is an achievement, not a duty**: strong action verb (never "Responsible for"/"Worked on"), "achieved X by doing Y" shape, exact metrics from the source (never invented or rounded up). Cut bullets with no impact and no JD relevance. Translate technical detail into business impact only where the source states that outcome.
   - **Hard cap: ~2 rendered lines per bullet (~180-200 chars / 28-32 words).** Condense long source bullets/narrative paragraphs to fit rather than carrying them over verbatim.
   - **Skills section — cut hard, this is the fastest way the CV overflows:** 3-5 categories (or one flat list) × 3-5 items, ~20 items total hard cap. Tool/skill names only, no parenthetical scope explanations. Drop irrelevant categories/items; fold near-empty categories into a related one.
   - **Length is set by years of relevant experience, per `cv-standards.md`: under 10 years → strictly 1 page; 10+ years → exactly 2 pages.** Determine the candidate's years of relevant experience from the CV to apply this. A "spilled" ~1.5 pages is never acceptable either way — cut harder, or the experience genuinely earns a full 2.
   - **Never invent experience, skills, titles, or dates.** Omit what's missing rather than fabricate it.

5. **Independent review.** Use the Agent tool (`subagent_type: general-purpose`, foreground) with a self-contained prompt: full original CV text, full JD text, any candidate notes, the draft JSON, and the contents of `cv-standards.md`. Ask it to check and report `PASS` or a specific, quoted, actionable fix list for:
   - **Accuracy**: every claim traceable to the CV or notes; flag anything added/exaggerated/unsupported, including JD phrasing that overstates what the original bullet said.
   - **Relevance/selection**: ignored JD requirements the CV had material for; generic filler rewrites; for master-CV sources, low-relevance content kept/under-trimmed while stronger material got cut.
   - **Bullet quality/caps**: duty-framing, no-impact bullets, anything that'll wrap past 2 lines, roles over their cap, Skills over ~20 items/5 categories/with parenthetical scope, duplicate bullets covering the same work.
   - **Standards compliance** (per `cv-standards.md`): length matches the candidate's years of relevant experience (1 page under 10 years, exactly 2 at 10+, never a ~1.5-page spill); skills are demonstrated in bullets rather than only listed; the summary is genuinely re-tailored to this posting, not generic.
   - **Completeness (of what's relevant, not the whole source)**: contact info or still-relevant sections missing; notes not reflected. Intentional dropping of low-relevance content from a long source is expected, not a completeness bug — only flag a drop that removed the CV's only evidence for an explicit JD requirement.
   
   Revise and re-review once more if fixes are returned (max 2 rounds total). If issues remain after that, proceed with the best version and note the unresolved concern in step 9.

6. **Determine output format** (`pdf`/`docx`, default `pdf`) and paths (default `output/<job-slug>.<ext>` unless given).

7. **Render:**
   - **PDF**: fill `templates/default.html` placeholders with the reviewed JSON (repeat entry blocks per item, most-recent-first). Drop the photo markup entirely if no photo; drop empty contact-line fields (don't leave a stray separator). Don't introduce multi-column layouts, tables, icons, or graphical skill bars — single-column with standard headers is deliberate for ATS parsing. Write `<output>.html`, then:
     `venv/bin/python3 scripts/render_pdf.py <output>.html <output>.pdf` — note the printed page count for step 8.
   - **DOCX**: write `<output>.json` (same schema), then:
     `venv/bin/python3 scripts/render_docx.py <output>.json <output>.docx`

8. **QA the actual rendered output before presenting it, and enforce the length policy against it — not the JSON.**
   - **PDF**: Read the rendered PDF and look at the page(s) — check spacing, overlap/cutoff text, empty-field gaps, thin extra pages. Fix `templates/default.html` if it's a template bug. Treat the page count as a strict gate per `cv-standards.md`: under 10 years of relevant experience must land on exactly 1 page, 10+ years on exactly 2; a spilled ~1.5 pages is never acceptable; 3+ pages is never acceptable regardless of experience.
   - **DOCX**: run `venv/bin/python3 scripts/docx_stats.py <output>.json` (bullet count, narrative word count) as a length proxy — comfortably-1-page tends to land ~550-650 words / <16-18 bullets. Apply the same 1-page/2-page test. Also sanity-check the JSON for structural issues (empty required fields, a section blank when the source had content).
   - **If cutting further, cut in this order**: Skills to its cap → tangential/older bullets to their cap → core-role bullets to their cap. Re-render and recheck after each round, capped at 2 additional cut-and-recheck iterations. If still over after that: proceed and note it honestly if genuinely justified, otherwise cut harder rather than ship an unjustified overage.

9. **Report back**: where the output file is, a brief summary of what changed and why (e.g. "led with your data-pipeline experience since the posting emphasizes ETL work; trimmed the unrelated retail bullet"), a short QA note from step 5 (e.g. "review caught and fixed one bullet that overstated scope"), and — for master-CV sources — a high-level note on what was condensed (e.g. "condensed from 6 roles to the 3 most relevant, ~40 bullets down to ~12").
