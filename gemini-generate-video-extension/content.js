(() => {
  const isPairPage = () => location.protocol === 'http:'
    && location.hostname === '127.0.0.1'
    && location.pathname.startsWith('/pair');
  const COMPOSER_SELECTORS = [
    'rich-textarea [contenteditable="true"]',
    'div.ql-editor[contenteditable="true"]',
    'div[contenteditable="true"]',
    'textarea[placeholder*="Ask Gemini" i]',
    'textarea[placeholder*="Tanya Gemini" i]',
    'textarea[aria-label*="prompt" i]',
  ];
  const SEND_BUTTON_SELECTORS = [
    'button[data-test-id*="send" i]',
    'button[aria-label*="Send message" i]',
    'button[aria-label*="Send" i]',
    'button[aria-label*="Kirim pesan" i]',
    'button[aria-label*="Kirim" i]',
    'button[mattooltip*="Send" i]',
  ];

  function isVisible(element) {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  }

  function firstVisible(selectors, predicate = () => true) {
    for (const selector of selectors) {
      const match = [...document.querySelectorAll(selector)].reverse().find(
        (element) => isVisible(element) && predicate(element)
      );
      if (match) return match;
    }
    return null;
  }

  function currentChatId() {
    const match = location.pathname.match(/^\/app\/([^/]+)/);
    return match?.[1] || '';
  }

  function generatedMedia(mediaType) {
    const selector = mediaType === 'video' ? 'video' : 'img';
    return [...document.querySelectorAll(selector)].filter((element) => {
      if (!isVisible(element)) return false;
      const rect = element.getBoundingClientRect();
      if (mediaType === 'video') {
        return rect.width >= 160 && rect.height >= 100
          && (element.currentSrc || element.src || element.poster);
      }
      return rect.width >= 160 && rect.height >= 100
        && element.complete && element.naturalWidth >= 160 && element.naturalHeight >= 100
        && !/avatar|profile|logo/i.test(element.alt || '');
    });
  }

  function generatedMediaDownloadButton() {
    const downloadSelector = [
      'gem-icon-button[data-test-id*="download" i] button',
      'button[data-test-id*="download" i]',
      'button[aria-label*="download full size" i]',
      'button[aria-label*="download" i]',
      'button[title*="download" i]',
      'button[mattooltip*="download" i]',
      'button[aria-label*="unduh" i]',
      'button[title*="unduh" i]',
      'a[download]',
    ].join(',');
    return firstVisible([downloadSelector], (element) => {
      const label = [
        element.getAttribute('aria-label'),
        element.getAttribute('title'),
        element.getAttribute('mattooltip'),
        element.getAttribute('gemtooltip'),
        element.getAttribute('data-test-id'),
        element.textContent,
      ].filter(Boolean).join(' ');
      return /download|unduh/i.test(label) || element.hasAttribute('download');
    });
  }

  async function waitForGeneratedMedia(mediaType) {
    const timeoutMs = mediaType === 'video' ? 600000 : 240000;
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const chatId = currentChatId();
      const media = generatedMedia(mediaType);
      const downloadButton = media.length ? generatedMediaDownloadButton() : null;
      if (chatId && media.length && downloadButton && !downloadButton.disabled) {
        const downloadStartedAt = Date.now();
        downloadButton.click();
        return { status: 'download_started', chatId, downloadStartedAt };
      }
      await new Promise((resolve) => setTimeout(resolve, 750));
    }
    return {
      status: 'error',
      chatId: currentChatId(),
      detail: `Hasil ${mediaType === 'image' ? 'gambar' : 'video'} atau tombol Download Gemini tidak ditemukan setelah menunggu.`,
    };
  }

  function onPairPage() {
    if (!isPairPage()) return;
    const ticket = location.hash.slice(1);
    history.replaceState(null, '', location.pathname + location.search);
    if (!ticket) return;
    chrome.runtime.sendMessage({ type: 'PAIR_PROFILE', ticket }, (result) => {
      if (chrome.runtime.lastError || !result?.ok) {
        const status = document.querySelector('#status');
        if (status) status.textContent = result?.error || 'Extension belum dapat dipasangkan. Pastikan server AMB berjalan.';
        const button = document.querySelector('#continue');
        if (button) button.style.display = 'inline-block';
      }
    });
  }

  async function sendPrompt(prompt) {
    if (location.hostname !== 'gemini.google.com') {
      return { status: 'wrong_page', detail: 'Tab target bukan Gemini.' };
    }
    if (typeof prompt !== 'string' || !prompt.trim() || prompt.length > 5000) {
      return { status: 'invalid_prompt', detail: 'Prompt kosong atau melebihi 5000 karakter.' };
    }

    const composer = firstVisible(COMPOSER_SELECTORS);
    if (!composer) {
      return { status: 'composer_not_found', detail: 'Kolom prompt Gemini tidak ditemukan atau belum siap.' };
    }

    const text = prompt.trim();
    composer.focus();
    if (composer instanceof HTMLTextAreaElement || composer instanceof HTMLInputElement) {
      const prototype = composer instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
      if (setter) setter.call(composer, text);
      else composer.value = text;
      composer.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
      composer.dispatchEvent(new Event('change', { bubbles: true }));
    } else {
      composer.replaceChildren(document.createTextNode(text));
      composer.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
    }

    const currentText = composer.value || composer.innerText || composer.textContent || '';
    if (!currentText.includes(text)) {
      return { status: 'fill_failed', detail: 'Prompt tidak terlihat pada kolom Gemini.' };
    }

    await new Promise((resolve) => setTimeout(resolve, 300));
    const isSendButton = (element) => {
      const label = [element.getAttribute('aria-label'), element.getAttribute('title'), element.getAttribute('mattooltip')]
        .filter(Boolean).join(' ');
      const testId = element.getAttribute('data-test-id') || '';
      if (/feedback|masukan|share|bagikan/i.test(`${label} ${testId}`)) return false;
      return /\b(send|kirim)\b/i.test(label) || /(?:^|[-_])(send|kirim)(?:[-_]|$)/i.test(testId);
    };
    const button = firstVisible(SEND_BUTTON_SELECTORS, isSendButton);
    if (!button || button.disabled || button.getAttribute('aria-disabled') === 'true') {
      return { status: 'send_button_unavailable', detail: 'Tombol kirim belum siap; prompt tidak dikirim.' };
    }

    button.click();
    return {
      status: 'prompt_sent',
      detail: 'Tombol kirim UI Gemini diklik. Selesainya pembuatan video belum diverifikasi.',
    };
  }

  if (isPairPage()) {
    onPairPage();
    chrome.runtime.onMessage.addListener((message) => {
      if (message?.type === 'PAIR_COMPLETE') {
        const nextPage = new URLSearchParams(location.search).get('next');
        location.replace(nextPage === 'flow' ? 'https://flow.google.com/' : 'https://gemini.google.com/app');
      }
    });
  }

  if (location.hostname === 'gemini.google.com') {
    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      if (message?.type === 'AMB_CONTENT_READY') {
        sendResponse({ ready: true });
        return false;
      }
      if (message?.type === 'WAIT_FOR_GEMINI_MEDIA') {
        waitForGeneratedMedia(message.mediaType)
          .then(sendResponse)
          .catch((error) => sendResponse({
            status: 'error',
            chatId: currentChatId(),
            detail: error instanceof Error ? error.message : 'Gagal menunggu hasil Gemini.',
          }));
        return true;
      }
      if (message?.type !== 'SEND_GEMINI_PROMPT') return false;
      sendPrompt(message.prompt)
        .then(sendResponse)
        .catch((error) => sendResponse({
          status: 'error',
          detail: error instanceof Error ? error.message : 'Gagal mengirim prompt.',
        }));
      return true;
    });
  }
})();
