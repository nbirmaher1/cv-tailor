const tailorEditMasterCvLink = document.getElementById('tailor-edit-master-cv-link');
const tailorCvGate = document.getElementById('tailor-cv-gate');
const tailorMain = document.getElementById('tailor-main');

// The input form doesn't need extra room, but the rendered preview is cramped at the
// form's width -- widen only while the success state (with its preview) is showing.
function setTailorWide(isWide) {
  tailorMain.classList.toggle('max-w-2xl', !isWide);
  tailorMain.classList.toggle('max-w-5xl', isWide);
}

function refreshTailorGate() {
  const gated = window.hasMasterCV === false;
  tailorCvGate.classList.toggle('hidden', !gated);
  form.classList.toggle('hidden', gated);
}
document.addEventListener('mastercv:changed', refreshTailorGate);

const form = document.getElementById('form');
const submitBtn = document.getElementById('submit-btn');
const formError = document.getElementById('form-error');

const loadingState = document.getElementById('loading-state');
const successState = document.getElementById('success-state');
const failureState = document.getElementById('failure-state');
const failureMessage = document.getElementById('failure-message');
const failureCause = document.getElementById('failure-cause');
const failureResumeBtn = document.getElementById('failure-resume-btn');
const failureStartOverBtn = document.getElementById('failure-startover-btn');
const failureDiscardBtn = document.getElementById('failure-discard-btn');

const runDrawer = document.getElementById('run-drawer');
const runDrawerBackdrop = document.getElementById('run-drawer-backdrop');
const runDrawerTitle = document.getElementById('run-drawer-title');
const runDrawerClose = document.getElementById('run-drawer-close');
const runDock = document.getElementById('run-dock');
const runDockList = document.getElementById('run-dock-list');
const runDockToggle = document.getElementById('run-dock-toggle');
const runDockSummary = document.getElementById('run-dock-summary');
const runDockCaret = document.getElementById('run-dock-caret');
const toastContainer = document.getElementById('toast-container');

const progressBar = document.getElementById('progress-bar');
const progressMessage = document.getElementById('progress-message');
const cancelBtn = document.getElementById('cancel-btn');
const downloadBtn = document.getElementById('download-btn');
const downloadLabel = document.getElementById('download-label');
const downloadError = document.getElementById('download-error');
const restartBtn = document.getElementById('restart-btn');

const rationaleCard = document.getElementById('rationale-card');
const rationaleSummary = document.getElementById('rationale-summary');
const rationaleRequirements = document.getElementById('rationale-requirements');
const rationaleRequirementsChips = document.getElementById('rationale-requirements-chips');
const rationaleChanges = document.getElementById('rationale-changes');
const rationaleReview = document.getElementById('rationale-review');
const rationaleReviewText = document.getElementById('rationale-review-text');

const previewWrap = document.getElementById('preview-wrap');
const previewLoading = document.getElementById('preview-loading');
const previewFrame = document.getElementById('preview-frame');
const previewFallback = document.getElementById('preview-fallback');

const docTabs = document.getElementById('doc-tabs');
const docTabCv = document.getElementById('doc-tab-cv');
const docTabCoverLetter = document.getElementById('doc-tab-cover-letter');

const reviseInput = document.getElementById('revise-input');
const reviseBtn = document.getElementById('revise-btn');
const reviseError = document.getElementById('revise-error');
const reviseHint = document.getElementById('revise-hint');

const coverLetterCheckbox = document.getElementById('cover-letter-checkbox');
const coverLetterExtras = document.getElementById('cover-letter-extras');
const intelligentCoverLetterCheckbox = document.getElementById('intelligent-cover-letter-checkbox');
const coverLetterNotesInput = document.getElementById('cover-letter-notes');
const coverLetterTemplateDropzone = document.getElementById('cover-letter-template-dropzone');
const coverLetterTemplateInput = document.getElementById('cover-letter-template-input');
const coverLetterTemplateLabel = document.getElementById('cover-letter-template-label');

const moreOptions = document.getElementById('more-options');
const moreOptionsSummary = document.getElementById('more-options-summary');
const fieldMemory = document.getElementById('field-memory');
const fieldMemoryList = document.getElementById('field-memory-list');

const filedStatus = document.getElementById('filed-status');
const filedApplyToggle = document.getElementById('filed-apply-toggle');
const filedRemoveBtn = document.getElementById('filed-remove-btn');
const filedRemoveError = document.getElementById('filed-remove-error');
const pendingFilingCard = document.getElementById('pending-card');
const pendingCompanyInput = document.getElementById('pending-company-input');
const pendingRoleInput = document.getElementById('pending-role-input');
const pendingError = document.getElementById('pending-error');
const pendingSaveBtn = document.getElementById('pending-save-btn');

const MAX_CV_BYTES = 10 * 1024 * 1024;
const COVER_LETTER_TEMPLATE_DEFAULT_LABEL = "Have a cover letter you've used before? Drop it here to match its style — optional, up to 10 MB";

let selectedCoverLetterTemplate = null;

// Multi-run model: every triggered run is tracked in `runs` and polled in the background
// (see trackRun) so several can tailor at once. The `current*` variables below are NOT "the
// one active run" anymore -- they mirror the run currently OPEN in the slide-over drawer
// (openRunId), so the existing result-view logic (preview/revise/download/apply) keeps
// working unchanged, just pointed at whichever run the user opened.
const runs = {};              // runId -> { runId, format, filename, company, role, done, error,
                              //            errorCause, resumable, queued, percent, step, rationale,
                              //            application, hasCoverLetter, revisionCount, maxRevisions,
                              //            seen, pollToken, stopped }
let openRunId = null;
let dockExpanded = false;

let currentRunId = null;
let currentFormat = null;
let currentFilename = null;
let currentCancelToken = null;
let cachedDownloads = { cv: null, cover_letter: null };
let isRevising = false;
let lastRationale = null;
let activeDocument = 'cv';

