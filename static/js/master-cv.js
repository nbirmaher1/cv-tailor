// Master CV screen: one-time upload/parse, then a full structured form editor
// that edits the saved record directly (no Claude call needed to save an edit).

const mcvUploadState = document.getElementById('mcv-upload-state');
const mcvParsingState = document.getElementById('mcv-parsing-state');
const mcvEditState = document.getElementById('mcv-edit-state');
const mcvSavedState = document.getElementById('mcv-saved-state');

const mcvDropzone = document.getElementById('mcv-dropzone');
const mcvDropzoneEmpty = document.getElementById('mcv-dropzone-empty');
const mcvDropzoneFilled = document.getElementById('mcv-dropzone-filled');
const mcvFileInput = document.getElementById('mcv-file-input');
const mcvFileNameDisplay = document.getElementById('mcv-file-name-display');
const mcvFileClear = document.getElementById('mcv-file-clear');
const mcvUploadBtn = document.getElementById('mcv-upload-btn');
const mcvUploadError = document.getElementById('mcv-upload-error');
const mcvParsingMessage = document.getElementById('mcv-parsing-message');
const mcvParsingBar = document.getElementById('mcv-parsing-bar');

const mcvFullName = document.getElementById('mcv-full-name');
const mcvTargetTitle = document.getElementById('mcv-target-title');
const mcvEmail = document.getElementById('mcv-email');
const mcvPhone = document.getElementById('mcv-phone');
const mcvLocation = document.getElementById('mcv-location');
const mcvLinks = document.getElementById('mcv-links');
const mcvWorkAuthorization = document.getElementById('mcv-work-authorization');
const mcvSummary = document.getElementById('mcv-summary');
const mcvLanguages = document.getElementById('mcv-languages');

const mcvPhotoStatus = document.getElementById('mcv-photo-status');
const mcvPhotoInput = document.getElementById('mcv-photo-input');
const mcvPhotoUploadBtn = document.getElementById('mcv-photo-upload-btn');
const mcvPhotoRemoveBtn = document.getElementById('mcv-photo-remove-btn');

const mcvExperienceList = document.getElementById('mcv-experience-list');
const mcvEducationList = document.getElementById('mcv-education-list');
const mcvSkillsList = document.getElementById('mcv-skills-list');
const mcvExtraSectionsList = document.getElementById('mcv-extra-sections-list');

const mcvAddExperienceBtn = document.getElementById('mcv-add-experience');
const mcvAddEducationBtn = document.getElementById('mcv-add-education');
const mcvAddSkillGroupBtn = document.getElementById('mcv-add-skill-group');
const mcvAddExtraSectionBtn = document.getElementById('mcv-add-extra-section');

const mcvSaveBtn = document.getElementById('mcv-save-btn');
const mcvReplaceBtn = document.getElementById('mcv-replace-btn');
const mcvSaveError = document.getElementById('mcv-save-error');
const mcvSaveSuccess = document.getElementById('mcv-save-success');

let mcvHasPhoto = false;

const inputClasses = 'w-full rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-800 px-3 py-2 text-sm focus-ring';
const textareaClasses = inputClasses + ' resize-y';
const removeBtnClasses = 'text-xs text-zinc-400 hover:text-red-500 transition-colors shrink-0';
const cardClasses = 'rounded-xl border border-zinc-100 dark:border-zinc-800 p-4 space-y-2';

function el(tag, className, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (attrs) for (const [k, v] of Object.entries(attrs)) node[k] = v;
  return node;
}

function labeledInput(label, value, className) {
  const wrap = el('div');
  wrap.appendChild(el('label', 'block text-[11px] font-medium text-zinc-400 mb-1', { textContent: label }));
  const input = el('input', className || inputClasses, { value: value || '' });
  wrap.appendChild(input);
  return { wrap, input };
}

function labeledTextarea(label, value, rows) {
  const wrap = el('div');
  wrap.appendChild(el('label', 'block text-[11px] font-medium text-zinc-400 mb-1', { textContent: label }));
  const textarea = el('textarea', textareaClasses, { value: value || '', rows: rows || 3 });
  wrap.appendChild(textarea);
  return { wrap, textarea };
}

