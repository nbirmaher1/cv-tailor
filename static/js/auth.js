// Login/register screens and the boot sequence that decides which screen to
// show first. Each user runs their own local instance of this app; "logged
// in" here just gates access to that person's own saved data.

const userEmailEl = document.getElementById('user-email');
const logoutBtn = document.getElementById('logout-btn');
const navHomeBtn = document.getElementById('nav-home-btn');
const navTailorBtn = document.getElementById('nav-tailor-btn');
const navMasterCvBtn = document.getElementById('nav-master-cv-btn');
const navApplicationsBtn = document.getElementById('nav-applications-btn');
const navAppliedBtn = document.getElementById('nav-applied-btn');

const loginForm = document.getElementById('login-form');
const loginEmail = document.getElementById('login-email');
const loginPassword = document.getElementById('login-password');
const loginError = document.getElementById('login-error');
const goToRegister = document.getElementById('go-to-register');

const registerForm = document.getElementById('register-form');
const registerEmail = document.getElementById('register-email');
const registerPassword = document.getElementById('register-password');
const registerError = document.getElementById('register-error');
const goToLogin = document.getElementById('go-to-login');

function onLoggedIn(user) {
  userEmailEl.textContent = user.email;
  userEmailEl.classList.remove('hidden');
  logoutBtn.classList.remove('hidden');
  navHomeBtn.classList.remove('hidden');
  navTailorBtn.classList.remove('hidden');
  navMasterCvBtn.classList.remove('hidden');
  navApplicationsBtn.classList.remove('hidden');
  navAppliedBtn.classList.remove('hidden');
  setHasMasterCV(user.has_master_cv);

  if (user.has_master_cv) {
    showScreen('home');
    if (window.loadHomeScreen) window.loadHomeScreen();
  } else {
    // First-time landing: prompt for a master CV before the tailor screen,
    // with a skip link (wired in master-cv.js) for anyone who'd rather
    // upload it inline from the Tailor screen instead.
    showScreen('master-cv');
    if (window.loadMasterCVScreen) window.loadMasterCVScreen();
  }
}

function onLoggedOut() {
  userEmailEl.textContent = '';
  userEmailEl.classList.add('hidden');
  logoutBtn.classList.add('hidden');
  navHomeBtn.classList.add('hidden');
  navTailorBtn.classList.add('hidden');
  navMasterCvBtn.classList.add('hidden');
  navApplicationsBtn.classList.add('hidden');
  navAppliedBtn.classList.add('hidden');
  setHasMasterCV(null);
  loginForm.reset();
  registerForm.reset();
  showScreen('login');
}

document.getElementById('login-password-toggle').addEventListener('click', () => {
  togglePasswordVisibility(loginPassword, document.getElementById('login-password-toggle'));
});
document.getElementById('register-password-toggle').addEventListener('click', () => {
  togglePasswordVisibility(registerPassword, document.getElementById('register-password-toggle'));
});

navHomeBtn.addEventListener('click', () => {
  showScreen('home');
  if (window.loadHomeScreen) window.loadHomeScreen();
});
navTailorBtn.addEventListener('click', () => showScreen('app'));
navMasterCvBtn.addEventListener('click', () => {
  showScreen('master-cv');
  if (window.loadMasterCVScreen) window.loadMasterCVScreen();
});
navApplicationsBtn.addEventListener('click', () => {
  showScreen('applications');
  if (window.loadApplicationsScreen) window.loadApplicationsScreen();
});
navAppliedBtn.addEventListener('click', () => {
  showScreen('applied');
  if (window.loadAppliedScreen) window.loadAppliedScreen();
});

async function boot() {
  const resp = await apiFetch('/api/auth/me');
  if (resp.ok) {
    onLoggedIn(await resp.json());
  } else {
    showScreen('login');
  }
}

loginForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  loginError.classList.add('hidden');
  try {
    const data = await apiJson('/api/auth/login', 'POST', {
      email: loginEmail.value.trim(),
      password: loginPassword.value,
    });
    onLoggedIn(data.user);
  } catch (err) {
    loginError.textContent = err.message;
    loginError.classList.remove('hidden');
  }
});

registerForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  registerError.classList.add('hidden');
  try {
    const data = await apiJson('/api/auth/register', 'POST', {
      email: registerEmail.value.trim(),
      password: registerPassword.value,
    });
    onLoggedIn(data.user);
  } catch (err) {
    registerError.textContent = err.message;
    registerError.classList.remove('hidden');
  }
});

goToRegister.addEventListener('click', (e) => {
  e.preventDefault();
  loginError.classList.add('hidden');
  showScreen('register');
});

goToLogin.addEventListener('click', (e) => {
  e.preventDefault();
  registerError.classList.add('hidden');
  showScreen('login');
});

logoutBtn.addEventListener('click', async () => {
  try { await apiFetch('/api/auth/logout', { method: 'POST' }); } catch (_) {}
  onLoggedOut();
});

boot();