const DISMISSED_KEY = 'cvtailor:dismissedRuns';

function wireDropzone(zoneEl, inputEl, onSelect) {
  zoneEl.addEventListener('click', () => inputEl.click());
  zoneEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); inputEl.click(); }
  });
  inputEl.addEventListener('change', () => onSelect(inputEl.files[0] || null));
  ['dragenter', 'dragover'].forEach(evt =>
    zoneEl.addEventListener(evt, e => { e.preventDefault(); e.stopPropagation(); zoneEl.classList.add('border-accent', 'bg-accent-light'); })
  );
  ['dragleave', 'drop'].forEach(evt =>
    zoneEl.addEventListener(evt, e => { e.preventDefault(); e.stopPropagation(); zoneEl.classList.remove('border-accent', 'bg-accent-light'); })
  );
  zoneEl.addEventListener('drop', e => {
    const file = e.dataTransfer.files[0];
    if (file) { inputEl.files = e.dataTransfer.files; onSelect(file); }
  });
}

coverLetterCheckbox.addEventListener('change', () => {
  coverLetterExtras.classList.toggle('hidden', !coverLetterCheckbox.checked);
});

wireDropzone(coverLetterTemplateDropzone, coverLetterTemplateInput, (file) => {
  if (file && file.size > MAX_CV_BYTES) {
    showError('That file is too large — please keep it under 10 MB.');
    coverLetterTemplateInput.value = '';
    return;
  }
  clearError();
  selectedCoverLetterTemplate = file;
  coverLetterTemplateLabel.textContent = file ? `Selected: ${file.name}` : COVER_LETTER_TEMPLATE_DEFAULT_LABEL;
  coverLetterTemplateDropzone.classList.toggle('border-solid', !!file);
  coverLetterTemplateDropzone.classList.toggle('border-emerald-300', !!file);
});

tailorEditMasterCvLink.addEventListener('click', (e) => {
  e.preventDefault();
  showScreen('master-cv');
  if (window.loadMasterCVScreen) window.loadMasterCVScreen();
});

