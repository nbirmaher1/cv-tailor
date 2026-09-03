// Applications browser: mirrors the on-disk "folder per company, folder per
// role" layout, plus a "Needs info" inbox for attempts that finished but
// couldn't be filed automatically. The separate Applications tab (below) is
// the curated subset actually applied to, as a sortable/filterable CRM table.

const appsPendingSection = document.getElementById('apps-pending-section');
const appsPendingList = document.getElementById('apps-pending-list');
const appsEmpty = document.getElementById('apps-empty');
const appsTree = document.getElementById('apps-tree');

const appliedEmpty = document.getElementById('applied-empty');
const appliedFilterStrip = document.getElementById('applied-filter-strip');
const appliedTableWrap = document.getElementById('applied-table-wrap');

function el(tag, className, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (attrs) for (const [k, v] of Object.entries(attrs)) node[k] = v;
  return node;
}

async function fetchDownload(url, filename) {
  const resp = await apiFetch(url);
  if (!resp.ok) return;
  const blob = await resp.blob();
  triggerDownload(URL.createObjectURL(blob), filename);
}

function attemptRow(attempt, kind, opts = {}) {
  const { showContext = false } = opts;
  const row = el('div', 'flex items-center justify-between rounded-lg border border-zinc-100 dark:border-zinc-800 px-3 py-2');
  const labelText = showContext
    ? `${attempt.company_name || 'Unfiled'} · ${attempt.role_name || 'Unspecified role'} — ${formatDate(attempt.created_at)}`
    : formatDate(attempt.created_at);
  const label = el('span', 'text-sm text-zinc-600 dark:text-zinc-300', { textContent: labelText });
  const actions = el('div', 'flex items-center gap-3');

  const base = kind === 'pending' ? `/api/applications/pending/${attempt.id}` : `/api/applications/attempts/${attempt.id}`;
  const cvName = `${attempt.download_name}.${attempt.output_format}`;

  if (attempt.output_format === 'pdf') {
    const previewLink = el('a', 'text-xs font-medium text-accent cursor-pointer', { textContent: 'Preview' });
    previewLink.addEventListener('click', async () => {
      const resp = await apiFetch(`${base}/preview`);
      if (!resp.ok) return;
      const blob = await resp.blob();
      window.open(URL.createObjectURL(blob), '_blank');
    });
    actions.appendChild(previewLink);
  }

  const downloadLink = el('a', 'text-xs font-medium text-accent cursor-pointer', { textContent: 'Download' });
  downloadLink.addEventListener('click', () => fetchDownload(`${base}/result`, cvName));
  actions.appendChild(downloadLink);

  if (attempt.has_cover_letter) {
    const clLink = el('a', 'text-xs font-medium text-accent cursor-pointer', { textContent: 'Cover letter' });
    clLink.addEventListener('click', () => fetchDownload(`${base}/cover-letter/result`, `${attempt.download_name}_cover_letter.${attempt.output_format}`));
    actions.appendChild(clLink);
  }

  // Whole company+role becomes "applied" when you move any one of its
  // tailored CVs here -- that attempt is remembered as "the one you applied
  // with"; clicking move on a different attempt for the same role just
  // switches which one holds that title. Never shown for pending items --
  // those have no company/role assigned yet, so there's nothing to apply to.
  if (opts.showApplyToggle && kind !== 'pending') {
    const isThisAttemptApplied = opts.isApplied && opts.appliedAttemptId === attempt.id;
    actions.appendChild(buildApplyToggleButton(opts.applicationId, attempt.id, isThisAttemptApplied, opts.onToggled));
  }

  row.append(label, actions);
  return row;
}