// -- Experience -----------------------------------------------------------

function addExperienceCard(entry) {
  entry = entry || { title: '', company: '', location: '', dates: '', bullets: [] };
  const card = el('div', cardClasses);
  const grid = el('div', 'grid grid-cols-2 gap-2');
  const title = labeledInput('Title', entry.title);
  const company = labeledInput('Company', entry.company);
  const location = labeledInput('Location', entry.location);
  const dates = labeledInput('Dates', entry.dates);
  grid.append(title.wrap, company.wrap, location.wrap, dates.wrap);
  const bullets = labeledTextarea('Bullets (one per line)', (entry.bullets || []).join('\n'), 4);
  const removeRow = el('div', 'flex justify-end');
  const removeBtn = el('button', removeBtnClasses, { type: 'button', textContent: 'Remove role' });
  removeBtn.addEventListener('click', () => card.remove());
  removeRow.appendChild(removeBtn);
  card.append(grid, bullets.wrap, removeRow);
  card._collect = () => ({
    title: title.input.value.trim(),
    company: company.input.value.trim(),
    location: location.input.value.trim(),
    dates: dates.input.value.trim(),
    bullets: bullets.textarea.value.split('\n').map(s => s.trim()).filter(Boolean),
  });
  mcvExperienceList.appendChild(card);
}

// -- Education --------------------------------------------------------------

function addEducationCard(entry) {
  entry = entry || { degree: '', school: '', location: '', dates: '' };
  const card = el('div', cardClasses);
  const grid = el('div', 'grid grid-cols-2 gap-2');
  const degree = labeledInput('Degree', entry.degree);
  const school = labeledInput('School', entry.school);
  const location = labeledInput('Location', entry.location);
  const dates = labeledInput('Dates', entry.dates);
  grid.append(degree.wrap, school.wrap, location.wrap, dates.wrap);
  const removeRow = el('div', 'flex justify-end');
  const removeBtn = el('button', removeBtnClasses, { type: 'button', textContent: 'Remove entry' });
  removeBtn.addEventListener('click', () => card.remove());
  removeRow.appendChild(removeBtn);
  card.append(grid, removeRow);
  card._collect = () => ({
    degree: degree.input.value.trim(),
    school: school.input.value.trim(),
    location: location.input.value.trim(),
    dates: dates.input.value.trim(),
  });
  mcvEducationList.appendChild(card);
}

// -- Skills -------------------------------------------------------------------

function addSkillGroupCard(group) {
  group = group || { category: '', items: [] };
  const card = el('div', cardClasses);
  const category = labeledInput('Category (leave blank for a single flat list)', group.category || '');
  const items = labeledTextarea('Skills (one per line)', (group.items || []).join('\n'), 3);
  const removeRow = el('div', 'flex justify-end');
  const removeBtn = el('button', removeBtnClasses, { type: 'button', textContent: 'Remove group' });
  removeBtn.addEventListener('click', () => card.remove());
  removeRow.appendChild(removeBtn);
  card.append(category.wrap, items.wrap, removeRow);
  card._collect = () => ({
    category: category.input.value.trim() || null,
    items: items.textarea.value.split('\n').map(s => s.trim()).filter(Boolean),
  });
  mcvSkillsList.appendChild(card);
}

// -- Extra sections -----------------------------------------------------------

function addExtraSectionCard(section) {
  section = section || { heading: '', items: [] };
  const card = el('div', cardClasses);
  const heading = labeledInput('Heading (e.g. Certifications, Projects)', section.heading);
  const items = labeledTextarea('Items (one per line)', (section.items || []).join('\n'), 3);
  const removeRow = el('div', 'flex justify-end');
  const removeBtn = el('button', removeBtnClasses, { type: 'button', textContent: 'Remove section' });
  removeBtn.addEventListener('click', () => card.remove());
  removeRow.appendChild(removeBtn);
  card.append(heading.wrap, items.wrap, removeRow);
  card._collect = () => ({
    heading: heading.input.value.trim(),
    items: items.textarea.value.split('\n').map(s => s.trim()).filter(Boolean),
  });
  mcvExtraSectionsList.appendChild(card);
}

