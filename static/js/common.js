// Shared helpers used across every screen.

// Wraps fetch with credentials so the session cookie is always sent, and
// reloads to the login screen on a 401 rather than making every screen
// handle that case individually.
async function apiFetch(url, options = {}) {
  const resp = await fetch(url, { ...options, credentials: 'include' });
  if (resp.status === 401 && !url.startsWith('/api/auth/')) {
    window.location.reload();
    throw new Error('Not logged in.');
  }
  return resp;
}

async function apiJson(url, method, body) {
  const resp = await apiFetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await resp.json(); } catch (_) {}
  if (!resp.ok) {
    throw new Error((data && data.detail) || `Request failed (${resp.status}).`);
  }
  return data;
}

function triggerDownload(url, filename) {
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function showScreen(name) {
  document.querySelectorAll('[data-screen]').forEach((el) => {
    el.classList.toggle('hidden', el.dataset.screen !== name);
  });
}

// Whether the current user has a saved master CV -- set once at login and
// updated whenever it changes (an upload completes), so any screen (the
// tailor page in particular) can react without re-fetching /api/auth/me.
window.hasMasterCV = null;
function setHasMasterCV(value) {
  window.hasMasterCV = value;
  document.dispatchEvent(new CustomEvent('mastercv:changed', { detail: { hasMasterCV: value } }));
}

function togglePasswordVisibility(inputEl, buttonEl) {
  const showing = inputEl.type === 'text';
  inputEl.type = showing ? 'password' : 'text';
  buttonEl.setAttribute('aria-label', showing ? 'Show password' : 'Hide password');
  buttonEl.querySelector('.eye-open').classList.toggle('hidden', !showing);
  buttonEl.querySelector('.eye-closed').classList.toggle('hidden', showing);
}

function formatDate(isoString) {
  if (!isoString) return '';
  const d = new Date(isoString.endsWith('Z') ? isoString : isoString + 'Z');
  if (isNaN(d.getTime())) return isoString;
  return d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}
