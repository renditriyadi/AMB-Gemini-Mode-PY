const BRIDGE_URL = 'ws://127.0.0.1:8766';
const MAX_PROMPT_LENGTH = 5000;

let bridgeSocket = null;
let reconnectTimer = null;
let heartbeatTimer = null;
let reconnectDelay = 1000;
let generation = 0;
let pairingWaiter = null;
let pendingGeminiDownload = null;

function isGeminiDirectMediaUrl(rawUrl) {
  try {
    const url = new URL(rawUrl);
    if (url.protocol !== 'https:') return false;
    if (
      url.hostname === 'contribution.usercontent.google.com'
      && url.pathname === '/download'
    ) return true;
    return (url.hostname === 'googleusercontent.com' || url.hostname.endsWith('.googleusercontent.com'))
      && url.pathname.includes('/rd-gg-dl/');
  } catch {
    return false;
  }
}

chrome.webRequest.onBeforeRequest.addListener((details) => {
  if (
    pendingGeminiDownload
    && details.timeStamp >= pendingGeminiDownload.startedAfter - 1000
    && isGeminiDirectMediaUrl(details.url)
  ) {
    pendingGeminiDownload.url = details.url;
  }
}, { urls: [
  'https://*.googleusercontent.com/*',
  'https://contribution.usercontent.google.com/download*',
] });

async function setState(connectionState, connectionDetail = '') {
  await chrome.storage.local.set({ connectionState, connectionDetail });
}

function sendBridgeMessage(socket, message) {
  if (socket.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify(message));
  return true;
}

function startHeartbeat(socket) {
  if (heartbeatTimer) clearInterval(heartbeatTimer);
  heartbeatTimer = setInterval(() => {
    sendBridgeMessage(socket, { type: 'ping' });
  }, 20000);
}