mcvAddExperienceBtn.addEventListener('click', () => addExperienceCard());
mcvAddEducationBtn.addEventListener('click', () => addEducationCard());
mcvAddSkillGroupBtn.addEventListener('click', () => addSkillGroupCard());
mcvAddExtraSectionBtn.addEventListener('click', () => addExtraSectionCard());

// -- Load / render --------------------------------------------------------------

function showMcvSubstate(name) {
  mcvUploadState.classList.toggle('hidden', name !== 'upload');
  mcvParsingState.classList.toggle('hidden', name !== 'parsing');
  mcvParsingState.classList.toggle('flex', name === 'parsing');
  mcvEditState.classList.toggle('hidden', name !== 'edit');
  mcvSavedState.classList.toggle('hidden', name !== 'saved');
  mcvSavedState.classList.toggle('flex', name === 'saved');
}

function populateForm(cv) {
  mcvFullName.value = cv.full_name || '';
  mcvTargetTitle.value = cv.target_title || '';
  mcvEmail.value = cv.email || '';
  mcvPhone.value = cv.phone || '';
  mcvLocation.value = cv.location || '';
  mcvLinks.value = cv.links || '';
  mcvWorkAuthorization.value = cv.work_authorization || '';
  mcvSummary.value = cv.summary || '';
  mcvLanguages.value = (cv.languages || []).join('\n');

  mcvHasPhoto = !!cv.has_photo;
  updatePhotoStatus();

  mcvExperienceList.innerHTML = '';
  (cv.experience || []).forEach(addExperienceCard);
  mcvEducationList.innerHTML = '';
  (cv.education || []).forEach(addEducationCard);
  mcvSkillsList.innerHTML = '';
  (cv.skills || []).forEach(addSkillGroupCard);
  mcvExtraSectionsList.innerHTML = '';
  (cv.extra_sections || []).forEach(addExtraSectionCard);
}

function updatePhotoStatus() {
  mcvPhotoStatus.textContent = mcvHasPhoto ? 'Photo saved.' : 'No photo saved.';
  mcvPhotoRemoveBtn.classList.toggle('hidden', !mcvHasPhoto);
}

async function loadMasterCVScreen() {
  mcvSaveError.classList.add('hidden');
  mcvSaveSuccess.classList.add('hidden');
  const resp = await apiFetch('/api/master-cv');
  if (resp.status === 404) {
    setHasMasterCV(false);
    showMcvSubstate('upload');
    return;
  }
  if (!resp.ok) return;
  const cv = await resp.json();
  setHasMasterCV(true);
  populateForm(cv);
  showMcvSubstate('edit');
}
window.loadMasterCVScreen = loadMasterCVScreen;

// -- Upload / parse ---------------------------------------------------------
//
// Two instances of the same widget exist: the dedicated "My CV" screen's
// upload state, and an inline gate on the Tailor screen for anyone who
// skipped that screen (or hasn't saved a master CV yet) -- both hit the same
// /api/master-cv/upload endpoint and become the user's one saved master CV.