// Shared apply/un-apply toggle -- used by attemptRow above (the Tailored CVs /
// Applications tables) and by the Tailor screen's own success state, so a single
// tailoring run can be bucketed right where its result already is instead of
// requiring a trip to another tab. `onToggled` (if given) runs after a successful
// POST/DELETE, e.g. to re-render whatever list/button is showing this state.
function buildApplyToggleButton(applicationId, attemptId, isApplied, onToggled) {
  const toggleBtn = el('button', 'text-xs font-medium rounded-lg px-2 py-1 transition-colors shrink-0', {
    type: 'button',
    textContent: isApplied ? '✓ Applied — remove' : 'Move to applications folder',
  });
  toggleBtn.classList.add(...(isApplied
    ? ['text-emerald-700', 'dark:text-emerald-400', 'bg-emerald-50', 'dark:bg-emerald-950/40', 'hover:bg-emerald-100', 'dark:hover:bg-emerald-900/40']
    : ['text-accent', 'hover:bg-accent-light', 'dark:hover:bg-accent/10']));
  toggleBtn.addEventListener('click', async () => {
    toggleBtn.disabled = true;
    try {
      if (isApplied) {
        await apiJson(`/api/applications/${applicationId}/apply`, 'DELETE');
      } else {
        await apiJson(`/api/applications/${applicationId}/apply`, 'POST', { attempt_id: attemptId });
      }
      if (onToggled) await onToggled();
    } catch (err) {
      toggleBtn.disabled = false;
    }
  });
  return toggleBtn;
}

// Tailored CVs tab: when this role was last worked on, regardless of applied status.
function roleDateLabel(role) {
  return role.attempts.length ? formatDate(role.attempts[0].created_at) : '';
}

function renderTree(tree, { container, emptyEl, showApplyToggle = false, onToggled } = {}) {
  container.innerHTML = '';
  if (emptyEl) emptyEl.classList.toggle('hidden', tree.length > 0);
  for (const company of tree) {
    const companyDetails = el('details', 'rounded-xl border border-zinc-100 dark:border-zinc-800 overflow-hidden', { open: true });
    const companySummary = el('summary', 'cursor-pointer select-none px-4 py-3 text-sm font-semibold text-zinc-900 dark:text-white hover:bg-zinc-50 dark:hover:bg-zinc-800/50', { textContent: company.company_name });
    companyDetails.appendChild(companySummary);

    const roleWrap = el('div', 'px-4 pb-3 space-y-2');
    for (const role of company.roles) {
      const roleDetails = el('details', 'rounded-lg border border-zinc-100 dark:border-zinc-800', { open: true });
      const roleSummary = el('summary', 'cursor-pointer select-none px-3 py-2 text-xs font-medium text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800/50 flex items-center gap-2');
      roleSummary.appendChild(el('span', '', { textContent: role.role_name }));
      const dateLabel = roleDateLabel(role);
      if (dateLabel) {
        roleSummary.appendChild(el('span', 'text-zinc-400 dark:text-zinc-500 font-normal', { textContent: dateLabel }));
      }
      roleDetails.appendChild(roleSummary);
      const attemptsWrap = el('div', 'px-3 pb-2 space-y-1.5');
      role.attempts.forEach(a => attemptsWrap.appendChild(attemptRow(a, 'filed', {
        showApplyToggle,
        applicationId: role.id,
        isApplied: role.status === 'applied',
        appliedAttemptId: role.applied_attempt_id,
        onToggled,
      })));
      roleDetails.appendChild(attemptsWrap);
      roleWrap.appendChild(roleDetails);
    }
    companyDetails.appendChild(roleWrap);
    container.appendChild(companyDetails);
  }
}

function pendingCard(item) {
  const card = el('div', 'rounded-xl border border-amber-200 dark:border-amber-900 bg-amber-50 dark:bg-amber-950/30 p-4');
  const top = el('div', 'flex items-center justify-between mb-2');
  top.append(
    el('span', 'text-xs text-zinc-500 dark:text-zinc-400', { textContent: formatDate(item.created_at) }),
  );
  card.appendChild(top);

  const grid = el('div', 'grid grid-cols-2 gap-2 mb-2');
  const companyInput = el('input', 'w-full rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-800 px-3 py-2 text-sm focus-ring', { placeholder: 'Company', value: item.company_name || '' });
  const roleInput = el('input', 'w-full rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-800 px-3 py-2 text-sm focus-ring', { placeholder: 'Role', value: item.role_name || '' });
  grid.append(companyInput, roleInput);
  card.appendChild(grid);

  const errorEl = el('div', 'hidden text-xs text-red-600 dark:text-red-400 mb-2');
  card.appendChild(errorEl);

  const actions = el('div', 'flex items-center justify-between');
  actions.appendChild(attemptRow(item, 'pending'));
  const saveBtn = el('button', 'text-xs font-medium text-white bg-accent hover:bg-accent-hover rounded-lg px-3 py-1.5 transition-colors shrink-0', { type: 'button', textContent: 'File it' });
  saveBtn.addEventListener('click', async () => {
    errorEl.classList.add('hidden');
    try {
      await apiJson(`/api/applications/pending/${item.id}`, 'PATCH', {
        company_name: companyInput.value.trim(),
        role_name: roleInput.value.trim(),
      });
      await loadApplicationsScreen();
    } catch (err) {
      errorEl.textContent = err.message;
      errorEl.classList.remove('hidden');
    }
  });
  card.appendChild(el('div', 'flex justify-end mt-2'));
  card.lastChild.appendChild(saveBtn);

  return card;
}

