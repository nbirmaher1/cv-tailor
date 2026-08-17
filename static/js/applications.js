// Applications browser: mirrors the on-disk "folder per company, folder per
// role" layout, plus a "Needs info" inbox for attempts that finished but
// couldn't be filed automatically.

const appsPendingSection = document.getElementById('apps-pending-section');
const appsPendingList = document.getElementById('apps-pending-list');
const appsEmpty = document.getElementById('apps-empty');
const appsTree = document.getElementById('apps-tree');

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

  row.append(label, actions);
  return row;
}

function renderTree(tree) {
  appsTree.innerHTML = '';
  appsEmpty.classList.toggle('hidden', tree.length > 0);
  for (const company of tree) {
    const companyDetails = el('details', 'rounded-xl border border-zinc-100 dark:border-zinc-800 overflow-hidden', { open: true });
    const companySummary = el('summary', 'cursor-pointer select-none px-4 py-3 text-sm font-semibold text-zinc-900 dark:text-white hover:bg-zinc-50 dark:hover:bg-zinc-800/50', { textContent: company.company_name });
    companyDetails.appendChild(companySummary);

    const roleWrap = el('div', 'px-4 pb-3 space-y-2');
    for (const role of company.roles) {
      const roleDetails = el('details', 'rounded-lg border border-zinc-100 dark:border-zinc-800', { open: true });
      const roleSummary = el('summary', 'cursor-pointer select-none px-3 py-2 text-xs font-medium text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800/50', { textContent: role.role_name });
      roleDetails.appendChild(roleSummary);
      const attemptsWrap = el('div', 'px-3 pb-2 space-y-1.5');
      role.attempts.forEach(a => attemptsWrap.appendChild(attemptRow(a, 'filed')));
      roleDetails.appendChild(attemptsWrap);
      roleWrap.appendChild(roleDetails);
    }
    companyDetails.appendChild(roleWrap);
    appsTree.appendChild(companyDetails);
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
  if (treeResp.ok) renderTree(await treeResp.json());
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
