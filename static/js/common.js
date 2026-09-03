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
  document.dispatchEvent(new CustomEvent('screen:shown', { detail: { name } }));
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

// Polls `statusUrl` every 800ms until the server reports `done`. Tolerates up to 3
// consecutive failed status-fetches (a network blip) before giving up -- the job may
// still be succeeding server-side even if one poll request drops, so aborting on the
// very first failure would falsely report a working run as lost. Pass `cancelToken`
// (a plain object) to let a caller cancel mid-poll: it gets `.reject` (call to abort
// with a custom reason) and `.interval` (the setInterval id, for clearInterval).
function pollJob(statusUrl, { onProgress, cancelToken } = {}) {
  return new Promise((resolve, reject) => {
    if (cancelToken) cancelToken.reject = reject;
    let consecutiveFailures = 0;
    const interval = setInterval(async () => {
      try {
        const resp = await apiFetch(statusUrl);
        if (!resp.ok) throw new Error('Lost track of the job.');
        const s = await resp.json();
        consecutiveFailures = 0;
        if (onProgress) onProgress(s);
        if (s.done) {
          clearInterval(interval);
          if (s.error) {
            // Carry the friendly cause + resumability so the caller can offer Resume
            // instead of just showing the (funny) top-line message.
            const err = new Error(s.error);
            err.errorCause = s.error_cause || null;
            err.resumable = !!s.resumable;
            reject(err);
          } else {
            resolve(s);
          }
        }
      } catch (err) {
        consecutiveFailures += 1;
        if (consecutiveFailures >= 3) {
          clearInterval(interval);
          reject(err);
        }
      }
    }, 800);
    if (cancelToken) cancelToken.interval = interval;
  });
}

function formatDate(isoString) {
  if (!isoString) return '';
  const d = new Date(isoString.endsWith('Z') ? isoString : isoString + 'Z');
  if (isNaN(d.getTime())) return isoString;
  return d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}
