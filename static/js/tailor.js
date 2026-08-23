const tailorEditMasterCvLink = document.getElementById('tailor-edit-master-cv-link');
const tailorCvGate = document.getElementById('tailor-cv-gate');

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
const progressBar = document.getElementById('progress-bar');
const progressMessage = document.getElementById('progress-message');
const cancelBtn = document.getElementById('cancel-btn');
const downloadBtn = document.getElementById('download-btn');
const downloadLabel = document.getElementById('download-label');
const downloadError = document.getElementById('download-error');
const restartBtn = document.getElementById('restart-btn');

const rationaleCard = document.getElementById('rationale-card');
const rationaleSummary = document.getElementById('rationale-summary');
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

const filedStatus = document.getElementById('filed-status');
const pendingFilingCard = document.getElementById('pending-card');
const pendingCompanyInput = document.getElementById('pending-company-input');
const pendingRoleInput = document.getElementById('pending-role-input');
const pendingError = document.getElementById('pending-error');
const pendingSaveBtn = document.getElementById('pending-save-btn');

const MAX_CV_BYTES = 10 * 1024 * 1024;
const COVER_LETTER_TEMPLATE_DEFAULT_LABEL = "Have a cover letter you've used before? Drop it here to match its style — optional, up to 10 MB";

let selectedCoverLetterTemplate = null;
let currentRunId = null;
let currentFormat = null;
let currentFilename = null;
let currentCancelToken = null;
let cachedDownloads = { cv: null, cover_letter: null };
let isRevising = false;
let lastRationale = null;
let activeDocument = 'cv';

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
  formData.append('include_cover_letter', wantsCoverLetter ? 'true' : 'false');
  if (wantsCoverLetter) {
    formData.append('cover_letter_notes', coverLetterNotesInput.value.trim());
    formData.append('intelligent_cover_letter', intelligentCoverLetterCheckbox.checked ? 'true' : 'false');
    if (selectedCoverLetterTemplate) formData.append('cover_letter_template', selectedCoverLetterTemplate);
  }

  form.classList.add('hidden');
  loadingState.classList.remove('hidden');
  loadingState.classList.add('flex');
  progressBar.style.width = '3%';
  progressMessage.textContent = 'Starting…';

  try {
    const startResp = await apiFetch('/api/tailor/start', { method: 'POST', body: formData });
    if (!startResp.ok) {
      let detail = 'Request failed.';
      try { detail = (await startResp.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    const { run_id } = await startResp.json();
    currentRunId = run_id;
    currentFormat = format;
    currentFilename = customFilename;
    cachedDownloads = { cv: null, cover_letter: null };
    activeDocument = 'cv';
    isRevising = false;

    currentCancelToken = {};
    const status = await pollUntilDone(run_id, currentCancelToken);
    currentCancelToken = null;

    downloadLabel.textContent = 'Download';
    lastRationale = status.rationale;
    renderRationale(status.rationale);
    renderApplicationStatus(status.application);
    updateDocTabs(status.has_cover_letter);
    renderPreview(run_id, format, activeDocument);
    updateReviseUI(status.revision_count, status.max_revisions);

    loadingState.classList.add('hidden');
    loadingState.classList.remove('flex');
    successState.classList.remove('hidden');
    successState.classList.add('flex');
  } catch (err) {
    currentCancelToken = null;
    loadingState.classList.add('hidden');
    loadingState.classList.remove('flex');
    form.classList.remove('hidden');
    showError(err.message || ('Network error: ' + err));
  } finally {
    submitBtn.disabled = false;
  }
});

cancelBtn.addEventListener('click', async () => {
  if (!currentRunId || !currentCancelToken) return;
  cancelBtn.disabled = true;
  const token = currentCancelToken;
  try {
    await apiFetch(`/api/tailor/${currentRunId}/cancel`, { method: 'POST' });
  } catch (_) {}
  if (token.interval) clearInterval(token.interval);
  const message = isRevising
    ? 'Cancelled — kept your previous version.'
    : 'Cancelled — no changes were made.';
  if (token.reject) token.reject(new Error(message));
  cancelBtn.disabled = false;
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

async function renderPreview(runId, format, doc) {
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
    const oldSrc = previewFrame.src;
    previewFrame.src = URL.createObjectURL(blob);
    if (oldSrc) URL.revokeObjectURL(oldSrc);
    previewLoading.classList.add('hidden');
    previewFrame.classList.remove('hidden');
  } catch (_) {
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

restartBtn.addEventListener('click', () => {
  successState.classList.add('hidden');
  successState.classList.remove('flex');
  form.classList.remove('hidden');
  form.reset();
  rationaleCard.classList.add('hidden');
  downloadError.classList.add('hidden');
  pendingFilingCard.classList.add('hidden');
  currentPendingId = null;

  coverLetterExtras.classList.add('hidden');
  coverLetterTemplateInput.value = '';
  selectedCoverLetterTemplate = null;
  coverLetterTemplateLabel.textContent = COVER_LETTER_TEMPLATE_DEFAULT_LABEL;
  coverLetterTemplateDropzone.classList.remove('border-solid', 'border-emerald-300');

  for (const doc of ['cv', 'cover_letter']) {
    if (cachedDownloads[doc]) URL.revokeObjectURL(cachedDownloads[doc].url);
  }
  cachedDownloads = { cv: null, cover_letter: null };
  activeDocument = 'cv';
  docTabs.classList.add('hidden');
  if (previewFrame.src) URL.revokeObjectURL(previewFrame.src);
  previewFrame.src = '';
  previewWrap.classList.add('hidden');
  previewFallback.classList.add('hidden');
  currentRunId = null;
  currentFormat = null;
  currentFilename = null;
  isRevising = false;
  lastRationale = null;
  reviseInput.value = '';
  reviseInput.disabled = false;
  reviseBtn.disabled = false;
  reviseError.classList.add('hidden');
});

let currentPendingId = null;

function renderApplicationStatus(application) {
  pendingFilingCard.classList.add('hidden');
  currentPendingId = null;
  pendingError.classList.add('hidden');

  if (!application) {
    filedStatus.textContent = 'Rewritten and reformatted for the role you targeted.';
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
  filedStatus.textContent = `Saved to ${application.company_name} → ${application.role_name} — ${formatDate(application.created_at)}`;
}

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

function renderRationale(rationale) {
  if (!rationale || (!rationale.summary && !(rationale.changes || []).length)) {
    rationaleCard.classList.add('hidden');
    return;
  }
  rationaleSummary.textContent = rationale.summary || '';
  rationaleSummary.classList.toggle('hidden', !rationale.summary);

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
  return new Promise((resolve, reject) => {
    cancelToken.reject = reject;
    const interval = setInterval(async () => {
      try {
        const resp = await apiFetch(`/api/tailor/${runId}/status`);
        if (!resp.ok) throw new Error('Lost track of the tailoring job.');
        const s = await resp.json();
        progressBar.style.width = `${s.percent}%`;
        progressMessage.textContent = s.step;
        if (s.done) {
          clearInterval(interval);
          if (s.error) reject(new Error(s.error));
          else resolve(s);
        }
      } catch (err) {
        clearInterval(interval);
        reject(err);
      }
    }, 800);
    cancelToken.interval = interval;
  });
}
