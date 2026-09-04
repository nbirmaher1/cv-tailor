---
name: job-search
description: Find real, currently-open job openings a candidate is a strong match for, from their CV and a target location (plus optional titles, companies, and sites). Use when the user wants to discover jobs to apply to, not tailor a CV to one posting.
---

# Intelligent job search

Find **real, currently-open** postings the candidate is a strong match for. The absolute rule is
zero false results: every returned job must be a live posting at a real company, confirmed on an
authoritative source. Read `job-search-standards.md` (same directory as this file) before step 2 and
apply it in full — it is the actual rule set (sources, the two-step verification protocol, the
token-discipline limits, the strong-match bar, and the result schema), not background reading.

Inputs: an already-parsed master CV content record (the web app passes its path — read it directly,
never re-parse a raw CV), a target **location** (required), and optionally target **titles**, target
**companies**, and extra **sites** to include. Tools available: WebSearch, WebFetch, Read, Write,
Agent (no Bash). Ask for the location if it's missing; everything else is optional.

## Steps

1. **Plan (no fetching).** Read the master CV content record. If the caller gave titles, use them;
   otherwise derive 3-6 realistic target titles + seniority from the CV. Pull the candidate's core
   skills/domain. Build the discovery queries: one per `title × location`, plus one per named company
   (its ATS board / careers). This step is cheap — no WebSearch/WebFetch yet.

2. **Discovery — WebSearch only, wide.** Run the discovery queries, restricting to
   `job-search-standards.md`'s Tier-A ATS domains **and** the Tier-B aggregators (and any caller-
   supplied sites). For named companies, prefer their own ATS board directly. Collect candidate
   postings as `{company, title, location, url, source}`, dedupe by company+title+url, and cap the raw
   pool at ~60. Do NOT fetch anything yet.

3. **Shortlist by CV fit (no fetching).** From the candidate pool — using only the titles/snippets
   already returned by search — pre-rank by likely fit to the CV and keep the top **~25** to verify.
   This keeps expensive fetches off obvious non-fits.

4. **Verify — step 1 (authoritative fetch).** For each shortlisted job, WebFetch its Tier-A source (an
   ATS board/API entry or the company's own careers page) per `job-search-standards.md`'s protocol.
   Confirm it's live, the company is real, and title/location match; extract the structured record.
   Drop any that fail. An aggregator hit that can't be confirmed on an authoritative company source is
   dropped.

5. **Verify — step 2 (independent review).** Use the Agent tool (`subagent_type: general-purpose`,
   foreground) once, with a self-contained prompt containing every surviving job and the evidence
   actually fetched for it. Ask it to reject anything not clearly backed by a live authoritative
   posting in that evidence (no-evidence, stale/closed, title/location mismatch, aggregator-only). Keep
   only what it confirms. This is the mandatory anti-hallucination gate.

6. **Match + score.** For each confirmed job, assess fit against the CV per `job-search-standards.md`'s
   bar: give it a `match_score` (0–100) and `match_level` (`strong`/`possible`/`stretch`), a one-line
   `why_fits`, and a `matched`/`listed_only`/`missing` requirement read. Keep every job the candidate
   has a plausible interview shot at (score ≥ 35) — partial matches included, not only strong ones —
   and drop true non-matches below that. Sort the kept jobs by `match_score`, highest first. Never
   inflate a score.

7. **Report.** If the caller gave a results JSON path (the web app does), write the final list of
   verified matches (sorted by `match_score` descending) to it, each object exactly matching
   `job-search-standards.md`'s result schema: `{"jobs": [ ... ], "searched": {"titles": [...],
   "location": "", "candidates_found": N, "verified": M}}`. Include the honest counts (how many
   candidates were found vs. verified) — never imply the list is exhaustive. If nothing survives
   verification, return `{"jobs": [], ...}` with the counts rather than lowering the reality bar.
   Otherwise report the same to the user directly.

If you cannot proceed (e.g. no location was given and none can be inferred), write a short explanation
to the caller's error path if one was given, and stop — do not guess.