async function loadApplicationsScreen() {
  const [treeResp, pendingResp] = await Promise.all([
    apiFetch('/api/applications'),
    apiFetch('/api/applications/pending'),
  ]);
  if (treeResp.ok) {
    renderTree(await treeResp.json(), {
      container: appsTree, emptyEl: appsEmpty, showApplyToggle: true, onToggled: loadApplicationsScreen,
    });
  }
  if (pendingResp.ok) {
    const pending = await pendingResp.json();
    appsPendingSection.classList.toggle('hidden', pending.length === 0);
    appsPendingList.innerHTML = '';
    pending.forEach(item => appsPendingList.appendChild(pendingCard(item)));
  }
}
window.loadApplicationsScreen = loadApplicationsScreen;

window.attemptRow = attemptRow;
window.pendingCard = pendingCard;

// -- Applications tab: sortable/filterable CRM table -----------------------------------

const STAGE_LABELS = { applied: 'Applied', screening: 'Screening', interviewing: 'Interviewing', offer: 'Offer', archived: 'Archived' };
const STAGE_ORDER = ['applied', 'screening', 'interviewing', 'offer', 'archived'];
const STAGE_BADGE_CLASSES = {
  applied: 'text-accent bg-accent-light dark:bg-accent/10',
  screening: 'text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/40',
  interviewing: 'text-sky-700 dark:text-sky-400 bg-sky-50 dark:bg-sky-950/40',
  offer: 'text-emerald-700 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-950/40',
  archived: 'text-zinc-500 dark:text-zinc-400 bg-zinc-100 dark:bg-zinc-800',
};

let lastAppliedTree = [];
let appliedStageFilter = 'all';
let appliedSortKey = 'date';
let appliedSortDir = 'desc';
let expandedApplicationId = null;

function flattenAppliedRows(tree) {
  const rows = [];
  for (const company of tree) {
    for (const role of company.roles) {
      if (role.stage === 'tailored') continue; // never applied -- not part of this tab
      rows.push({ ...role, company_name: company.company_name, company_slug: company.company_slug });
    }
  }
  return rows;
}

function roleAppliedDateLabel(role) {
  if (!role.applied_at) return '';
  const appliedAttempt = role.attempts.find(a => a.id === role.applied_attempt_id);
  const applied = `Applied ${formatDate(role.applied_at)}`;
  return appliedAttempt ? `${applied} (CV tailored ${formatDate(appliedAttempt.created_at)})` : applied;
}

function formatSalary(role) {
  if (!role.salary_min && !role.salary_max) return '';
  const currency = role.salary_currency || 'USD';
  const fmt = (n) => n.toLocaleString();
  if (role.salary_min && role.salary_max) return `${fmt(role.salary_min)}–${fmt(role.salary_max)} ${currency}`;
  return `${fmt(role.salary_min || role.salary_max)} ${currency}`;
}

function followUpDueDate(role) {
  if (role.follow_up_due_date) {
    const d = new Date(role.follow_up_due_date);
    return isNaN(d.getTime()) ? null : d;
  }
  // No explicit date set -- default nudge 6 days after applying, only while still
  // waiting on a first response (a later stage means you've already heard something).
  if (role.stage === 'applied' && role.applied_at) {
    const d = new Date(role.applied_at.endsWith('Z') ? role.applied_at : role.applied_at + 'Z');
    if (isNaN(d.getTime())) return null;
    d.setDate(d.getDate() + 6);
    return d;
  }
  return null;
}

