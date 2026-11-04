const profileInput = document.querySelector('#profileId');
const keyInput = document.querySelector('#bridgeKey');
const statusElement = document.querySelector('#status');
const saveButton = document.querySelector('#saveSettings');
let formDirty = false;
let settingsLoaded = false;

function showStatus(state, detail = '') {
  statusElement.dataset.state = state;
  const labels = {
    setup_required: 'Isi ID profil dan kunci bridge.',
    connecting: 'Menghubungkan ke Python...',
    connected: detail || 'Terhubung ke Python.',
    disconnected: detail || 'Bridge Python belum berjalan.',
  };
  statusElement.textContent = labels[state] || detail || state;
}

function refreshStatus() {
  chrome.runtime.sendMessage({ type: 'GET_BRIDGE_STATUS' }, (result) => {
    if (chrome.runtime.lastError) {
      showStatus('disconnected', 'Service worker belum siap. Muat ulang halaman pengaturan.');
      return;
    }
    if (result) {
      if (!settingsLoaded) {
        if (!formDirty) {
          profileInput.value = result.profileId || '';
          keyInput.value = result.bridgeKey || '';
        }
        settingsLoaded = true;
      }
      showStatus(result.connectionState || 'setup_required', result.connectionDetail || '');
    }
  });
}

profileInput.addEventListener('input', () => { formDirty = true; });
keyInput.addEventListener('input', () => { formDirty = true; });

saveButton.addEventListener('click', () => {
  const profileId = profileInput.value.trim();
  const bridgeKey = keyInput.value.trim();
  if (!profileId || !bridgeKey) {
    showStatus('setup_required');
    return;
  }

  saveButton.disabled = true;
  showStatus('connecting');
  chrome.runtime.sendMessage({ type: 'SAVE_BRIDGE_SETTINGS', profileId, bridgeKey }, (result) => {
    saveButton.disabled = false;
    if (chrome.runtime.lastError || !result?.ok) {
      showStatus('error', result?.error || chrome.runtime.lastError?.message || 'Gagal menyimpan konfigurasi.');
      return;
    }
    formDirty = false;
    settingsLoaded = true;
    setTimeout(refreshStatus, 400);
  });
});

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type === 'BRIDGE_STATE') showStatus(message.state, message.detail);
});

refreshStatus();
setInterval(refreshStatus, 1500);