async function findReadyGeminiTab() {
  const deadline = Date.now() + 20000;
  let lastError;
  while (Date.now() < deadline) {
    const tabs = await chrome.tabs.query({ url: 'https://gemini.google.com/*' });
    const candidates = tabs
      .filter((tab) => Number.isInteger(tab.id) && tab.status === 'complete')
      .sort((a, b) => Number(b.active) - Number(a.active));
    for (const tab of candidates) {
      try {
        const readiness = await chrome.tabs.sendMessage(tab.id, { type: 'AMB_CONTENT_READY' });
        if (readiness?.ready === true) return tab;
        lastError = new Error('Content script Gemini tidak mengonfirmasi kesiapan.');
      } catch (error) {
        lastError = error;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  const detail = lastError instanceof Error ? ` (${lastError.message})` : '';
  throw new Error(
    `Tab Gemini belum siap menerima perintah extension. Muat ulang extension dan refresh tab Gemini${detail}`
  );
}

async function waitForReadyGeminiTab(tabId, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const tab = await chrome.tabs.get(tabId);
      if (tab.status === 'complete' && tab.url?.startsWith('https://gemini.google.com/')) {
        const readiness = await chrome.tabs.sendMessage(tabId, { type: 'AMB_CONTENT_READY' });
        if (readiness?.ready === true) return tab;
      }
    } catch {
      // The content script is not ready until its document finishes loading.
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new Error('Chat Gemini baru tidak siap. Refresh tab Gemini lalu coba lagi.');
}

function isRequestedMediaDownload(item, mediaType, startedAfter, directUrl = '') {
  if (Date.parse(item.startTime || '') < startedAfter - 1500) return false;
  if (directUrl && (item.url === directUrl || item.finalUrl === directUrl)) return true;
  const mimeType = (item.mime || '').toLowerCase();
  const filename = item.filename.toLowerCase();
  const url = `${item.url || ''} ${item.finalUrl || ''}`.toLowerCase();
  const imageMatch = mimeType.startsWith('image/')
    || /\.(png|jpe?g|webp|gif|avif)(?:[?#\s]|$)/i.test(`${filename} ${url}`);
  const videoMatch = mimeType.startsWith('video/')
    || /\.(mp4|webm|mov|m4v)(?:[?#\s]|$)/i.test(`${filename} ${url}`);
  return mediaType === 'image' ? imageMatch : videoMatch;
}

async function waitForMediaDownload(mediaType, startedAfter, chatId) {
  const deadline = Date.now() + 600000;
  let directDownloadId = null;
  while (Date.now() < deadline) {
    const items = await chrome.downloads.search({
      startedAfter: new Date(startedAfter - 1500).toISOString(),
      orderBy: ['-startTime'],
      limit: 20,
    });
    const download = items.find((item) => directDownloadId !== null
      ? item.id === directDownloadId
      : isRequestedMediaDownload(
        item,
        mediaType,
        startedAfter,
        pendingGeminiDownload?.url || '',
      ));
    if (download?.state === 'complete') return download;
    if (download?.state === 'interrupted' && directDownloadId === null) {
      if (!pendingGeminiDownload?.url) {
        throw new Error(`Unduhan Gemini gagal: ${download.error || 'unduhan terhenti.'}`);
      }
    }
    if (download?.state === 'interrupted' && directDownloadId !== null) {
      throw new Error(`Unduhan Gemini gagal: ${download.error || 'unduhan terhenti.'}`);
    }
    if (
      directDownloadId === null
      && pendingGeminiDownload?.url
      && Date.now() >= startedAfter + (download?.state === 'interrupted' ? 1000 : 2500)
      && (!download || download.state === 'interrupted')
    ) {
      directDownloadId = await chrome.downloads.download({
        url: pendingGeminiDownload.url,
        filename: `AMB-Gemini/${mediaType}-${chatId || Date.now()}`,
        conflictAction: 'uniquify',
        saveAs: false,
      });
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(
    pendingGeminiDownload?.url
      ? 'URL unduh hasil Gemini terdeteksi, tetapi file belum berhasil diunduh setelah 10 menit.'
      : 'URL unduh Gemini tidak terdeteksi dan file belum selesai diunduh setelah 10 menit.',
  );
}

async function processGeneratePrompt(socket, message, profileId) {
  const requestId = message.requestId;
  let promptAccepted = false;
  let chatId = '';
  try {
    if (!['video', 'image'].includes(message.mediaType) || !['9:16', '16:9'].includes(message.ratio)) {
      throw new Error('Jenis media atau rasio tidak valid.');
    }

    const existingTab = await findReadyGeminiTab();
    const freshTab = await chrome.tabs.update(existingTab.id, { url: 'https://gemini.google.com/app' });
    const tab = await waitForReadyGeminiTab(freshTab.id);
    const sent = await chrome.tabs.sendMessage(tab.id, {
      type: 'SEND_GEMINI_PROMPT',
      requestId,
      prompt: message.prompt,
      mediaType: message.mediaType,
      ratio: message.ratio,
    });
    if (sent?.status !== 'prompt_sent') {
      throw new Error(sent?.detail || 'Gemini tidak menerima prompt.');
    }

    chatId = typeof sent.chatId === 'string' ? sent.chatId : '';
    sendBridgeMessage(socket, {
      type: 'command_result',
      requestId,
      profileId,
      status: 'prompt_sent',
      chatId,
      detail: sent.detail || 'Prompt dikirim ke chat Gemini baru.',
    });
    promptAccepted = true;

    pendingGeminiDownload = { startedAfter: Date.now(), url: '' };
    try {
      const mediaResult = await chrome.tabs.sendMessage(tab.id, {
        type: 'WAIT_FOR_GEMINI_MEDIA',
        mediaType: message.mediaType,
      });
      if (mediaResult?.status !== 'download_started') {
        throw new Error(mediaResult?.detail || 'Hasil media atau tombol Download tidak ditemukan.');
      }
      chatId = mediaResult.chatId || chatId;

      const download = await waitForMediaDownload(message.mediaType, pendingGeminiDownload.startedAfter, chatId);
      sendBridgeMessage(socket, {
        type: 'media_result',
        requestId,
        profileId,
        status: 'complete',
        chatId,
        downloadPath: download.filename,
        mimeType: download.mime || '',
        detail: 'Hasil Gemini selesai diunduh.',
      });
    } finally {
      pendingGeminiDownload = null;
    }
  } catch (error) {
    const detail = error instanceof Error ? error.message : 'Generate atau unduhan Gemini gagal.';
    sendBridgeMessage(socket, {
      type: promptAccepted ? 'media_result' : 'command_result',
      requestId,
      profileId,
      status: 'error',
      chatId,
      detail,
    });
  }
}

function scheduleReconnect(expectedGeneration) {
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(() => {
    if (expectedGeneration === generation) void connectToBridge();
  }, reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 2, 15000);
}

async function connectToBridge(pairing = null) {
  if (!pairing && bridgeSocket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(bridgeSocket.readyState)) return;

  if (pairing) {
    generation += 1;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = null;
    pairingWaiter?.reject(new Error('Pairing digantikan oleh permintaan baru.'));
    pairingWaiter = null;
    await chrome.storage.local.remove(['profileId', 'pairingKey']);
    if (bridgeSocket) {
      const old = bridgeSocket;
      bridgeSocket = null;
      if (heartbeatTimer) clearInterval(heartbeatTimer);
      heartbeatTimer = null;
      old.close();
    }
  }

  const myGeneration = ++generation;
  await setState('connecting', pairing ? 'Memasangkan profil AMB...' : 'Menghubungkan extension ke AMB lokal...');
  const socket = new WebSocket(BRIDGE_URL);
  bridgeSocket = socket;

  if (pairing) {
    const waiter = {};
    pairingWaiter = waiter;
    waiter.promise = new Promise((resolve, reject) => {
      waiter.resolve = resolve;
      waiter.reject = reject;
      waiter.timer = setTimeout(() => {
        if (pairingWaiter !== waiter) return;
        pairingWaiter = null;
        reject(new Error('Pairing timeout. Pastikan server AMB berjalan lalu buka ulang profil.'));
        socket.close();
      }, 15000);
    });
  }

  socket.addEventListener('open', async () => {
    if (myGeneration !== generation) return socket.close();
    if (pairing) {
      socket.send(JSON.stringify({ type: 'pair', ticket: pairing.ticket }));
      return;
    }
    const stored = await chrome.storage.local.get(['profileId', 'pairingKey']);
    if (!stored.profileId || !stored.pairingKey) {
      await setState('not_paired', 'Buka profil melalui tombol Buka di AMB untuk memasangkan extension.');
      socket.close();
      return;
    }
    socket.send(JSON.stringify({
      type: 'hello',
      profileId: stored.profileId,
      pairingKey: stored.pairingKey,
    }));
  });

  socket.addEventListener('message', async (event) => {
    if (myGeneration !== generation) return;
    let message;
    try {
      message = JSON.parse(event.data);
    } catch {
      return;
    }

    if (message.type === 'paired') {
      if (typeof message.profileId !== 'string' || typeof message.pairingKey !== 'string') {
        pairingWaiter?.reject(new Error('Respons pairing AMB tidak valid.'));
        socket.close();
        return;
      }
      await chrome.storage.local.set({
        profileId: message.profileId,
        pairingKey: message.pairingKey,
      });
      reconnectDelay = 1000;
      await setState('connected', `Terhubung ke profil ${message.profileId}.`);
      startHeartbeat(socket);
      const tabId = pairing?.tabId;
      if (Number.isInteger(tabId)) {
        try {
          await chrome.tabs.sendMessage(tabId, { type: 'PAIR_COMPLETE' });
        } catch {
          // The pairing page may have been closed after the server accepted the ticket.
        }
      }
      if (pairingWaiter?.timer) clearTimeout(pairingWaiter.timer);
      pairingWaiter?.resolve({ ok: true, profileId: message.profileId });
      pairingWaiter = null;
      return;
    }

    if (message.type === 'hello_ack') {
      reconnectDelay = 1000;
      await setState('connected', `Terhubung ke profil ${message.profileId}.`);
      startHeartbeat(socket);
      return;
    }

    if (message.type !== 'generate_prompt') return;
    const stored = await chrome.storage.local.get(['profileId']);
    if (
      message.profileId !== stored.profileId
      || typeof message.requestId !== 'string'
      || typeof message.prompt !== 'string'
      || !message.prompt.trim()
      || message.prompt.length > MAX_PROMPT_LENGTH
    ) {
      sendBridgeMessage(socket, {
        type: 'command_result',
        requestId: message.requestId,
        profileId: stored.profileId || '',
        status: 'invalid_command',
        detail: 'Perintah tidak cocok dengan profil atau format prompt tidak valid.',
      });
      return;
    }
    void processGeneratePrompt(socket, message, stored.profileId);
  });

  socket.addEventListener('close', async () => {
    if (myGeneration !== generation) return;
    if (bridgeSocket === socket) bridgeSocket = null;
    if (heartbeatTimer) clearInterval(heartbeatTimer);
    heartbeatTimer = null;
    await setState('disconnected', 'Koneksi extension ke AMB terputus.');
    if (pairingWaiter) {
      if (pairingWaiter.timer) clearTimeout(pairingWaiter.timer);
      pairingWaiter.reject(new Error('Pairing terputus. Buka ulang profil dari AMB.'));
      pairingWaiter = null;
    }
    const stored = await chrome.storage.local.get(['profileId', 'pairingKey']);
    if (stored.profileId && stored.pairingKey) scheduleReconnect(myGeneration);
  });

  socket.addEventListener('error', () => {
    void setState('disconnected', 'Bridge AMB lokal belum berjalan.');
    try { socket.close(); } catch {}
  });

  if (pairing) return pairingWaiter.promise;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === 'PAIR_PROFILE') {
    let senderUrl;
    try {
      senderUrl = new URL(sender.tab?.url || '');
    } catch {
      senderUrl = null;
    }
    if (
      typeof message.ticket !== 'string'
      || message.ticket.length < 20
      || !Number.isInteger(sender.tab?.id)
      || senderUrl?.protocol !== 'http:'
      || senderUrl?.hostname !== '127.0.0.1'
      || !senderUrl.pathname.startsWith('/pair')
    ) {
      sendResponse({ ok: false, error: 'Permintaan pairing tidak valid.' });
      return false;
    }
    connectToBridge({ ticket: message.ticket, tabId: sender.tab.id })
      .then(sendResponse)
      .catch((error) => sendResponse({ ok: false, error: error.message || 'Pairing gagal.' }));
    return true;
  }

  if (message?.type === 'GET_EXTENSION_STATUS') {
    chrome.storage.local.get(['profileId', 'connectionState', 'connectionDetail'])
      .then(sendResponse);
    return true;
  }

  if (message?.type === 'RECONNECT_EXTENSION') {
    void connectToBridge();
    sendResponse({ ok: true });
  }
});

chrome.runtime.onInstalled.addListener(() => void connectToBridge());
chrome.runtime.onStartup.addListener(() => void connectToBridge());
void connectToBridge();
