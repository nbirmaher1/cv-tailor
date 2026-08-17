// Home screen: an identity snapshot (from the master CV) plus a condensed
// view of recent tailoring activity -- the post-login landing page once a
// master CV exists. The full company/role tree still lives on its own
// Applications screen; this is a preview with a link through to it.

const homePhoto = document.getElementById('home-photo');
const homeAvatar = document.getElementById('home-avatar');
const homeName = document.getElementById('home-name');
const homeTitle = document.getElementById('home-title');
const homeContact = document.getElementById('home-contact');
const homeSummary = document.getElementById('home-summary');
const homeEmpty = document.getElementById('home-empty');
const homePendingSection = document.getElementById('home-pending-section');
const homePendingList = document.getElementById('home-pending-list');
const homeRecentSection = document.getElementById('home-recent-section');
const homeRecentList = document.getElementById('home-recent-list');

const HOME_RECENT_LIMIT = 5;

function el(tag, className, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (attrs) for (const [k, v] of Object.entries(attrs)) node[k] = v;
  return node;
}

function initials(name) {
  const parts = (name || '').trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  return (parts[0][0] + (parts[1] ? parts[1][0] : '')).toUpperCase();
}

function renderHomeSnapshot(cv) {
  homeName.textContent = cv.full_name || '';

  homeTitle.textContent = cv.target_title || '';
  homeTitle.classList.toggle('hidden', !cv.target_title);

  const contactParts = [cv.email, cv.phone, cv.location, cv.links].filter(Boolean);
  homeContact.textContent = contactParts.join(' · ');
  homeContact.classList.toggle('hidden', contactParts.length === 0);

  const summary = (cv.summary || '').trim();
  const excerpt = summary.length > 180 ? summary.slice(0, 180).trimEnd() + '…' : summary;
  homeSummary.textContent = excerpt;
  homeSummary.classList.toggle('hidden', !excerpt);

  if (cv.has_photo) {
    homePhoto.src = `/api/master-cv/photo?t=${Date.now()}`;
    homePhoto.classList.remove('hidden');
    homeAvatar.classList.add('hidden');
  } else {
    homePhoto.classList.add('hidden');
    homeAvatar.classList.remove('hidden');
    homeAvatar.textContent = initials(cv.full_name);
  }
}

function flattenAttempts(tree) {
  const flat = [];
  for (const company of tree) {
    for (const role of company.roles) {
      for (const attempt of role.attempts) {
        flat.push({ ...attempt, company_name: company.company_name, role_name: role.role_name });
      }
    }
  }
  flat.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
  return flat;
}

function renderHomeActivity(tree, pending) {
  const recent = flattenAttempts(tree).slice(0, HOME_RECENT_LIMIT);

  homeEmpty.classList.toggle('hidden', !(recent.length === 0 && pending.length === 0));

  homePendingSection.classList.toggle('hidden', pending.length === 0);
  homePendingList.innerHTML = '';
  pending.forEach(item => homePendingList.appendChild(window.pendingCard(item)));

  homeRecentSection.classList.toggle('hidden', recent.length === 0);
  homeRecentList.innerHTML = '';
  recent.forEach(attempt => homeRecentList.appendChild(window.attemptRow(attempt, 'filed', { showContext: true })));
}

async function loadHomeScreen() {
  const cvResp = await apiFetch('/api/master-cv');
  if (cvResp.ok) renderHomeSnapshot(await cvResp.json());

  const [treeResp, pendingResp] = await Promise.all([
    apiFetch('/api/applications'),
    apiFetch('/api/applications/pending'),
  ]);
  const tree = treeResp.ok ? await treeResp.json() : [];
  const pending = pendingResp.ok ? await pendingResp.json() : [];
  renderHomeActivity(tree, pending);
}
window.loadHomeScreen = loadHomeScreen;

document.getElementById('home-tailor-btn').addEventListener('click', () => showScreen('app'));
document.getElementById('home-empty-tailor-btn').addEventListener('click', () => showScreen('app'));
document.getElementById('home-edit-cv-btn').addEventListener('click', () => {
  showScreen('master-cv');
  window.loadMasterCVScreen();
});
document.getElementById('home-view-all-link').addEventListener('click', (e) => {
  e.preventDefault();
  showScreen('applications');
  window.loadApplicationsScreen();
});