function isFollowUpDue(role) {
  const due = followUpDueDate(role);
  return due !== null && due.getTime() <= Date.now();
}

const SORT_COMPARATORS = {
  company: (a, b) => a.company_name.localeCompare(b.company_name),
  role: (a, b) => a.role_name.localeCompare(b.role_name),
  stage: (a, b) => STAGE_ORDER.indexOf(a.stage) - STAGE_ORDER.indexOf(b.stage),
  date: (a, b) => new Date(a.applied_at || 0) - new Date(b.applied_at || 0),
  salary: (a, b) => (a.salary_min || a.salary_max || 0) - (b.salary_min || b.salary_max || 0),
};

function sortRows(rows, key, dir) {
  const sorted = [...rows].sort(SORT_COMPARATORS[key] || SORT_COMPARATORS.date);
  return dir === 'desc' ? sorted.reverse() : sorted;
}

function renderFilterStrip(allRows) {
  appliedFilterStrip.innerHTML = '';
  appliedFilterStrip.classList.toggle('hidden', allRows.length === 0);
  const counts = { all: allRows.length };
  for (const stage of STAGE_ORDER) counts[stage] = allRows.filter(r => r.stage === stage).length;

  const options = [['all', 'All'], ...STAGE_ORDER.map(s => [s, STAGE_LABELS[s]])];
  for (const [value, label] of options) {
    const isActive = appliedStageFilter === value;
    const btn = el('button', 'text-xs font-medium rounded-full px-3 py-1.5 transition-colors', {
      type: 'button', textContent: `${label} (${counts[value]})`,
    });
    btn.classList.add(...(isActive
      ? ['bg-accent', 'text-white']
      : ['bg-zinc-100', 'dark:bg-zinc-800', 'text-zinc-600', 'dark:text-zinc-300', 'hover:bg-zinc-200', 'dark:hover:bg-zinc-700']));
    btn.addEventListener('click', () => {
      appliedStageFilter = value;
      renderApplicationsTableFromCache();
    });
    appliedFilterStrip.appendChild(btn);
  }
}

function sortHeader(label, key) {
  const th = el('th', 'px-3 py-2 text-left text-xs font-semibold text-zinc-500 dark:text-zinc-400 cursor-pointer select-none whitespace-nowrap');
  const isActive = appliedSortKey === key;
  th.textContent = label + (isActive ? (appliedSortDir === 'desc' ? ' ↓' : ' ↑') : '');
  th.addEventListener('click', () => {
    if (appliedSortKey === key) {
      appliedSortDir = appliedSortDir === 'desc' ? 'asc' : 'desc';
    } else {
      appliedSortKey = key;
      appliedSortDir = key === 'company' || key === 'role' ? 'asc' : 'desc';
    }
    renderApplicationsTableFromCache();
  });
  return th;
}

function detailsFieldInput(tag, attrs, wrapClass = '') {
  const wrap = el('div', wrapClass);
  const input = el(tag, 'w-full rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-800 px-3 py-2 text-sm focus-ring', attrs);
  wrap.appendChild(input);
  return { wrap, input };
}