function createCvUploadWidget(el_, { onSaved, showState }) {
  let selectedFile = null;

  function selectFile(file) {
    el_.uploadError.classList.add('hidden');
    if (file && file.size > 10 * 1024 * 1024) {
      el_.uploadError.textContent = 'That CV is too large — please keep it under 10 MB.';
      el_.uploadError.classList.remove('hidden');
      el_.fileInput.value = '';
      return;
    }
    selectedFile = file;
    el_.dropzoneEmpty.classList.toggle('hidden', !!file);
    el_.dropzoneFilled.classList.toggle('hidden', !file);
    el_.dropzoneFilled.classList.toggle('flex', !!file);
    if (file) el_.fileNameDisplay.textContent = file.name;
  }

  el_.dropzone.addEventListener('click', () => el_.fileInput.click());
  el_.dropzone.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); el_.fileInput.click(); }
  });
  el_.fileInput.addEventListener('change', () => selectFile(el_.fileInput.files[0] || null));
  ['dragenter', 'dragover'].forEach(evt =>
    el_.dropzone.addEventListener(evt, e => { e.preventDefault(); e.stopPropagation(); el_.dropzone.classList.add('border-accent', 'bg-accent-light'); })
  );
  ['dragleave', 'drop'].forEach(evt =>
    el_.dropzone.addEventListener(evt, e => { e.preventDefault(); e.stopPropagation(); el_.dropzone.classList.remove('border-accent', 'bg-accent-light'); })
  );
  el_.dropzone.addEventListener('drop', e => {
    const file = e.dataTransfer.files[0];
    if (file) { el_.fileInput.files = e.dataTransfer.files; selectFile(file); }
  });
  el_.fileClear.addEventListener('click', (e) => {
    e.stopPropagation();
    el_.fileInput.value = '';
    selectFile(null);
  });

  function pollParse(jobId) {
    return new Promise((resolve, reject) => {
      const interval = setInterval(async () => {
        try {
          const resp = await apiFetch(`/api/master-cv/parse/${jobId}/status`);
          if (!resp.ok) throw new Error('Lost track of the parsing job.');
          const s = await resp.json();
          el_.parsingBar.style.width = `${s.percent}%`;
          el_.parsingMessage.textContent = s.step;
          if (s.done) {
            clearInterval(interval);
            if (s.error) reject(new Error(s.error));
            else resolve();
          }
        } catch (err) {
          clearInterval(interval);
          reject(err);
        }
      }, 800);
    });
  }

  el_.uploadBtn.addEventListener('click', async () => {
    el_.uploadError.classList.add('hidden');
    if (!selectedFile) {
      el_.uploadError.textContent = 'Please choose a CV file first.';
      el_.uploadError.classList.remove('hidden');
      return;
    }
    const formData = new FormData();
    formData.append('cv_file', selectedFile);

    showState('parsing');
    el_.parsingBar.style.width = '3%';
    el_.parsingMessage.textContent = 'Starting…';

    try {
      const startResp = await apiFetch('/api/master-cv/upload', { method: 'POST', body: formData });
      if (!startResp.ok) {
        let detail = 'Upload failed.';
        try { detail = (await startResp.json()).detail || detail; } catch (_) {}
        throw new Error(detail);
      }
      const { job_id } = await startResp.json();
      await pollParse(job_id);
      setHasMasterCV(true);
      await onSaved();
    } catch (err) {
      showState('upload');
      el_.uploadError.textContent = err.message || 'Something went wrong.';
      el_.uploadError.classList.remove('hidden');
    }
  });

  return {
    reset: () => { el_.fileInput.value = ''; selectFile(null); },
  };
}

const mcvUploadWidget = createCvUploadWidget({
  dropzone: mcvDropzone, dropzoneEmpty: mcvDropzoneEmpty, dropzoneFilled: mcvDropzoneFilled,
  fileInput: mcvFileInput, fileNameDisplay: mcvFileNameDisplay, fileClear: mcvFileClear,
  uploadBtn: mcvUploadBtn, uploadError: mcvUploadError,
  parsingMessage: mcvParsingMessage, parsingBar: mcvParsingBar,
}, {
  showState: showMcvSubstate,
  onSaved: async () => { showMcvSubstate('saved'); },
});

mcvReplaceBtn.addEventListener('click', () => {
  mcvUploadWidget.reset();
  showMcvSubstate('upload');
});

document.getElementById('mcv-skip-link').addEventListener('click', (e) => {
  e.preventDefault();
  showScreen('app');
});

document.getElementById('mcv-saved-tailor-btn').addEventListener('click', () => {
  showScreen('app');
});
document.getElementById('mcv-saved-edit-btn').addEventListener('click', () => {
  loadMasterCVScreen();
});
document.getElementById('mcv-saved-home-btn').addEventListener('click', () => {
  showScreen('home');
  if (window.loadHomeScreen) window.loadHomeScreen();
});

