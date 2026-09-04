# Job-search standards (2026)

The single source of truth for the job-search helper's sources, its zero-false-results verification
protocol, and what counts as a strong match. The `job-search` SKILL.md runs this process; it applies
and checks these rules but does not restate them, so if a rule changes, change it here only.

The bar is absolute: **a returned job must be a real, currently-open posting at a company that
actually exists, confirmed on an authoritative source. There is zero tolerance for a fabricated,
stale, closed, or unverifiable result.** A smaller list of real jobs is always better than a longer
list with one job that isn't real.

## Sources (two tiers)

**Tier A — authoritative company ATS boards. Discovery AND verification.** These expose public,
login-free job feeds; a posting on a company's own board is by definition a real opening.
- Greenhouse: `boards.greenhouse.io/{company}` · JSON API `boards-api.greenhouse.io/v1/boards/{company}/jobs`
- Lever: `jobs.lever.co/{company}` · JSON API `api.lever.co/v0/postings/{company}?mode=json`
- Ashby: `jobs.ashbyhq.com/{company}`
- Workday: `*.myworkdayjobs.com/...` (per-tenant)
- SmartRecruiters: `careers.smartrecruiters.com/{company}`
- Common in NL/EU: Workable (`apply.workable.com/{company}`), Recruitee (`{company}.recruitee.com`),
  Personio (`{company}.jobs.personio.com`)

**Tier B — aggregators. Discovery ONLY, never a verified result on their own.** LinkedIn, Indeed,
Glassdoor are login-walled/anti-bot: a WebFetch of them usually fails, and even when it doesn't, the
listing can't be trusted as live. Use them only to *surface* that a company may be hiring a role —
then confirm that role on the company's own Tier-A board or careers page. **If an aggregator hit
cannot be confirmed on an authoritative company source, drop it.** Never present an aggregator URL as
the result.

The user may add extra sites; treat a company-owned careers/ATS URL as Tier A and a third-party board
as Tier B (discovery-only, still needs authoritative confirmation).

## Verification protocol (two independent steps — both mandatory)

A job is only eligible to be returned after passing BOTH:

1. **Authoritative fetch.** WebFetch the job's Tier-A source (its ATS board/API entry or the company's
   own careers page). Confirm all of: the page loads and is a real job posting (not a 404, a closed/
   expired notice, or a login wall); the company is a real, identifiable employer; the title and
   location match what the candidate is searching for. Extract the structured record (see schema).
   Anything that fails any check is dropped here, not "included with a caveat."

2. **Independent review (batched).** After step 1, hand the full set of surviving jobs — each with the
   evidence actually fetched for it — to a single independent Agent-tool pass (fresh context). It must
   reject any job that is not clearly backed by a live, authoritative posting in the provided evidence:
   a job with no fetched evidence, a stale/closed one, a title/location mismatch, or an aggregator-only
   entry. This is the anti-hallucination gate; a job the reviewer can't confirm from the evidence is
   cut, not kept.

**Never invent, infer, or "reconstruct" a posting, company, or apply URL.** Every field in a returned
job must come from content actually fetched in step 1. If unsure whether a job is real, drop it.

## Breadth vs. cost (token discipline)

Value comes from a wide net, but tokens come from fetching. Keep them separate:
- Discovery is **WebSearch only** (cheap): cast wide across Tier-A domains + the aggregators, across
  every target title and any named companies. Build a deduped candidate pool.
- Verification is **WebFetch** (costly): only ever on a **shortlist of ~25** candidates, chosen by
  likely CV fit from the search snippets already in hand — never fetch the whole pool.
- Reuse the already-parsed master CV record; never re-parse a raw CV. Batch the independent review
  into one call. Keep all intermediate and final output as compact JSON.
- If the pool exceeds the shortlist, that's expected — the user can run the search again for the next
  batch. Note honestly how many candidates were found vs. verified; never silently imply the list is
  exhaustive.

## Match bar and scoring (what actually gets shown)

Return every real opening the candidate has a **plausible shot at an interview** for — not only the
perfect fits. Include partial matches; the user decides which to pursue. The verification bar above
(the job must be real and live) is never relaxed — this section only governs *fit*, not *reality*.

Score each verified job's CV fit on a **0–100** `match_score` and label its `match_level`:
- **`strong` (75–100)** — clears the core must-haves; seniority aligned; would expect a callback.
- **`possible` (50–74)** — meets most core requirements; a real interview shot with a few gaps the CV
  can speak to (adjacent experience, transferable skills).
- **`stretch` (35–49)** — a genuine reach but defensible: some core requirements are `listed_only` or
  `missing`, yet the candidate has adjacent experience that could plausibly earn a conversation.

**Drop anything below 35** — a true non-match (wrong field, seniority wildly off, core requirements
missing with no adjacent experience). Never pad the list with jobs the candidate has no realistic
shot at; "partial" means a plausible interview, not "any open role."

Score honestly against the actual CV. Anchor the score on the requirement read: for each of the
role's key requirements, mark `matched` (the CV demonstrates it), `listed_only` (a skill present but
not demonstrated), or `missing`. More `matched` core requirements → higher score; core requirements
`missing` pull it down. State a one-line **why-it-fits** grounded in that read. Never inflate fit; the
score and read must be defensible against the actual CV.

Results are shown sorted by `match_score`, highest first, so the best fits lead.

## Result schema (per returned job)

```json
{"company": "", "role": "", "location": "", "work_model": "onsite|hybrid|remote|",
 "url": "authoritative apply/posting URL actually fetched in step 1", "source": "greenhouse|lever|ashby|workday|smartrecruiters|workable|recruitee|personio|company-site",
 "posted_date": "as stated on the posting, else \"\"", "match_score": 0-100, "match_level": "strong|possible|stretch",
 "why_fits": "one concrete sentence", "requirements": [{"name": "", "status": "matched|listed_only|missing", "evidence": "short CV excerpt for matched; omit otherwise"}]}
```