function buildDetailsForm(role) {
  const form = el('div', 'grid grid-cols-2 gap-2');

  const location = detailsFieldInput('input', { placeholder: 'Location', value: role.location || '' });
  const workModel = el('select', 'w-full rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-800 px-3 py-2 text-sm focus-ring');
  workModel.append(
    el('option', '', { value: '', textContent: 'Work model' }),
    el('option', '', { value: 'remote', textContent: 'Remote' }),
    el('option', '', { value: 'hybrid', textContent: 'Hybrid' }),
    el('option', '', { value: 'onsite', textContent: 'Onsite' }),
  );
  workModel.value = role.work_model || '';

  const salaryMin = detailsFieldInput('input', { type: 'number', placeholder: 'Salary min', value: role.salary_min ?? '' });
  const salaryMax = detailsFieldInput('input', { type: 'number', placeholder: 'Salary max', value: role.salary_max ?? '' });
  const currency = detailsFieldInput('input', { placeholder: 'Currency', value: role.salary_currency || 'USD' });
  const followUp = detailsFieldInput('input', { type: 'date', value: role.follow_up_due_date || '' });
  const notes = el('textarea', 'w-full rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-800 px-3 py-2 text-sm focus-ring col-span-2', { rows: 2, placeholder: 'Notes', value: role.notes || '' });

  form.append(location.wrap, workModel, salaryMin.wrap, salaryMax.wrap, currency.wrap, followUp.wrap, notes);

  const errorEl = el('div', 'hidden text-xs text-red-600 dark:text-red-400 col-span-2');
  const saveBtn = el('button', 'col-span-2 justify-self-end text-xs font-medium text-white bg-accent hover:bg-accent-hover rounded-lg px-3 py-1.5 transition-colors', { type: 'button', textContent: 'Save details' });
  saveBtn.addEventListener('click', async () => {
    errorEl.classList.add('hidden');
    saveBtn.disabled = true;
    try {
      await apiJson(`/api/applications/${role.id}/details`, 'PATCH', {
        location: location.input.value.trim() || null,
        work_model: workModel.value || null,
        salary_min: salaryMin.input.value ? Number(salaryMin.input.value) : null,
        salary_max: salaryMax.input.value ? Number(salaryMax.input.value) : null,
        salary_currency: currency.input.value.trim() || null,
        follow_up_due_date: followUp.input.value || null,
        notes: notes.value.trim() || null,
      });
      await loadAppliedScreen();
    } catch (err) {
      errorEl.textContent = err.message;
      errorEl.classList.remove('hidden');
      saveBtn.disabled = false;
    }
  });
  form.append(errorEl, saveBtn);
  return form;
}

async function buildActivityList(role) {
  const wrap = el('div', 'space-y-1');
  wrap.appendChild(el('p', 'text-xs text-zinc-400', { textContent: 'Loading history…' }));
  try {
    const resp = await apiFetch(`/api/applications/${role.id}/activities`);
    const activities = resp.ok ? await resp.json() : [];
    wrap.innerHTML = '';
    if (activities.length === 0) {
      wrap.appendChild(el('p', 'text-xs text-zinc-400', { textContent: 'No history yet.' }));
    }
    for (const a of activities) {
      const row = el('div', 'flex items-baseline gap-2 text-xs');
      row.append(
        el('span', 'text-zinc-400 dark:text-zinc-500 shrink-0', { textContent: formatDate(a.event_date) }),
        el('span', 'text-zinc-600 dark:text-zinc-300', { textContent: a.description }),
      );
      wrap.appendChild(row);
    }
  } catch (err) {
    wrap.innerHTML = '';
    wrap.appendChild(el('p', 'text-xs text-red-500', { textContent: 'Could not load history.' }));
  }
  return wrap;
}

function buildDetailRow(role) {
  const tr = el('tr');
  const td = el('td', 'px-3 pb-4 pt-1 bg-zinc-50/60 dark:bg-zinc-800/30', { colSpan: 6 });
  const grid = el('div', 'grid grid-cols-1 md:grid-cols-2 gap-4');

  const attemptsCol = el('div', 'space-y-2');
  attemptsCol.appendChild(el('p', 'text-xs font-semibold text-zinc-500 dark:text-zinc-400 uppercase tracking-wide mb-1', { textContent: 'Tailored CVs' }));
  role.attempts.forEach(a => attemptsCol.appendChild(attemptRow(a, 'filed', {
    showApplyToggle: true,
    applicationId: role.id,
    isApplied: role.status === 'applied',
    appliedAttemptId: role.applied_attempt_id,
    onToggled: loadAppliedScreen,
  })));

  const detailsCol = el('div', 'space-y-4');
  const detailsSection = el('div');
  detailsSection.appendChild(el('p', 'text-xs font-semibold text-zinc-500 dark:text-zinc-400 uppercase tracking-wide mb-1', { textContent: 'Details' }));
  detailsSection.appendChild(buildDetailsForm(role));
  const historySection = el('div');
  historySection.appendChild(el('p', 'text-xs font-semibold text-zinc-500 dark:text-zinc-400 uppercase tracking-wide mb-1', { textContent: 'History' }));
  buildActivityList(role).then(list => historySection.appendChild(list));
  detailsCol.append(detailsSection, historySection);

  grid.append(attemptsCol, detailsCol);
  td.appendChild(grid);
  tr.appendChild(td);
  return tr;
}

