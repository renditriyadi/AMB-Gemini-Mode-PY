(() => {
  if (window.__GEMINI_PYTHON_BRIDGE_READY__) return;
  window.__GEMINI_PYTHON_BRIDGE_READY__ = true;

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
    const style = window.getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  }

  function firstVisible(selectors) {
    for (const selector of selectors) {
      const elements = Array.from(document.querySelectorAll(selector));
      const match = elements.reverse().find(isVisible);
      if (match) return match;
    }
    return null;
  }

  function setComposerText(element, text) {
    element.focus();
    if (element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement) {
      const prototype = element instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
      if (setter) setter.call(element, text);
      else element.value = text;
      element.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
      element.dispatchEvent(new Event('change', { bubbles: true }));
    } else {
      element.replaceChildren(document.createTextNode(text));
      element.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
    }
    return (element.value || element.innerText || element.textContent || '').includes(text);
  }

  async function sendPrompt(prompt) {
    if (location.hostname !== 'gemini.google.com') {
      return { status: 'wrong_page', detail: 'Tab aktif bukan Gemini.' };
    }
    if (typeof prompt !== 'string' || !prompt.trim() || prompt.length > 5000) {
      return { status: 'invalid_prompt', detail: 'Prompt kosong atau melebihi 5000 karakter.' };
    }

    const composer = firstVisible(COMPOSER_SELECTORS);
    if (!composer) {
      return { status: 'composer_not_found', detail: 'Kolom chat Gemini tidak ditemukan atau belum siap.' };
    }
    if (!setComposerText(composer, prompt.trim())) {
      return { status: 'fill_failed', detail: 'Teks prompt belum terlihat di kolom chat.' };
    }

    await new Promise((resolve) => setTimeout(resolve, 300));
    const sendButton = firstVisible(SEND_BUTTON_SELECTORS);
    if (!sendButton || sendButton.disabled || sendButton.getAttribute('aria-disabled') === 'true') {
      return { status: 'send_button_unavailable', detail: 'Tombol kirim belum tersedia; prompt tidak dikirim.' };
    }

    sendButton.click();
    return { status: 'prompt_sent', detail: 'Tombol kirim Gemini diklik; status generate belum diverifikasi.' };
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== 'SEND_GEMINI_PROMPT') return false;
    sendPrompt(message.prompt)
      .then(sendResponse)
      .catch((error) => sendResponse({ status: 'error', detail: error.message || 'Gagal mengirim prompt.' }));
    return true;
  });
})();