function showError(message) {
  formError.textContent = message;
  formError.classList.remove('hidden');
}
function clearError() {
  formError.classList.add('hidden');
  formError.textContent = '';
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  clearError();

  const jobUrl = document.getElementById('job-url').value.trim();
  const jobText = document.getElementById('job-text').value.trim();
  if (!jobUrl && !jobText) {
    showError('Please provide a job posting URL or paste the job description.');
    return;
  }

  const format = document.querySelector('input[name=format]:checked').value;
  const customFilename = document.getElementById('file-name').value.trim();
  const notes = document.getElementById('notes').value.trim();
  const companyName = document.getElementById('company-name').value.trim();
  const roleName = document.getElementById('role-name').value.trim();
  const wantsCoverLetter = coverLetterCheckbox.checked;

  const formData = new FormData();
  formData.append('job_url', jobUrl);
  formData.append('job_text', jobText);
  formData.append('output_format', format);
  formData.append('filename', customFilename);
  formData.append('notes', notes);
  formData.append('company_name', companyName);
  formData.append('role_name', roleName);
  formData.append('field_overrides', JSON.stringify(collectFieldOverrides()));
  formData.append('include_cover_letter', wantsCoverLetter ? 'true' : 'false');
  if (wantsCoverLetter) {
    formData.append('cover_letter_notes', coverLetterNotesInput.value.trim());
    formData.append('intelligent_cover_letter', intelligentCoverLetterCheckbox.checked ? 'true' : 'false');
    if (selectedCoverLetterTemplate) formData.append('cover_letter_template', selectedCoverLetterTemplate);
  }

  // The run drops to the background immediately: track + poll it, drop a dock chip, toast,
  // and reset the form so the next CV can be started right away. The form is never replaced
  // by a loading state -- progress/results live in the dock + drawer.
  submitBtn.disabled = true;
  try {
    const startResp = await apiFetch('/api/tailor/start', { method: 'POST', body: formData });
    if (!startResp.ok) {
      let detail = 'Request failed.';
      try { detail = (await startResp.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    const { run_id } = await startResp.json();
    runs[run_id] = {
      runId: run_id, format, filename: customFilename,
      company: companyName || null, role: roleName || null,
      done: false, queued: false, percent: 3, step: 'Starting…',
      activeDocument: 'cv', seen: false,
    };
    trackRun(run_id);
    renderDock();
    showToast("Tailoring started — it's running in the background.", { type: 'info' });
    resetFormForNext();
  } catch (err) {
    showError(err.message || ('Network error: ' + err));
  } finally {
    submitBtn.disabled = false;
  }
});

function resetFormForNext() {
  // Clear the per-application inputs so the next CV starts fresh, but keep the remembered
  // field toggles and saved format/cover-letter prefs (those persist across runs).
  ['job-url', 'job-text', 'file-name', 'company-name', 'role-name', 'notes'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
  coverLetterNotesInput.value = '';
  coverLetterTemplateInput.value = '';
  selectedCoverLetterTemplate = null;
  coverLetterTemplateLabel.textContent = COVER_LETTER_TEMPLATE_DEFAULT_LABEL;
  coverLetterTemplateDropzone.classList.remove('border-solid', 'border-emerald-300');
  clearError();
  focusJobField();
}

// setTailorWide is obsolete now that results live in a fixed-width drawer, not the card.
setTailorWide = () => {};

// -- Toasts (non-interrupting notices) --------------------------------------------------

function showToast(message, opts = {}) {
  if (!toastContainer) return;
  const border = opts.type === 'success' ? 'border-emerald-200 dark:border-emerald-900'
    : opts.type === 'error' ? 'border-red-200 dark:border-red-900'
    : 'border-zinc-200 dark:border-zinc-700';
  const toast = document.createElement('div');
  toast.className = `pointer-events-auto max-w-xs rounded-xl bg-white dark:bg-zinc-900 shadow-lg ring-1 ring-zinc-900/5 dark:ring-white/10 border ${border} px-4 py-3 text-sm text-zinc-700 dark:text-zinc-200 fade-up`;
  toast.textContent = message;
  if (opts.onClick) {
    toast.classList.add('cursor-pointer');
    toast.addEventListener('click', () => { opts.onClick(); toast.remove(); });
  }
  toastContainer.appendChild(toast);
  setTimeout(() => {
    toast.style.transition = 'opacity .3s';
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 300);
  }, opts.timeout || 6000);
}

// -- Slide-over drawer (the opened run's view) -----------------------------------------

function runLabel(r) {
  if (!r) return 'Tailoring run';
  if (r.company || r.role) return `${r.company || 'Unknown company'}${r.role ? ' → ' + r.role : ''}`;
  return 'Tailoring run';
}

function openDrawer() {
  runDrawerBackdrop.classList.remove('hidden');
  runDrawer.classList.remove('translate-x-full');
}
function closeDrawer() {
  runDrawer.classList.add('translate-x-full');
  runDrawerBackdrop.classList.add('hidden');
  openRunId = null;
}
runDrawerClose.addEventListener('click', closeDrawer);
runDrawerBackdrop.addEventListener('click', closeDrawer);

function drawerShow(which) {
  loadingState.classList.toggle('hidden', which !== 'loading');
  loadingState.classList.toggle('flex', which === 'loading');
  successState.classList.toggle('hidden', which !== 'success');
  successState.classList.toggle('flex', which === 'success');
  failureState.classList.toggle('hidden', which !== 'failure');
  failureState.classList.toggle('flex', which === 'failure');
}

function showSuccess(status, runId, format) {
  currentRunId = runId;
  currentFormat = format;
  cachedDownloads = { cv: null, cover_letter: null };
  activeDocument = 'cv';
  downloadLabel.textContent = 'Download';
  lastRationale = status.rationale;
  renderRationale(status.rationale);
  renderApplicationStatus(status.application);
  updateDocTabs(status.has_cover_letter);
  renderPreview(runId, format, activeDocument);
  updateReviseUI(status.revision_count || 0, status.max_revisions || 3);
  drawerShow('success');
}

let failureRunId = null;

function showFailure(runId, message, cause, resumable) {
  failureRunId = runId;
  currentRunId = runId;
  failureMessage.textContent = message || 'Something went wrong';
  failureCause.textContent = cause || '';
  failureCause.classList.toggle('hidden', !cause);
  failureResumeBtn.classList.toggle('hidden', !resumable);
  drawerShow('failure');
}

function showLoading(message, percent) {
  progressBar.style.width = `${percent || 8}%`;
  progressMessage.textContent = message || 'Working…';
  drawerShow('loading');
}

// -- Multi-run tracking -----------------------------------------------------------------

function applyStatusToRun(runId, s) {
  const r = runs[runId];
  if (!r) return;
  if ('percent' in s) r.percent = s.percent;
  if ('step' in s) r.step = s.step;
  if ('done' in s) r.done = s.done;
  if ('error' in s) r.error = s.error;
  if ('error_cause' in s) r.errorCause = s.error_cause;
  if ('resumable' in s) r.resumable = s.resumable;
  if ('queued' in s) r.queued = s.queued;
  if ('output_format' in s && s.output_format) r.format = s.output_format;
  if ('download_name' in s && s.download_name) r.filename = s.download_name;
  if ('has_cover_letter' in s) r.hasCoverLetter = s.has_cover_letter;
  if ('application' in s) r.application = s.application;
  if ('rationale' in s) r.rationale = s.rationale;
  if ('company_name' in s && s.company_name) r.company = s.company_name;
  if ('role_name' in s && s.role_name) r.role = s.role_name;
}

function trackRun(runId) {
  const token = {};
  runs[runId].pollToken = token;
  runs[runId].stopped = false;
  pollJob(`/api/tailor/${runId}/status`, {
    cancelToken: token,
    onProgress: (s) => onRunProgress(runId, s),
  }).then((s) => onRunDone(runId, s, null))
    .catch((err) => onRunDone(runId, null, err));
}

function stopTracking(runId) {
  const r = runs[runId];
  if (r && r.pollToken && r.pollToken.interval) clearInterval(r.pollToken.interval);
  if (r) r.stopped = true;
}

function onRunProgress(runId, s) {
  const r = runs[runId];
  if (!r || r.stopped) return;
  applyStatusToRun(runId, s);
  if (openRunId === runId && !s.done) showLoading(s.step, s.percent);
  renderDock();
}

function onRunDone(runId, status, err) {
  const r = runs[runId];
  if (!r || r.stopped) return;
  r.stopped = true;
  r.done = true;
  if (status && !err) {
    applyStatusToRun(runId, status);
    r.error = null;
    if (openRunId === runId) {
      currentFilename = status.download_name || r.filename || '';
      showSuccess(status, runId, status.output_format || r.format);
      r.seen = true;
    } else {
      showToast(`${runLabel(r)} — CV is ready`, { type: 'success', onClick: () => openRun(runId) });
    }
  } else {
    r.error = (err && err.message) || 'Something went wrong.';
    r.errorCause = err && err.errorCause;
    r.resumable = !!(err && err.resumable);
    if (openRunId === runId) {
      showFailure(runId, r.error, r.errorCause, r.resumable);
      r.seen = true;
    } else {
      showToast(`${runLabel(r)} — run didn't finish`, { type: 'error', onClick: () => openRun(runId) });
    }
  }
  renderDock();
}

async function openRun(runId) {
  const r = runs[runId];
  if (!r) return;
  openRunId = runId;
  r.seen = true;
  currentRunId = runId;
  currentFormat = r.format;
  currentFilename = r.filename || '';
  activeDocument = 'cv';
  cachedDownloads = { cv: null, cover_letter: null };
  runDrawerTitle.textContent = runLabel(r);
  openDrawer();
  // Immediate view from what we already know, then refresh with a full /status.
  if (!r.done) showLoading(r.step, r.percent);
  else if (r.error) showFailure(runId, r.error, r.errorCause, r.resumable);
  try {
    const resp = await apiFetch(`/api/tailor/${runId}/status`);
    if (resp.ok) {
      const s = await resp.json();
      applyStatusToRun(runId, s);
      if (openRunId !== runId) return;
      if (!s.done) showLoading(s.step, s.percent);
      else if (s.error) showFailure(runId, s.error, s.error_cause, s.resumable);
      else {
        currentFormat = s.output_format || r.format;
        currentFilename = s.download_name || r.filename || '';
        showSuccess(s, runId, currentFormat);
      }
    }
  } catch (_) { /* keep the optimistic view */ }
  renderDock();
}

async function cancelRun(runId) {
  try { await apiFetch(`/api/tailor/${runId}/cancel`, { method: 'POST' }); } catch (_) {}
  stopTracking(runId);
  delete runs[runId];
  if (openRunId === runId) closeDrawer();
  renderDock();
}

async function resumeRun(runId) {
  try {
    const resp = await apiFetch(`/api/tailor/${runId}/resume`, { method: 'POST' });
    if (!resp.ok) {
      let detail = "Couldn't resume this run.";
      try { detail = (await resp.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
  } catch (err) {
    if (openRunId === runId) showFailure(runId, err.message || "Couldn't resume this run.", null, false);
    else showToast(err.message || "Couldn't resume this run.", { type: 'error' });
    return;
  }
  const r = runs[runId] || (runs[runId] = { runId, format: 'pdf', activeDocument: 'cv' });
  r.done = false; r.error = null; r.errorCause = null; r.resumable = false; r.stopped = false;
  r.percent = 8; r.step = 'Resuming…'; r.seen = openRunId === runId;
  if (openRunId === runId) showLoading('Resuming…', 8);
  trackRun(runId);
  renderDock();
}

async function discardRun(runId) {
  try { await apiFetch(`/api/tailor/${runId}/discard`, { method: 'POST' }); } catch (_) {}
  stopTracking(runId);
  addDismissed(runId);
  delete runs[runId];
  if (openRunId === runId) closeDrawer();
  renderDock();
}

function dismissRun(runId) {
  stopTracking(runId);
  addDismissed(runId);
  delete runs[runId];
  if (openRunId === runId) closeDrawer();
  renderDock();
}

// Dismissed run ids (localStorage) so a reload's /api/tailor/runs rebuild doesn't resurrect
// a completed/failed run the user already cleared from the dock.
function getDismissed() {
  try { return new Set(JSON.parse(localStorage.getItem(DISMISSED_KEY) || '[]')); } catch (_) { return new Set(); }
}
function addDismissed(runId) {
  const s = getDismissed();
  s.add(runId);
  try { localStorage.setItem(DISMISSED_KEY, JSON.stringify([...s])); } catch (_) { /* ignore */ }
}

failureResumeBtn.addEventListener('click', () => { if (failureRunId) resumeRun(failureRunId); });
failureDiscardBtn.addEventListener('click', () => { if (failureRunId) discardRun(failureRunId); });
failureStartOverBtn.addEventListener('click', () => closeDrawer());

// -- Run dock (bottom-right) ------------------------------------------------------------

function runStatusMeta(r) {
  if (!r.done && r.queued) return { icon: 'queued', text: 'Waiting for a free slot…' };
  if (!r.done) return { icon: 'spin', text: r.step || 'Tailoring…' };
  if (r.error) return { icon: r.resumable ? 'warn' : 'fail', text: r.errorCause || r.error || 'Run failed' };
  return { icon: 'done', text: 'Ready' };
}

function statusIconEl(icon) {
  const el = document.createElement('span');
  el.className = 'shrink-0 inline-flex items-center justify-center w-4 h-4 text-xs';
  if (icon === 'spin') { el.className += ' rounded-full border-2 border-accent border-t-transparent animate-spin'; }
  else if (icon === 'queued') { el.textContent = '⏳'; }
  else if (icon === 'done') { el.textContent = '✓'; el.classList.add('text-emerald-500', 'font-bold'); }
  else if (icon === 'warn') { el.textContent = '!'; el.classList.add('text-amber-500', 'font-bold'); }
  else { el.textContent = '✕'; el.classList.add('text-red-500', 'font-bold'); }
  return el;
}

function buildDockRow(r) {
  const row = document.createElement('div');
  row.className = 'flex items-center gap-2 px-3 py-2.5';
  const meta = runStatusMeta(r);

  const openArea = document.createElement('button');
  openArea.type = 'button';
  openArea.className = 'flex items-center gap-2 min-w-0 flex-1 text-left';
  openArea.appendChild(statusIconEl(meta.icon));
  const labels = document.createElement('div');
  labels.className = 'min-w-0';
  const title = document.createElement('p');
  title.className = 'text-sm font-medium text-zinc-700 dark:text-zinc-200 truncate';
  title.textContent = runLabel(r);
  const sub = document.createElement('p');
  sub.className = 'text-xs text-zinc-400 truncate';
  sub.textContent = meta.text;
  labels.appendChild(title);
  labels.appendChild(sub);
  openArea.appendChild(labels);
  openArea.addEventListener('click', () => openRun(r.runId));
  row.appendChild(openArea);

  const actions = document.createElement('div');
  actions.className = 'flex items-center gap-1.5 shrink-0';
  const mkBtn = (text, cls, fn) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = `text-xs px-2 py-1 rounded-md ${cls} transition-colors`;
    b.textContent = text;
    b.addEventListener('click', (e) => { e.stopPropagation(); fn(); });
    return b;
  };
  if (!r.done) {
    actions.appendChild(mkBtn('Cancel', 'text-zinc-400 hover:text-red-500', () => cancelRun(r.runId)));
  } else if (r.error && r.resumable) {
    actions.appendChild(mkBtn('Resume', 'bg-accent hover:bg-accent-hover text-white', () => resumeRun(r.runId)));
    actions.appendChild(mkBtn('Discard', 'text-zinc-400 hover:text-red-500', () => discardRun(r.runId)));
  } else {
    actions.appendChild(mkBtn('Dismiss', 'text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300', () => dismissRun(r.runId)));
  }
  row.appendChild(actions);
  return row;
}

function renderDock() {
  const list = Object.values(runs);
  runDock.classList.toggle('hidden', list.length === 0);
  if (list.length === 0) { dockExpanded = false; return; }

  const active = list.filter((r) => !r.done);
  const doneUnseen = list.filter((r) => r.done && !r.seen).length;

  runDockSummary.innerHTML = '';
  if (active.length) {
    runDockSummary.appendChild(statusIconEl('spin'));
    runDockSummary.appendChild(document.createTextNode(
      ` ${active.length} tailoring${active.length > 1 ? ' (multiple)' : ''}…`));
  } else {
    runDockSummary.appendChild(document.createTextNode(`${list.length} run${list.length > 1 ? 's' : ''}`));
  }
  if (doneUnseen) {
    const badge = document.createElement('span');
    badge.className = 'ml-1 inline-flex items-center justify-center min-w-[1.1rem] h-[1.1rem] px-1 rounded-full bg-emerald-500 text-white text-[10px] font-bold';
    badge.textContent = String(doneUnseen);
    runDockSummary.appendChild(badge);
  }

  runDockList.classList.toggle('hidden', !dockExpanded);
  runDockCaret.classList.toggle('rotate-180', dockExpanded);
  runDockList.innerHTML = '';
  // Active first, then done.
  [...active, ...list.filter((r) => r.done)].forEach((r) => runDockList.appendChild(buildDockRow(r)));
}

runDockToggle.addEventListener('click', () => { dockExpanded = !dockExpanded; renderDock(); });

// -- Rebuild tracked runs after a page reload ------------------------------------------

async function rebuildRunsFromServer() {
  let serverRuns = [];
  try {
    const resp = await apiFetch('/api/tailor/runs');
    if (resp.ok) serverRuns = (await resp.json()).runs || [];
  } catch (_) { return; }
  const dismissed = getDismissed();
  for (const s of serverRuns) {
    if (dismissed.has(s.run_id)) continue;
    if (runs[s.run_id]) { applyStatusToRun(s.run_id, s); continue; }
    runs[s.run_id] = {
      runId: s.run_id, format: s.output_format || 'pdf', filename: s.download_name || '',
      company: s.company_name || null, role: s.role_name || null,
      done: s.done, error: s.error, errorCause: s.error_cause, resumable: s.resumable,
      queued: s.queued, percent: s.percent || 0, step: s.step || '',
      application: s.application, hasCoverLetter: s.has_cover_letter,
      activeDocument: 'cv', seen: true, // rebuilt runs start "seen" (no retroactive toast)
    };
    if (!s.done) trackRun(s.run_id);
  }
  renderDock();
}

document.addEventListener('screen:shown', (e) => {
  if (!e.detail || e.detail.name === 'login' || e.detail.name === 'register') return;
  rebuildRunsFromServer();
  if (e.detail.name === 'app') {
    loadFieldMemory();
    restorePrefs();
    focusJobField();
  }
});

// -- Remembered field toggles (short-term memory of details you keep adding) ------------
//
// Fetched from /api/tailor/field-memory: fields you've included across recent tailored CVs,
// surfaced as pre-checked, editable toggles at the top of "More options" so you don't retype
// them. `photo` is a bare checkbox (no value); the rest carry an editable text value.

let fieldMemoryFields = [];

async function loadFieldMemory() {
  if (!fieldMemory) return;
  try {
    const resp = await apiFetch('/api/tailor/field-memory');
    fieldMemoryFields = resp.ok ? ((await resp.json()).fields || []) : [];
  } catch (_) {
    fieldMemoryFields = [];
  }
  renderFieldMemory();
}

function renderFieldMemory() {
  fieldMemoryList.innerHTML = '';
  fieldMemory.classList.toggle('hidden', fieldMemoryFields.length === 0);
  for (const f of fieldMemoryFields) {
    const row = document.createElement('div');
    row.className = 'flex items-center gap-2';
    row.dataset.field = f.field;

    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.className = 'field-memory-check w-4 h-4 rounded border-zinc-300 dark:border-zinc-600 text-accent focus:ring-accent focus:ring-offset-0 shrink-0';
    cb.checked = !!f.default_checked;
    cb.addEventListener('change', updateMoreOptionsSummary);
    row.appendChild(cb);

    if (f.field === 'photo') {
      const label = document.createElement('span');
      label.className = 'text-sm text-zinc-600 dark:text-zinc-300';
      label.textContent = f.label;
      row.appendChild(label);
    } else {
      const label = document.createElement('span');
      label.className = 'text-xs font-medium text-zinc-500 dark:text-zinc-400 w-28 shrink-0';
      label.textContent = f.label;
      const input = document.createElement('input');
      input.type = 'text';
      input.className = 'field-memory-value flex-1 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-800 px-3 py-1.5 text-sm placeholder:text-zinc-400 focus-ring';
      input.value = f.value || '';
      input.maxLength = 300;
      row.appendChild(label);
      row.appendChild(input);
    }
    fieldMemoryList.appendChild(row);
  }
  // Auto-expand More options so remembered items are visible, and refresh the summary line.
  if (fieldMemoryFields.length && moreOptions) moreOptions.open = true;
  updateMoreOptionsSummary();
}

function collectFieldOverrides() {
  const out = [];
  if (!fieldMemoryList) return out;
  for (const row of fieldMemoryList.querySelectorAll('[data-field]')) {
    const field = row.dataset.field;
    const include = row.querySelector('.field-memory-check')?.checked || false;
    const valueEl = row.querySelector('.field-memory-value');
    out.push({ field, include, value: valueEl ? valueEl.value.trim() : '' });
  }
  return out;
}

function updateMoreOptionsSummary() {
  if (!moreOptionsSummary) return;
  const checkedLabels = fieldMemoryFields
    .filter((f) => {
      const row = fieldMemoryList.querySelector(`[data-field="${f.field}"]`);
      return row && row.querySelector('.field-memory-check')?.checked;
    })
    .map((f) => (f.field === 'photo' ? 'photo' : f.label.split(' (')[0].split(' / ')[0].toLowerCase()));
  const text = checkedLabels.length ? `Including: ${checkedLabels.join(', ')}` : '';
  moreOptionsSummary.textContent = text;
  // Visible only when collapsed and there's something to show.
  moreOptionsSummary.classList.toggle('hidden', !text || (moreOptions && moreOptions.open));
}

if (moreOptions) {
  moreOptions.addEventListener('toggle', updateMoreOptionsSummary);
}

// -- Remembered format + cover-letter preferences (localStorage) ------------------------

const PREFS_KEY = 'cvtailor:prefs';

function restorePrefs() {
  let prefs = {};
  try { prefs = JSON.parse(localStorage.getItem(PREFS_KEY) || '{}'); } catch (_) { prefs = {}; }
  if (prefs.format === 'pdf' || prefs.format === 'docx') {
    const radio = document.querySelector(`input[name=format][value="${prefs.format}"]`);
    if (radio) radio.checked = true;
  }
  if (typeof prefs.coverLetter === 'boolean') {
    coverLetterCheckbox.checked = prefs.coverLetter;
    coverLetterExtras.classList.toggle('hidden', !prefs.coverLetter);
  }
}

function savePrefs() {
  const format = document.querySelector('input[name=format]:checked')?.value || 'pdf';
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ format, coverLetter: coverLetterCheckbox.checked }));
  } catch (_) { /* storage disabled -- prefs just won't persist */ }
}