function buildStageSelect(role) {
  const select = el('select', 'text-xs font-medium rounded-full pl-2.5 pr-6 py-1 border-0 focus-ring cursor-pointer');
  select.classList.add(...(STAGE_BADGE_CLASSES[role.stage] || STAGE_BADGE_CLASSES.applied).split(' '));
  for (const stage of STAGE_ORDER) {
    select.appendChild(el('option', '', { value: stage, textContent: STAGE_LABELS[stage], selected: stage === role.stage }));
  }
  select.addEventListener('click', (e) => e.stopPropagation());
  select.addEventListener('change', async () => {
    const previous = role.stage;
    select.disabled = true;
    try {
      await apiJson(`/api/applications/${role.id}/stage`, 'PATCH', { stage: select.value });
      await loadAppliedScreen();
    } catch (err) {
      select.value = previous;
      select.disabled = false;
    }
  });
  return select;
}

function buildRow(role) {
  const isExpanded = expandedApplicationId === role.id;
  const tr = el('tr', 'cursor-pointer hover:bg-zinc-50 dark:hover:bg-zinc-800/50 border-t border-zinc-100 dark:border-zinc-800');
  tr.addEventListener('click', () => {
    expandedApplicationId = isExpanded ? null : role.id;
    renderApplicationsTableFromCache();
  });

  const companyTd = el('td', 'px-3 py-2.5 text-sm font-medium text-zinc-900 dark:text-white whitespace-nowrap', { textContent: role.company_name });
  const roleTd = el('td', 'px-3 py-2.5 text-sm text-zinc-600 dark:text-zinc-300', { textContent: role.role_name });
  const stageTd = el('td', 'px-3 py-2.5');
  stageTd.appendChild(buildStageSelect(role));
  const dateTd = el('td', 'px-3 py-2.5 text-xs text-zinc-400 dark:text-zinc-500 whitespace-nowrap', { textContent: roleAppliedDateLabel(role) });
  const salaryTd = el('td', 'px-3 py-2.5 text-xs text-zinc-500 dark:text-zinc-400 whitespace-nowrap', { textContent: formatSalary(role) });
  const followUpTd = el('td', 'px-3 py-2.5 whitespace-nowrap');
  if (isFollowUpDue(role)) {
    followUpTd.appendChild(el('span', 'text-xs font-medium text-amber-700 dark:text-amber-400', { textContent: '⚠ Follow up' }));
  }

  tr.append(companyTd, roleTd, stageTd, dateTd, salaryTd, followUpTd);
  return tr;
}

function renderApplicationsTableFromCache() {
  const allRows = flattenAppliedRows(lastAppliedTree);
  appliedEmpty.classList.toggle('hidden', allRows.length > 0);
  renderFilterStrip(allRows);

  if (allRows.length === 0) {
    appliedTableWrap.innerHTML = '';
    return;
  }

  const filtered = appliedStageFilter === 'all' ? allRows : allRows.filter(r => r.stage === appliedStageFilter);
  const sorted = sortRows(filtered, appliedSortKey, appliedSortDir);

  const table = el('table', 'w-full border-collapse');
  const thead = el('thead');
  const headRow = el('tr');
  headRow.append(
    sortHeader('Company', 'company'), sortHeader('Role', 'role'), sortHeader('Stage', 'stage'),
    sortHeader('Date', 'date'), sortHeader('Salary', 'salary'), el('th', 'px-3 py-2'),
  );
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = el('tbody');
  for (const role of sorted) {
    tbody.appendChild(buildRow(role));
    if (expandedApplicationId === role.id) {
      tbody.appendChild(buildDetailRow(role));
    }
  }
  table.appendChild(tbody);

  appliedTableWrap.innerHTML = '';
  appliedTableWrap.appendChild(table);
}

async function loadAppliedScreen() {
  const resp = await apiFetch('/api/applications');
  lastAppliedTree = resp.ok ? await resp.json() : [];
  renderApplicationsTableFromCache();
}
window.loadAppliedScreen = loadAppliedScreen;
