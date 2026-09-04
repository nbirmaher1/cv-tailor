// Job Search screen. Runs a background search (tracked in the shared dock from tailor.js),
// lists verified job_leads, and hands any lead off to the tailoring flow. Loaded after
// tailor.js, so it reuses its globals: `runs`, trackRun, renderDock, showToast, openRun,
// REQUIREMENT_CHIP_STYLE, plus apiFetch/showScreen from common.js.

const jsForm = document.getElementById('jobsearch-form');
const jsSubmitBtn = document.getElementById('js-submit-btn');
const jsFormError = document.getElementById('js-form-error');
const jsRunningNote = document.getElementById('js-running-note');
const jsResultsHeader = document.getElementById('js-results-header');
const jsResultsCount = document.getElementById('js-results-count');
const jsEmpty = document.getElementById('js-empty');
const jsResultsList = document.getElementById('js-results-list');

const JS_SOURCE_LABEL = {
  greenhouse: 'Greenhouse', lever: 'Lever', ashby: 'Ashby', workday: 'Workday',
  smartrecruiters: 'SmartRecruiters', workable: 'Workable', recruitee: 'Recruitee',
  personio: 'Personio', 'company-site': 'Company site',
};

// Fit-level colouring for the match-score pill. Falls back by score if level is missing.
const JS_MATCH_STYLE = {
  strong: { label: 'Strong match', classes: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300' },
  possible: { label: 'Possible match', classes: 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300' },
  stretch: { label: 'Stretch', classes: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400' },
};
function jsMatchLevel(lead) {
  if (JS_MATCH_STYLE[lead.match_level]) return lead.match_level;
  const s = Number(lead.match_score) || 0;
  return s >= 75 ? 'strong' : s >= 50 ? 'possible' : 'stretch';
}

function jsShowError(msg) {
  jsFormError.textContent = msg;
  jsFormError.classList.remove('hidden');
}

jsForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  jsFormError.classList.add('hidden');
  const location = document.getElementById('js-location').value.trim();
  if (!location) { jsShowError('Enter a location to search.'); return; }
  const titles = document.getElementById('js-titles').value.trim();

  const fd = new FormData();
  fd.append('location', location);
  fd.append('titles', titles);
  fd.append('companies', document.getElementById('js-companies').value.trim());
  fd.append('sites', document.getElementById('js-sites').value.trim());

  jsSubmitBtn.disabled = true;
  try {
    const resp = await apiFetch('/api/job-search/start', { method: 'POST', body: fd });
    if (!resp.ok) {
      let detail = 'Could not start the search.';
      try { detail = (await resp.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    const { run_id } = await resp.json();
    runs[run_id] = {
      runId: run_id, kind: 'search',
      label: `${titles || 'matching roles'} in ${location}`,
      done: false, queued: false, percent: 3, step: 'Starting…', seen: false,
    };
    trackRun(run_id);
    renderDock();
    showToast('Job search started — running in the background.', { type: 'info' });
    jsRunningNote.classList.remove('hidden');
  } catch (err) {
    jsShowError(err.message || 'Could not start the search.');
  } finally {
    jsSubmitBtn.disabled = false;
  }
});

// tailor.js calls this when a search run finishes.
window.onJobSearchDone = function () {
  jsRunningNote.classList.add('hidden');
  loadJobLeads();
};

// Called on nav to the screen and when a search completes.
window.loadJobSearchScreen = function () { loadJobLeads(); };

async function loadJobLeads() {
  let leads = [];
  try {
    const resp = await apiFetch('/api/job-leads');
    if (resp.ok) leads = (await resp.json()).leads || [];
  } catch (_) { /* leave empty on error */ }
  renderJobLeads(leads);
}

function renderJobLeads(leads) {
  jsResultsList.innerHTML = '';
  jsResultsHeader.classList.toggle('hidden', leads.length === 0);
  jsEmpty.classList.toggle('hidden', leads.length !== 0);
  jsResultsCount.textContent = leads.length ? `${leads.length} verified match${leads.length === 1 ? '' : 'es'}` : '';
  for (const lead of leads) jsResultsList.appendChild(buildLeadCard(lead));
}

function buildLeadCard(lead) {
  const card = document.createElement('div');
  card.className = 'rounded-xl border border-zinc-100 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5';

  // Header: company -> role, with source badge.
  const top = document.createElement('div');
  top.className = 'flex items-start justify-between gap-3';
  const title = document.createElement('div');
  title.className = 'min-w-0';
  const h = document.createElement('p');
  h.className = 'text-sm font-semibold text-zinc-900 dark:text-white';
  h.textContent = `${lead.company_name} → ${lead.role_name}`;
  const meta = document.createElement('p');
  meta.className = 'text-xs text-zinc-400 mt-0.5';
  meta.textContent = [lead.location, lead.work_model].filter(Boolean).join(' · ');
  title.appendChild(h);
  title.appendChild(meta);
  top.appendChild(title);

  // Right side: match-score pill (+ source badge under it).
  const badges = document.createElement('div');
  badges.className = 'shrink-0 flex flex-col items-end gap-1';
  const level = jsMatchLevel(lead);
  const score = Math.max(0, Math.min(100, Number(lead.match_score) || 0));
  const scoreBadge = document.createElement('span');
  scoreBadge.className = `text-[11px] font-semibold px-2 py-0.5 rounded-full ${JS_MATCH_STYLE[level].classes}`;
  scoreBadge.textContent = `${score}% · ${JS_MATCH_STYLE[level].label}`;
  badges.appendChild(scoreBadge);
  if (lead.source && JS_SOURCE_LABEL[lead.source]) {
    const badge = document.createElement('span');
    badge.className = 'text-[10px] font-medium px-2 py-0.5 rounded-full bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400';
    badge.textContent = JS_SOURCE_LABEL[lead.source];
    badges.appendChild(badge);
  }
  top.appendChild(badges);
  card.appendChild(top);

  if (lead.why_fits) {
    const why = document.createElement('p');
    why.className = 'text-sm text-zinc-600 dark:text-zinc-300 mt-2';
    why.textContent = lead.why_fits;
    card.appendChild(why);
  }

  // Requirement chips (reuse the tailoring categorical style).
  if (lead.requirements && lead.requirements.length && typeof REQUIREMENT_CHIP_STYLE !== 'undefined') {
    const chips = document.createElement('div');
    chips.className = 'flex flex-wrap gap-1.5 mt-3';
    for (const req of lead.requirements) {
      const style = REQUIREMENT_CHIP_STYLE[req.status];
      if (!style) continue;
      const chip = document.createElement('span');
      chip.className = `inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium ${style.classes}`;
      chip.textContent = `${style.dot} ${req.name}`;
      if (req.evidence) chip.title = req.evidence;
      chips.appendChild(chip);
    }
    card.appendChild(chips);
  }

  // Actions.
  const actions = document.createElement('div');
  actions.className = 'flex items-center gap-3 mt-4';

  const tailorBtn = document.createElement('button');
  tailorBtn.type = 'button';
  tailorBtn.className = 'inline-flex items-center gap-1.5 rounded-lg bg-accent hover:bg-accent-hover text-white font-medium text-xs px-3 py-2 transition-colors';
  tailorBtn.textContent = 'Tailor my CV for this';
  tailorBtn.addEventListener('click', () => tailorForLead(lead));
  actions.appendChild(tailorBtn);

  const applyLink = document.createElement('a');
  applyLink.href = lead.url;
  applyLink.target = '_blank';
  applyLink.rel = 'noopener noreferrer';
  applyLink.className = 'text-xs font-medium text-accent hover:underline';
  applyLink.textContent = 'View posting ↗';
  actions.appendChild(applyLink);

  const dismissBtn = document.createElement('button');
  dismissBtn.type = 'button';
  dismissBtn.className = 'ml-auto text-xs text-zinc-400 hover:text-red-500 transition-colors';
  dismissBtn.textContent = 'Dismiss';
  dismissBtn.addEventListener('click', async () => {
    try { await apiFetch(`/api/job-leads/${lead.id}/dismiss`, { method: 'POST' }); } catch (_) {}
    card.remove();
    if (!jsResultsList.children.length) loadJobLeads();  // refresh empty/header state
  });
  actions.appendChild(dismissBtn);

  card.appendChild(actions);
  return card;
}

// Hand a lead to the tailoring flow: prefill the tailor form (its verified apply URL as the
// job posting, plus company/role) and switch to the Tailor screen for the user to review + run.
function tailorForLead(lead) {
  const jobUrl = document.getElementById('job-url');
  const jobText = document.getElementById('job-text');
  const company = document.getElementById('company-name');
  const role = document.getElementById('role-name');
  if (jobUrl) jobUrl.value = lead.url;
  if (jobText) jobText.value = '';
  if (company) company.value = lead.company_name || '';
  if (role) role.value = lead.role_name || '';
  const moreOpts = document.getElementById('more-options');
  if (moreOpts) moreOpts.open = true;  // company/role live inside "More options"
  showScreen('app');
  showToast('Prefilled the tailor form for this job — review and hit "Tailor my CV".', { type: 'info' });
}