document.querySelectorAll('input[name=format]').forEach((r) => r.addEventListener('change', savePrefs));
coverLetterCheckbox.addEventListener('change', savePrefs);

// -- Auto-focus + keyboard submit ------------------------------------------------------

function focusJobField() {
  const jobUrl = document.getElementById('job-url');
  if (jobUrl && !form.classList.contains('hidden')) {
    try { jobUrl.focus(); } catch (_) { /* ignore */ }
  }
}

form.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
    e.preventDefault();
    if (!submitBtn.disabled) form.requestSubmit();
  }
});

cancelBtn.addEventListener('click', async () => {
  // Inside the drawer's loading view. During a revision, cancel just the revision (keeping
  // the previous version). Otherwise cancel the whole run shown in the drawer.
  if (isRevising && currentCancelToken) {
    cancelBtn.disabled = true;
    const token = currentCancelToken;
    try { await apiFetch(`/api/tailor/${currentRunId}/cancel`, { method: 'POST' }); } catch (_) {}
    if (token.interval) clearInterval(token.interval);
    if (token.reject) token.reject(new Error('Cancelled — kept your previous version.'));
    cancelBtn.disabled = false;
    return;
  }
  if (openRunId) cancelRun(openRunId);
});

reviseBtn.addEventListener('click', async () => {
  const feedback = reviseInput.value.trim();
  reviseError.classList.add('hidden');
  if (!feedback) {
    reviseError.textContent = "Tell us what you'd like changed.";
    reviseError.classList.remove('hidden');
    return;
  }
  if (!currentRunId) return;

  const previousRationale = lastRationale;
  isRevising = true;

  successState.classList.add('hidden');
  successState.classList.remove('flex');
  loadingState.classList.remove('hidden');
  loadingState.classList.add('flex');
  progressBar.style.width = '8%';
  progressMessage.textContent = 'Applying your feedback…';

  try {
    const resp = await apiFetch(`/api/tailor/${currentRunId}/revise`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ feedback }),
    });
    if (!resp.ok) {
      let detail = "Couldn't apply that change.";
      try { detail = (await resp.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }

    currentCancelToken = {};
    const status = await pollUntilDone(currentRunId, currentCancelToken);
    currentCancelToken = null;
    isRevising = false;

    lastRationale = status.rationale;
    cachedDownloads = { cv: null, cover_letter: null };
    downloadLabel.textContent = 'Download';
    renderRationale(status.rationale);
    renderApplicationStatus(status.application);
    updateDocTabs(status.has_cover_letter);
    renderPreview(currentRunId, currentFormat, activeDocument);
    updateReviseUI(status.revision_count, status.max_revisions);
    reviseInput.value = '';

    loadingState.classList.add('hidden');
    loadingState.classList.remove('flex');
    successState.classList.remove('hidden');
    successState.classList.add('flex');
  } catch (err) {
    // Whatever happened, the previous valid version is untouched -- go back to it
    // rather than losing it, and surface what happened inline.
    currentCancelToken = null;
    isRevising = false;
    renderRationale(previousRationale);

    loadingState.classList.add('hidden');
    loadingState.classList.remove('flex');
    successState.classList.remove('hidden');
    successState.classList.add('flex');

    reviseError.textContent = err.message || "Couldn't apply that change.";
    reviseError.classList.remove('hidden');
  }
});

