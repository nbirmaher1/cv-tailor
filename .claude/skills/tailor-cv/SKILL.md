---
name: tailor-cv
description: Tailor a CV/resume to a specific job posting and render it as a styled PDF or DOCX. Use when the user gives a CV file (PDF/docx), a job posting (URL or pasted text), and wants a tailored version of their resume for that job.
---

# Tailor CV to a job posting

Inputs: a CV file path (PDF or .docx), a job description (a URL, or pasted text if no URL works), optionally a photo file to include, and optionally an output format (`pdf` or `docx`; default `pdf`) and/or explicit output paths. When invoked directly by a user (not the web app), ask for whatever of these wasn't given.

Project root: `~/projects/cv-tailor`. Python venv (has `playwright`, `python-docx`): `~/projects/cv-tailor/venv/bin/python3`.

## Steps

1. **Read the CV.**
   - If the file is a `.pdf`, read it directly with the Read tool (it supports PDF).
   - If the file is a `.docx`, extract its text with:
     `~/projects/cv-tailor/venv/bin/python3 ~/projects/cv-tailor/scripts/extract_docx.py <path>`

2. **Get the job description.**
   - If given a URL, try WebFetch on it first.
   - LinkedIn and some ATS pages often block fetching or return a login wall / near-empty content. If the fetch fails, returns very little text, or looks like a login page, tell the user and ask them to paste the job description text instead. Don't guess at job content.
   - If given raw pasted text already, use it directly.

3. **Extract structure from the CV into a canonical JSON content record** (this is the single source of truth used for either output format later), preserving the CV's actual section structure rather than collapsing everything into a fixed set of buckets:

   ```json
   {
     "full_name": "", "target_title": "", "email": "", "phone": "", "location": "", "links": "",
     "photo_path": null,
     "summary": "",
     "experience": [{"title": "", "company": "", "location": "", "dates": "", "bullets": [""]}],
     "education": [{"degree": "", "school": "", "location": "", "dates": ""}],
     "skills": [{"category": null, "items": [""]}],
     "languages": [""],
     "extra_sections": [{"heading": "", "items": [""]}]
   }
   ```

   - `skills` may have multiple groups if the source CV labels subcategories (e.g. "Analysis & Visualization" vs "Big Data & Engineering"); use a single group with `category: null` if it doesn't.
   - **If the source CV has Languages as its own distinct section or clearly-separate subsection, put it in `languages`, not folded into `skills`.** Leave `languages` empty if the source CV has none.
   - Any other distinct section in the source CV (certifications, projects, publications, etc.) goes in `extra_sections` as its own entry — never merged into the nearest similar-looking bucket.
   - If a photo file was provided as input, set `photo_path` to its absolute path; otherwise leave it `null`.

4. **Tailor the content to the job posting** (this JSON is a draft — it gets reviewed in step 5):
   - Rewrite `summary` to speak directly to the role.
   - Reorder and rephrase experience bullets to foreground what's relevant to this job; tighten or cut bullets that aren't relevant.
   - Adjust skills wording/order to match the posting's terminology where it's a genuine match.
   - **Never invent experience, skills, titles, or dates that aren't in the source CV.** Rephrasing and re-emphasizing existing content is fine; fabrication is not. If the CV is missing something the job clearly wants, leave it out rather than inventing it.

5. **Review the draft JSON with an independent subagent.** Use the Agent tool (`subagent_type: general-purpose`, run in the foreground — you need its result before continuing) with a fully self-contained prompt that includes: the complete original CV text, the complete job description text, and the tailored draft JSON. Ask it to check, and report back explicitly:
   - **Accuracy**: every claim in the draft (skills, achievements, titles, dates, responsibilities) must be traceable to the original CV. Rephrasing/reordering/re-emphasizing is fine; anything added, exaggerated, or not supported by the source is not — list each such claim.
   - **Relevance**: does the draft actually address the job posting's key requirements/skills/responsibilities? Flag major requirements the draft ignores when the source CV did have relevant material for them, and flag any rewrites that are generic filler rather than a genuine match.
   - **Completeness**: nothing important got dropped (contact info, distinct sections like languages/certifications, etc.).
   - Have it respond with either `PASS` or a specific, actionable list of fixes (quote the problematic text and say what's wrong).

   If the review returns fixes, revise the JSON accordingly and run the review subagent once more on the revised draft. Cap it at 2 review rounds total — if issues remain after the second round, proceed with the best version and note the unresolved concern in your summary to the user rather than looping indefinitely. Keep track of what was flagged and fixed so you can summarize it in step 9.

6. **Determine the output format** (`pdf` or `docx`, default `pdf` if not specified) and output paths (default `~/projects/cv-tailor/output/<short-job-slug>.<ext>` unless explicit paths were given as input).

7. **Render:**
   - **PDF path**: read `~/projects/cv-tailor/templates/default.html` (unless the user pointed you at a different style template) and produce a filled copy with the reviewed JSON content substituted for the `{{PLACEHOLDER}}` tokens — repeat the example entry blocks (Experience, Education, Skills) per item, most-recent-first for experience/education. If `photo_path` is set, keep the `.header`/`.photo` markup; if not, drop the photo `<img>` and header wrapper entirely (don't leave a broken image). Write the filled HTML to `<output>.html`, then render:
     `~/projects/cv-tailor/venv/bin/python3 ~/projects/cv-tailor/scripts/render_pdf.py <output>.html <output>.pdf`
   - **DOCX path**: write the reviewed JSON content record to `<output>.json` (matching the schema in step 3 exactly, `photo_path` included if set), then render:
     `~/projects/cv-tailor/venv/bin/python3 ~/projects/cv-tailor/scripts/render_docx.py <output>.json <output>.docx`

8. **Visually inspect the rendered output before presenting it.**
   - For PDF: use the Read tool on the output PDF and actually look at the rendered page(s) — don't just trust that the HTML was well-formed. Check for uneven spacing around separators/bullets, overlapping or cut-off text, awkward gaps from empty fields, content spilling onto a near-empty extra page, or anything that would look unpolished on a real job application. If you find a template-level bug, fix `templates/default.html` too so future runs don't repeat it.
   - For DOCX: there's no direct visual read — instead sanity-check the JSON content record itself (step 3/4 output) for structural issues (empty required fields, a section that ended up empty when the source had content, obviously mismatched data in the wrong field) before rendering, since that JSON is the entire input to the docx renderer.
   - Re-render if you find and fix anything. Only proceed to step 9 once the output actually looks right.

9. Tell the user where the output file is, briefly summarize what was changed and why (e.g. "led with your data-pipeline experience since the posting emphasizes ETL work; trimmed the unrelated retail bullet"), and include a short QA note from the review step (e.g. "review pass: no unsupported claims found" or "review caught and fixed one bullet that overstated scope").