// -- Inline "no master CV yet" gate on the Tailor screen -----------------------

const tcgUploadState = document.getElementById('tcg-upload-state');
const tcgParsingState = document.getElementById('tcg-parsing-state');

function showTcgSubstate(name) {
  tcgUploadState.classList.toggle('hidden', name !== 'upload');
  tcgParsingState.classList.toggle('hidden', name !== 'parsing');
  tcgParsingState.classList.toggle('flex', name === 'parsing');
}

createCvUploadWidget({
  dropzone: document.getElementById('tcg-dropzone'),
  dropzoneEmpty: document.getElementById('tcg-dropzone-empty'),
  dropzoneFilled: document.getElementById('tcg-dropzone-filled'),
  fileInput: document.getElementById('tcg-file-input'),
  fileNameDisplay: document.getElementById('tcg-file-name-display'),
  fileClear: document.getElementById('tcg-file-clear'),
  uploadBtn: document.getElementById('tcg-upload-btn'),
  uploadError: document.getElementById('tcg-upload-error'),
  parsingMessage: document.getElementById('tcg-parsing-message'),
  parsingBar: document.getElementById('tcg-parsing-bar'),
}, {
  showState: showTcgSubstate,
  onSaved: async () => { showTcgSubstate('upload'); },
});

// -- Photo --------------------------------------------------------------------

mcvPhotoUploadBtn.addEventListener('click', () => mcvPhotoInput.click());
mcvPhotoInput.addEventListener('change', async () => {
  const file = mcvPhotoInput.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append('photo', file);
  try {
    const resp = await apiFetch('/api/master-cv/photo', { method: 'POST', body: formData });
    if (!resp.ok) throw new Error('Photo upload failed.');
    mcvHasPhoto = true;
    updatePhotoStatus();
  } catch (err) {
    mcvSaveError.textContent = err.message;
    mcvSaveError.classList.remove('hidden');
  }
  mcvPhotoInput.value = '';
});

mcvPhotoRemoveBtn.addEventListener('click', async () => {
  try {
    await apiFetch('/api/master-cv/photo', { method: 'DELETE' });
    mcvHasPhoto = false;
    updatePhotoStatus();
  } catch (_) {}
});

// -- Save ---------------------------------------------------------------------

mcvSaveBtn.addEventListener('click', async () => {
  mcvSaveError.classList.add('hidden');
  mcvSaveSuccess.classList.add('hidden');

  const payload = {
    full_name: mcvFullName.value.trim(),
    target_title: mcvTargetTitle.value.trim(),
    email: mcvEmail.value.trim(),
    phone: mcvPhone.value.trim(),
    location: mcvLocation.value.trim(),
    links: mcvLinks.value.trim(),
    work_authorization: mcvWorkAuthorization.value.trim(),
    photo_path: null,
    summary: mcvSummary.value.trim(),
    languages: mcvLanguages.value.split('\n').map(s => s.trim()).filter(Boolean),
    experience: Array.from(mcvExperienceList.children).map(c => c._collect()),
    education: Array.from(mcvEducationList.children).map(c => c._collect()),
    skills: Array.from(mcvSkillsList.children).map(c => c._collect()),
    extra_sections: Array.from(mcvExtraSectionsList.children).map(c => c._collect()),
  };

  if (!payload.full_name) {
    mcvSaveError.textContent = 'Full name is required.';
    mcvSaveError.classList.remove('hidden');
    return;
  }

  mcvSaveBtn.disabled = true;
  try {
    const resp = await apiJson('/api/master-cv', 'PUT', payload);
    mcvHasPhoto = !!resp.has_photo;
    updatePhotoStatus();
    mcvSaveSuccess.classList.remove('hidden');
  } catch (err) {
    mcvSaveError.textContent = err.message || 'Save failed.';
    mcvSaveError.classList.remove('hidden');
  } finally {
    mcvSaveBtn.disabled = false;
  }
});