function previewUrlFor(doc) {
  return doc === 'cv'
    ? `/api/tailor/${currentRunId}/preview`
    : `/api/tailor/${currentRunId}/cover-letter/preview`;
}
function resultUrlFor(doc) {
  return doc === 'cv'
    ? `/api/tailor/${currentRunId}/result`
    : `/api/tailor/${currentRunId}/cover-letter/result`;
}
function downloadFilenameFor(doc) {
  const base = currentFilename || 'tailored_cv';
  return doc === 'cv' ? `${base}.${currentFormat}` : `${base}_cover_letter.${currentFormat}`;
}

downloadBtn.addEventListener('click', async () => {
  downloadError.classList.add('hidden');
  const doc = activeDocument;
  const cached = cachedDownloads[doc];
  if (cached) {
    triggerDownload(cached.url, cached.filename);
    return;
  }
  downloadBtn.disabled = true;
  downloadLabel.textContent = 'Preparing…';
  try {
    const resultResp = await apiFetch(resultUrlFor(doc));
    if (!resultResp.ok) {
      let detail = 'Download failed.';
      try { detail = (await resultResp.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    const blob = await resultResp.blob();
    const url = URL.createObjectURL(blob);
    const filename = downloadFilenameFor(doc);
    cachedDownloads[doc] = { url, filename };
    downloadLabel.textContent = `Download ${filename}`;
    triggerDownload(url, filename);
  } catch (err) {
    downloadLabel.textContent = 'Download';
    downloadError.textContent = err.message || 'Download failed.';
    downloadError.classList.remove('hidden');
  } finally {
    downloadBtn.disabled = false;
  }
});

// Bumped on every renderPreview call so a slower-resolving fetch from a since-abandoned
// call (e.g. the cover letter, if switched away from before it responds) can tell it's
// stale and skip touching the DOM -- otherwise it can land after a newer, faster call
// already set the correct preview and silently overwrite (and revoke the blob URL of)
// whatever the user is actually looking at, even though the tab styling still shows the
// right document selected.
let previewRequestId = 0;

async function renderPreview(runId, format, doc) {
  const requestId = ++previewRequestId;
  previewWrap.classList.add('hidden');
  previewFallback.classList.add('hidden');
  previewFrame.classList.add('hidden');
  previewLoading.classList.remove('hidden');
  downloadLabel.textContent = cachedDownloads[doc] ? `Download ${cachedDownloads[doc].filename}` : 'Download';
  downloadError.classList.add('hidden');

  if (format !== 'pdf') {
    previewFallback.classList.remove('hidden');
    return;
  }

  previewWrap.classList.remove('hidden');
  try {
    const resp = await apiFetch(previewUrlFor(doc));
    if (!resp.ok) throw new Error('Preview unavailable.');
    const blob = await resp.blob();
    if (requestId !== previewRequestId) return;
    const oldSrc = previewFrame.src;
    previewFrame.src = URL.createObjectURL(blob);
    if (oldSrc) URL.revokeObjectURL(oldSrc);
    previewLoading.classList.add('hidden');
    previewFrame.classList.remove('hidden');
  } catch (_) {
    if (requestId !== previewRequestId) return;
    previewWrap.classList.add('hidden');
    previewFallback.classList.remove('hidden');
  }
}

const DOC_TAB_ACTIVE_CLASSES = ['bg-white', 'dark:bg-zinc-700', 'text-zinc-900', 'dark:text-white', 'shadow-sm'];
const DOC_TAB_INACTIVE_CLASSES = ['text-zinc-500', 'dark:text-zinc-400'];

function updateDocTabStyles() {
  docTabCv.classList.remove(...DOC_TAB_ACTIVE_CLASSES, ...DOC_TAB_INACTIVE_CLASSES);
  docTabCoverLetter.classList.remove(...DOC_TAB_ACTIVE_CLASSES, ...DOC_TAB_INACTIVE_CLASSES);
  docTabCv.classList.add(...(activeDocument === 'cv' ? DOC_TAB_ACTIVE_CLASSES : DOC_TAB_INACTIVE_CLASSES));
  docTabCoverLetter.classList.add(...(activeDocument === 'cover_letter' ? DOC_TAB_ACTIVE_CLASSES : DOC_TAB_INACTIVE_CLASSES));
}

function updateDocTabs(hasCoverLetter) {
  docTabs.classList.toggle('hidden', !hasCoverLetter);
  if (!hasCoverLetter) activeDocument = 'cv';
  updateDocTabStyles();
}

function switchDocument(doc) {
  if (doc === activeDocument) return;
  activeDocument = doc;
  updateDocTabStyles();
  renderPreview(currentRunId, currentFormat, doc);
}

docTabCv.addEventListener('click', () => switchDocument('cv'));
docTabCoverLetter.addEventListener('click', () => switchDocument('cover_letter'));

function updateReviseUI(revisionCount, maxRevisions) {
  const remaining = maxRevisions - revisionCount;
  reviseError.classList.add('hidden');
  if (remaining <= 0) {
    reviseHint.textContent = `You've used all ${maxRevisions} revisions for this CV — start a new tailoring run for further changes.`;
    reviseInput.disabled = true;
    reviseBtn.disabled = true;
  } else {
    reviseHint.textContent = `${remaining} revision${remaining === 1 ? '' : 's'} left for this CV.`;
    reviseInput.disabled = false;
    reviseBtn.disabled = false;
  }
}

// "Tailor another CV" in the drawer's success view: just close the drawer -- the empty form
// is right there underneath. Refresh field memory since this run is now part of recent history.
restartBtn.addEventListener('click', () => {
  closeDrawer();
  loadFieldMemory();
});

let currentPendingId = null;

function renderApplyToggle(application) {
  filedApplyToggle.innerHTML = '';
  const btn = buildApplyToggleButton(application.application_id, application.attempt_id, application.is_applied, () => {
    application.is_applied = !application.is_applied;
    renderApplyToggle(application);
  });
  filedApplyToggle.appendChild(btn);
}

function renderApplicationStatus(application) {
  pendingFilingCard.classList.add('hidden');
  filedApplyToggle.innerHTML = '';
  currentPendingId = null;
  pendingError.classList.add('hidden');
  if (filedRemoveBtn) filedRemoveBtn.classList.add('hidden');
  if (filedRemoveError) filedRemoveError.classList.add('hidden');

  if (!application) {
    filedStatus.textContent = 'Rewritten and reformatted for the role you targeted.';
    return;
  }
  if (application.removed) {
    filedStatus.textContent = `Removed from Applications${application.company_name ? ` (${application.company_name})` : ''} — still downloadable below.`;
    return;
  }
  if (application.pending) {
    filedStatus.textContent = 'Rewritten and reformatted for the role you targeted.';
    currentPendingId = application.pending_id;
    pendingCompanyInput.value = application.company_name || '';
    pendingRoleInput.value = application.role_name || '';
    pendingFilingCard.classList.remove('hidden');
    return;
  }
  // Auto-filed into Applications -- say so clearly, and offer to take it back out.
  filedStatus.textContent = `Saved to Applications: ${application.company_name} → ${application.role_name} — ${formatDate(application.created_at)}`;
  renderApplyToggle(application);
  if (filedRemoveBtn) filedRemoveBtn.classList.remove('hidden');
}

filedRemoveBtn && filedRemoveBtn.addEventListener('click', async () => {
  if (!openRunId) return;
  filedRemoveError.classList.add('hidden');
  filedRemoveBtn.disabled = true;
  try {
    const resp = await apiFetch(`/api/tailor/${openRunId}/unfile`, { method: 'POST' });
    if (!resp.ok) {
      let detail = "Couldn't remove it from Applications.";
      try { detail = (await resp.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    const data = await resp.json();
    if (runs[openRunId]) runs[openRunId].application = data.application;
    renderApplicationStatus(data.application);
  } catch (err) {
    filedRemoveError.textContent = err.message || "Couldn't remove it from Applications.";
    filedRemoveError.classList.remove('hidden');
  } finally {
    filedRemoveBtn.disabled = false;
  }
});

pendingSaveBtn.addEventListener('click', async () => {
  if (!currentPendingId) return;
  pendingError.classList.add('hidden');
  const companyName = pendingCompanyInput.value.trim();
  const roleName = pendingRoleInput.value.trim();
  if (!companyName || !roleName) {
    pendingError.textContent = 'Both company and role are required to file this application.';
    pendingError.classList.remove('hidden');
    return;
  }
  pendingSaveBtn.disabled = true;
  try {
    await apiJson(`/api/applications/pending/${currentPendingId}`, 'PATCH', {
      company_name: companyName, role_name: roleName,
    });
    filedStatus.textContent = `Saved to ${companyName} → ${roleName}`;
    pendingFilingCard.classList.add('hidden');
    currentPendingId = null;
  } catch (err) {
    pendingError.textContent = err.message || 'Could not file this application.';
    pendingError.classList.remove('hidden');
  } finally {
    pendingSaveBtn.disabled = false;
  }
});

// Categorical (matched / listed-only / missing) coverage of the JD requirements the
// review pass already maps every bullet against -- deliberately not a numeric "match
// score": an invented single number invites false precision an LLM can't actually back
// up run to run, where this is a direct readout of a real, checkable mapping.
const REQUIREMENT_CHIP_STYLE = {
  matched: { dot: '🟢', classes: 'text-emerald-700 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-950/40' },
  listed_only: { dot: '🟡', classes: 'text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/40' },
  missing: { dot: '🔴', classes: 'text-rose-700 dark:text-rose-400 bg-rose-50 dark:bg-rose-950/40' },
};

function renderRequirementChips(requirements) {
  rationaleRequirementsChips.innerHTML = '';
  rationaleRequirements.classList.toggle('hidden', requirements.length === 0);
  for (const req of requirements) {
    const style = REQUIREMENT_CHIP_STYLE[req.status];
    if (!style) continue;
    const chip = document.createElement('span');
    chip.className = `inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium ${style.classes}`;
    chip.textContent = `${style.dot} ${req.name}`;
    if (req.evidence) chip.title = req.evidence;
    rationaleRequirementsChips.appendChild(chip);
  }
}

function renderRationale(rationale) {
  if (!rationale || (!rationale.summary && !(rationale.changes || []).length)) {
    rationaleCard.classList.add('hidden');
    return;
  }
  rationaleSummary.textContent = rationale.summary || '';
  rationaleSummary.classList.toggle('hidden', !rationale.summary);

  renderRequirementChips(rationale.requirements || []);

  rationaleChanges.innerHTML = '';
  for (const change of (rationale.changes || [])) {
    const li = document.createElement('li');
    li.className = 'flex items-start gap-2 text-sm text-zinc-600 dark:text-zinc-300';
    li.innerHTML = '<span class="mt-1.5 w-1 h-1 rounded-full bg-accent shrink-0"></span><span></span>';
    li.querySelector('span:last-child').textContent = change;
    rationaleChanges.appendChild(li);
  }

  if (rationale.review_note) {
    rationaleReviewText.textContent = rationale.review_note;
    rationaleReview.classList.remove('hidden');
  } else {
    rationaleReview.classList.add('hidden');
  }

  rationaleCard.classList.remove('hidden');
}

function pollUntilDone(runId, cancelToken) {
  return pollJob(`/api/tailor/${runId}/status`, {
    cancelToken,
    onProgress: (s) => {
      progressBar.style.width = `${s.percent}%`;
      progressMessage.textContent = s.step;
    },
  });
}
