const BRIDGE_URL = 'ws://127.0.0.1:8765';

let bridgeSocket = null;
let reconnectTimer = null;
let heartbeatTimer = null;
let reconnectDelay = 1000;

function sendBridgeMessage(message) {
  if (!bridgeSocket || bridgeSocket.readyState !== WebSocket.OPEN) return false;
  bridgeSocket.send(JSON.stringify(message));
  return true;
}

async function getSettings() {
  return chrome.storage.local.get(['profileId', 'bridgeKey']);
}

async function setConnectionState(state, detail = '') {
  await chrome.storage.local.set({ connectionState: state, connectionDetail: detail });
  chrome.runtime.sendMessage({ type: 'BRIDGE_STATE', state, detail }).catch(() => {});
}

function scheduleReconnect() {
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(() => connectToBridge(), reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 2, 15000);
}

async function connectToBridge() {
  const { profileId, bridgeKey } = await getSettings();
  if (!profileId || !bridgeKey) {
    await setConnectionState('setup_required', 'Isi ID profil dan kunci bridge di popup extension.');
    return;
  }
  if (bridgeSocket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(bridgeSocket.readyState)) return;

  await setConnectionState('connecting');
  const socket = new WebSocket(BRIDGE_URL);
  bridgeSocket = socket;

  socket.addEventListener('open', () => {
    reconnectDelay = 1000;
    socket.send(JSON.stringify({ type: 'hello', profileId, bridgeKey }));
    if (heartbeatTimer) clearInterval(heartbeatTimer);
    heartbeatTimer = setInterval(() => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'ping' }));
    }, 20000);
  });

  socket.addEventListener('message', async (event) => {
    let message;
    try {
      message = JSON.parse(event.data);
    } catch {
      return;
    }

    if (message.type === 'hello_ack') {
      await setConnectionState('connected', `Terhubung sebagai ${profileId}`);
      return;
    }
    if (message.type === 'ping') {
      socket.send(JSON.stringify({ type: 'pong' }));
      return;
    }
    if (message.type !== 'generate_prompt') return;

    const currentSettings = await getSettings();
    if (message.profileId !== currentSettings.profileId) {
      socket.send(JSON.stringify({
        type: 'command_result',
        requestId: message.requestId,
        profileId: currentSettings.profileId,
        status: 'wrong_profile',
        detail: 'Perintah ini ditujukan ke profil Chrome lain.',
      }));
      return;
    }

    try {
      const tabs = await chrome.tabs.query({ url: 'https://gemini.google.com/*' });
      const tab = tabs.find((candidate) => candidate.active) || tabs[0];
      if (!tab?.id) throw new Error('Buka tab Gemini pada profil ini terlebih dahulu.');

      const result = await chrome.tabs.sendMessage(tab.id, {
        type: 'SEND_GEMINI_PROMPT',
        requestId: message.requestId,
        prompt: message.prompt,
      });

      socket.send(JSON.stringify({
        type: 'command_result',
        requestId: message.requestId,
        profileId: currentSettings.profileId,
        status: result?.status || 'unknown',
        detail: result?.detail || '',
      }));
    } catch (error) {
      socket.send(JSON.stringify({
        type: 'command_result',
        requestId: message.requestId,
        profileId: currentSettings.profileId,
        status: 'error',
        detail: error.message || 'Extension tidak dapat menjangkau tab Gemini.',
      }));
    }
  });

  socket.addEventListener('close', () => {
    if (heartbeatTimer) clearInterval(heartbeatTimer);
    heartbeatTimer = null;
    if (bridgeSocket === socket) bridgeSocket = null;
    void setConnectionState('disconnected');
    scheduleReconnect();
  });

  socket.addEventListener('error', () => {
    void setConnectionState('disconnected', 'Bridge Python lokal belum berjalan.');
    try { socket.close(); } catch {}
  });
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === 'SAVE_BRIDGE_SETTINGS') {
    chrome.storage.local.set({
      profileId: String(message.profileId || '').trim(),
      bridgeKey: String(message.bridgeKey || '').trim(),
    }).then(() => {
      if (bridgeSocket) bridgeSocket.close();
      bridgeSocket = null;
      reconnectDelay = 1000;
      void connectToBridge();
      sendResponse({ ok: true });
    }).catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }

  if (message?.type === 'GET_BRIDGE_STATUS') {
    getSettings().then(async (settings) => {
      const state = await chrome.storage.local.get(['connectionState', 'connectionDetail']);
      sendResponse({ ...settings, ...state });
    });
    return true;
  }

  if (message?.type === 'RECONNECT_BRIDGE') {
    void connectToBridge();
    sendResponse({ ok: true });
  }
});

chrome.runtime.onInstalled.addListener(() => void connectToBridge());
chrome.runtime.onStartup.addListener(() => void connectToBridge());
void connectToBridge();