const STORAGE_KEY = 'mobile-computer-use-bridge-url';
const DEMO_URL = 'https://peytontolbert.com/mobiledemo/';

const bridgeForm = document.getElementById('bridgeForm');
const bridgeUrlInput = document.getElementById('bridgeUrlInput');
const connectButton = document.getElementById('connectButton');
const openSavedButton = document.getElementById('openSavedButton');
const clearSavedButton = document.getElementById('clearSavedButton');
const demoButton = document.getElementById('demoButton');
const helpButton = document.getElementById('helpButton');
const helpDialog = document.getElementById('helpDialog');
const closeHelpButton = document.getElementById('closeHelpButton');
const statusEl = document.getElementById('status');
const bridgeDetails = document.getElementById('bridgeDetails');

let connectInFlight = false;

function maybeDemoUrl(value) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  try {
    const withScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(raw) ? raw : `https://${raw}`;
    const url = new URL(withScheme);
    if (url.hostname === 'peytontolbert.com' && url.pathname.replace(/\/+$/, '') === '/mobiledemo') {
      return DEMO_URL;
    }
  } catch {
    return '';
  }
  return '';
}

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

function isProbablyPublicHost(hostname) {
  const host = String(hostname || '').toLowerCase();
  return Boolean(host)
    && host !== 'localhost'
    && !host.startsWith('127.')
    && !host.startsWith('10.')
    && !host.startsWith('192.168.')
    && !/^172\.(1[6-9]|2\d|3[0-1])\./.test(host);
}

function connectionHint(error, bridgeUrl) {
  const message = String(error?.message || error || '');
  const hints = [];
  if (error?.name === 'AbortError') {
    hints.push('The bridge did not answer within 6 seconds.');
  } else if (/Failed to fetch|Load failed|NetworkError/i.test(message)) {
    hints.push('The phone could not reach the bridge from this network.');
  } else if (/CORS|origin/i.test(message)) {
    hints.push('The bridge rejected the app origin. Restart the latest bridge.');
  } else {
    hints.push(message || 'The bridge check failed.');
  }
  if (bridgeUrl) {
    const url = new URL(bridgeUrl);
    if (isProbablyPublicHost(url.hostname)) {
      hints.push('This looks like an external address. Confirm port forwarding is intentional and the computer is trusted.');
    } else {
      hints.push('For LAN use, keep the phone and computer on the same Wi-Fi.');
    }
  }
  hints.push('You can still try opening the bridge directly.');
  return hints.join(' ');
}

function renderDetails(bridgeUrl, health) {
  const providers = Array.isArray(health.providers) ? health.providers : [];
  const workspaces = Array.isArray(health.allowed_workspaces) ? health.allowed_workspaces.join('\n') : '';
  bridgeDetails.innerHTML = '';
  const rows = [
    ['Bridge', bridgeUrl],
    ['Protocol', health.protocol || 'unknown'],
    ['Workspaces', workspaces || 'unknown'],
    ['Network', isProbablyPublicHost(new URL(bridgeUrl).hostname) ? 'External address. Use only with intentional port forwarding.' : 'Local/private network address.'],
  ];
  for (const [label, value] of rows) {
    const term = document.createElement('dt');
    term.textContent = label;
    const detail = document.createElement('dd');
    detail.textContent = value;
    bridgeDetails.append(term, detail);
  }
  const providerTerm = document.createElement('dt');
  providerTerm.textContent = 'Providers';
  const providerDetail = document.createElement('dd');
  const providerList = document.createElement('div');
  providerList.className = 'providerList';
  for (const provider of providers) {
    const badge = document.createElement('div');
    badge.className = 'providerBadge';
    badge.dataset.available = String(Boolean(provider.available));
    const name = document.createElement('strong');
    name.textContent = provider.name || provider.id || 'Provider';
    const state = document.createElement('span');
    state.textContent = provider.available ? 'Ready' : 'Missing';
    badge.append(name, state);
    providerList.appendChild(badge);
  }
  providerDetail.appendChild(providerList);
  bridgeDetails.append(providerTerm, providerDetail);
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

function openDemo() {
  setStatus('Opening review demo...', 'pending');
  window.location.assign(DEMO_URL);
}

async function connect(value, shouldOpen = true) {
  if (connectInFlight) return;
  const demoUrl = maybeDemoUrl(value);
  if (demoUrl) {
    localStorage.setItem(STORAGE_KEY, demoUrl);
    bridgeUrlInput.value = demoUrl;
    setStatus('Review demo is ready.', 'ok');
    if (shouldOpen) openDemo();
    return;
  }
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
    const message = connectionHint(error, bridgeUrl);
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

demoButton.addEventListener('click', openDemo);

helpButton.addEventListener('click', () => {
  if (typeof helpDialog.showModal === 'function') {
    helpDialog.showModal();
  } else {
    helpDialog.setAttribute('open', '');
  }
});

closeHelpButton.addEventListener('click', () => {
  helpDialog.close();
});

const savedBridgeUrl = localStorage.getItem(STORAGE_KEY) || '';
if (savedBridgeUrl) {
  bridgeUrlInput.value = savedBridgeUrl;
  setStatus('Reconnecting to saved bridge...', 'pending');
  connect(savedBridgeUrl, false);
}
