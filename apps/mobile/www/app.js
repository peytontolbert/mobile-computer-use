const STORAGE_KEY = 'mobile-computer-use-bridge-url';

const bridgeForm = document.getElementById('bridgeForm');
const bridgeUrlInput = document.getElementById('bridgeUrlInput');
const connectButton = document.getElementById('connectButton');
const openSavedButton = document.getElementById('openSavedButton');
const clearSavedButton = document.getElementById('clearSavedButton');
const statusEl = document.getElementById('status');
const bridgeDetails = document.getElementById('bridgeDetails');

let connectInFlight = false;

function normalizeBridgeUrl(value) {
  const raw = String(value || '').trim();
  if (!raw) throw new Error('Enter the bridge URL printed by the computer.');
  const withScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(raw) ? raw : `http://${raw}`;
  const url = new URL(withScheme);
  if (!['http:', 'https:'].includes(url.protocol)) {
    throw new Error('Bridge URL must start with http:// or https://.');
  }
  url.pathname = url.pathname.replace(/\/(?:mobile)?\/?$/, '');
  url.search = '';
  url.hash = '';
  return url.toString().replace(/\/$/, '');
}

function setStatus(message, mode = '') {
  statusEl.textContent = message;
  statusEl.dataset.mode = mode;
}

function renderDetails(bridgeUrl, health) {
  const providers = Array.isArray(health.providers) ? health.providers : [];
  const providerText = providers
    .map((provider) => `${provider.name || provider.id}: ${provider.available ? 'available' : 'missing'}`)
    .join('\n');
  const workspaces = Array.isArray(health.allowed_workspaces) ? health.allowed_workspaces.join('\n') : '';
  bridgeDetails.innerHTML = '';
  for (const [label, value] of [
    ['Bridge', bridgeUrl],
    ['Protocol', health.protocol || 'unknown'],
    ['Providers', providerText || 'unknown'],
    ['Workspaces', workspaces || 'unknown'],
  ]) {
    const term = document.createElement('dt');
    term.textContent = label;
    const detail = document.createElement('dd');
    detail.textContent = value;
    bridgeDetails.append(term, detail);
  }
  bridgeDetails.classList.remove('hidden');
}

async function checkBridge(bridgeUrl) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 6000);
  try {
    const response = await fetch(`${bridgeUrl}/health`, {
      method: 'GET',
      cache: 'no-store',
      signal: controller.signal,
    });
    const payload = await response.json();
    if (!response.ok || payload.status !== 'ok') {
      throw new Error(payload.error || `Bridge returned HTTP ${response.status}`);
    }
    return payload;
  } finally {
    window.clearTimeout(timeout);
  }
}

function openBridge(bridgeUrl) {
  window.location.assign(`${bridgeUrl}/mobile`);
}

async function connect(value, shouldOpen = true) {
  if (connectInFlight) return;
  connectInFlight = true;
  connectButton.disabled = true;
  openSavedButton.disabled = true;
  let bridgeUrl = '';
  try {
    bridgeUrl = normalizeBridgeUrl(value);
    bridgeUrlInput.value = bridgeUrl;
    setStatus('Checking bridge...', 'pending');
    const health = await checkBridge(bridgeUrl);
    localStorage.setItem(STORAGE_KEY, bridgeUrl);
    renderDetails(bridgeUrl, health);
    setStatus('Bridge is reachable.', 'ok');
    if (shouldOpen) openBridge(bridgeUrl);
  } catch (error) {
    const message = error.name === 'AbortError' ? 'Bridge check timed out.' : error.message;
    if (bridgeUrl && shouldOpen) {
      localStorage.setItem(STORAGE_KEY, bridgeUrl);
      setStatus(`${message || 'Could not check bridge.'} Opening anyway...`, 'pending');
      window.setTimeout(() => openBridge(bridgeUrl), 300);
    } else {
      setStatus(message || 'Could not reach bridge.', 'error');
    }
  } finally {
    connectInFlight = false;
    connectButton.disabled = false;
    openSavedButton.disabled = false;
  }
}

bridgeForm.addEventListener('submit', (event) => {
  event.preventDefault();
  connect(bridgeUrlInput.value, true);
});

openSavedButton.addEventListener('click', () => {
  const saved = localStorage.getItem(STORAGE_KEY) || bridgeUrlInput.value;
  connect(saved, true);
});

clearSavedButton.addEventListener('click', () => {
  localStorage.removeItem(STORAGE_KEY);
  bridgeUrlInput.value = '';
  bridgeDetails.classList.add('hidden');
  setStatus('Saved bridge cleared.');
});

const savedBridgeUrl = localStorage.getItem(STORAGE_KEY) || '';
if (savedBridgeUrl) {
  bridgeUrlInput.value = savedBridgeUrl;
  connect(savedBridgeUrl, false);
}
