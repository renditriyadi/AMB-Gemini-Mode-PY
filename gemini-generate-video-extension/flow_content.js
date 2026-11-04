(() => {
  const FLOW_TIMEOUT_MS = 15 * 60 * 1000;

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();

  function queryAll(selector, root = document) {
    const matches = [];
    const visit = (currentRoot) => {
      matches.push(...currentRoot.querySelectorAll(selector));
      if (currentRoot instanceof Element && currentRoot.shadowRoot) {
        visit(currentRoot.shadowRoot);
      }
      for (const element of currentRoot.querySelectorAll('*')) {
        if (element.shadowRoot) visit(element.shadowRoot);
      }
    };
    visit(root);
    return matches;
  }

  function isVisible(element) {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0
      && style.display !== 'none'
      && style.visibility !== 'hidden';
  }

  function accessibleName(element) {
    return normalize([
      element.getAttribute('aria-label'),
      element.getAttribute('title'),
      element.getAttribute('mattooltip'),
      element.getAttribute('data-test-id'),
      element.innerText,
      element.textContent,
    ].filter(Boolean).join(' '));
  }

  async function waitFor(getValue, timeoutMs, errorMessage) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const value = getValue();
      if (value) return value;
      await sleep(250);
    }
    throw new Error(errorMessage);
  }

  async function clickButton(pattern, timeoutMs = 15000) {
    const button = await waitFor(
      () => queryAll('button,[role="button"]')
        .find((element) => isVisible(element) && pattern.test(accessibleName(element))),
      timeoutMs,
      `Tombol Google Flow '${pattern}' tidak ditemukan.`,
    );
    if (button.disabled || button.getAttribute('aria-disabled') === 'true') {
      throw new Error(`Tombol Google Flow '${pattern}' belum aktif.`);
    }
    button.click();
    return button;
  }

  async function selectRadio(pattern) {
    const radio = await waitFor(
      () => queryAll('[role="radio"]')
        .find((element) => isVisible(element) && pattern.test(accessibleName(element))),
      10000,
      `Pilihan Google Flow '${pattern}' tidak tersedia.`,
    );
    if (radio.getAttribute('aria-checked') !== 'true') radio.click();
  }

  async function selectModel(model) {
    const selector = queryAll('button,[role="button"]')
      .find((element) => isVisible(element) && /Pilih rangkaian model/i.test(accessibleName(element)));
    if (!selector) throw new Error('Pemilih model Google Flow tidak ditemukan.');
    if (accessibleName(selector).includes(model)) return;
    selector.click();
    const option = await waitFor(
      () => queryAll('[role="menuitem"]')
        .find((element) => isVisible(element) && accessibleName(element).includes(model)),
      10000,
      `Model Google Flow '${model}' tidak tersedia pada akun ini.`,
    );
    option.click();
  }

  async function ensureAgentInactive() {
    const agentButton = await waitFor(
      () => queryAll('button.agent-mode-chip')
        .find(isVisible),
      15000,
      'Status Agen Flow tidak dapat diverifikasi; prompt tidak dikirim.',
    );
    const pressed = agentButton.getAttribute('aria-pressed');
    if (pressed === 'true') {
      agentButton.click();
      await waitFor(
        () => agentButton.getAttribute('aria-pressed') === 'false',
        5000,
        'Agen Flow masih aktif; prompt tidak dikirim.',
      );
    } else if (pressed !== 'false') {
      throw new Error('Status Agen Flow tidak jelas; prompt tidak dikirim.');
    }
  }

  function findComposer() {
    return queryAll(
      'textarea,input,[role="textbox"],[contenteditable="true"]',
    ).find((element) => {
      if (!isVisible(element)) return false;
      const label = normalize([
        element.getAttribute('aria-label'),
        element.getAttribute('placeholder'),
        element.getAttribute('data-placeholder'),
      ].filter(Boolean).join(' '));
      return /Apa yang ingin Anda buat\?/i.test(label);
    });
  }

  function setComposerText(composer, value) {
    composer.focus();
    if (composer instanceof HTMLTextAreaElement || composer instanceof HTMLInputElement) {
      const prototype = composer instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
      if (setter) setter.call(composer, value);
      else composer.value = value;
    } else {
      composer.replaceChildren(document.createTextNode(value));
    }
    composer.dispatchEvent(new InputEvent('input', {
      bubbles: true,
      inputType: 'insertText',
      data: value,
    }));
    composer.dispatchEvent(new Event('change', { bubbles: true }));
  }

  async function configureGeneration(message) {
    const projectButton = queryAll('button,[role="button"]')
      .find((element) => isVisible(element) && /Project baru/i.test(accessibleName(element)));
    if (projectButton) {
      projectButton.click();
      await waitFor(
        () => location.pathname.startsWith('/project/'),
        30000,
        'Google Flow tidak membuka project baru.',
      );
    }
    const composer = await waitFor(
      findComposer,
      30000,
      'Kolom prompt Google Flow tidak ditemukan.',
    );
    await ensureAgentInactive();
    setComposerText(composer, message.prompt.trim());

    await clickButton(/Pemicu setelan/i);
    await selectRadio(message.mediaType === 'image' ? /Gambar/i : /Video/i);
    await selectRadio(new RegExp(message.ratio.replace(':', '\\:')));
    await selectModel(message.model);
    if (message.mediaType === 'video') {
      await selectRadio(new RegExp(message.resolution, 'i'));
      await selectRadio(new RegExp(message.duration, 'i'));
    }
    await selectRadio(/x1/i);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  }

  function mediaId(source) {
    return source.match(/\/image\/([^/?#]+)/)?.[1] || '';
  }

  async function uploadReference(message) {
    if (!message.referenceUrl) return;
    const materialToggle = queryAll('button,[role="button"]')
      .find((element) => isVisible(element) && /Tambahkan bahan ke kotak perintah/i.test(accessibleName(element)));
    if (!materialToggle) throw new Error('Tombol upload bahan Google Flow tidak ditemukan.');
    if (materialToggle.getAttribute('aria-expanded') !== 'true') materialToggle.click();
    let clickedFileInput = null;
    const suppressFileChooser = (event) => {
      const fileInput = event.composedPath().find(
        (element) => element instanceof HTMLInputElement && element.type === 'file',
      );
      if (fileInput) {
        clickedFileInput = fileInput;
        event.preventDefault();
      }
    };
    document.addEventListener('click', suppressFileChooser, true);
    try {
      await clickButton(/Upload media/i);
    } finally {
      document.removeEventListener('click', suppressFileChooser, true);
    }

    const input = await waitFor(
      () => clickedFileInput || queryAll('input[type="file"]')
        .find((element) => !element.disabled),
      10000,
      'Input upload media Google Flow tidak ditemukan.',
    );
    const response = await fetch(message.referenceUrl, { cache: 'no-store' });
    if (!response.ok) throw new Error(`Gagal mengambil media referensi (${response.status}).`);
    const blob = await response.blob();
    const file = new File([blob], message.referenceName, {
      type: message.referenceMimeType || blob.type || 'application/octet-stream',
    });
    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));

    const option = await waitFor(
      () => queryAll('[role="option"]')
        .find((element) => isVisible(element)
          && normalize(element.innerText || element.textContent).includes(message.referenceName)),
      message.referenceMimeType.startsWith('video/') ? 90000 : 30000,
      'Media referensi tidak muncul di daftar upload Google Flow.',
    );
    const source = option.querySelector('img')?.src || '';
    const uploadedId = mediaId(source);
    option.click();

    const addToPrompt = await waitFor(
      () => queryAll('button,[role="button"]')
        .find((element) => isVisible(element)
          && /Tambahkan ke perintah/i.test(accessibleName(element))
          && !element.disabled),
      5 * 60 * 1000,
      'Google Flow tidak menyediakan tombol untuk menempelkan media referensi ke prompt.',
    );
    addToPrompt.click();

    await waitFor(() => {
      const promptBox = queryAll('flow-base-prompt-box');
      const promptImages = promptBox.flatMap((element) => queryAll('img', element));
      const attached = promptImages
        .some((image) => uploadedId && mediaId(image.currentSrc || image.src) === uploadedId);
      const videoChip = promptImages.find(
        (image) => image.alt === 'Gambar bahan video' && isVisible(image),
      );
      return attached || isVisible(videoChip);
    }, 30000, 'Media referensi tidak terpasang pada prompt Google Flow.');
  }

  function generatedMedia(message, baseline) {
    if (message.mediaType === 'video') {
      const thumbnails = queryAll(
        'img[alt="Thumbnail video yang dihasilkan"]',
      ).filter(isVisible);
      return thumbnails.length > baseline ? thumbnails[thumbnails.length - 1] : null;
    }
    return queryAll('img[data-media-id]')
      .filter((image) => isVisible(image)
        && !baseline.has(image.getAttribute('data-media-id')))
      .at(-1) || null;
  }

  async function generate(message) {
    await configureGeneration(message);
    await uploadReference(message);
    const baseline = message.mediaType === 'video'
      ? queryAll('img[alt="Thumbnail video yang dihasilkan"]').length
      : new Set(queryAll('img[data-media-id]')
        .map((image) => image.getAttribute('data-media-id')));

    await clickButton(/Mulai pembuatan/i);
    const accepted = await chrome.runtime.sendMessage({
      type: 'FLOW_GENERATE_ACCEPTED',
      requestId: message.requestId,
      detail: `Prompt diterima Google Flow (${message.mediaType}, ${message.model}).`,
    });
    if (!accepted?.ok) throw new Error('AMB tidak menerima konfirmasi prompt Google Flow.');

    const result = await waitFor(
      () => generatedMedia(message, baseline),
      FLOW_TIMEOUT_MS,
      `Google Flow belum menampilkan hasil ${message.mediaType} setelah 15 menit.`,
    );
    result.click();
    await clickButton(/Download media/i, 15000);

    const menuItems = await waitFor(
      () => queryAll('[role="menuitem"]').filter(isVisible),
      10000,
      'Menu unduhan Google Flow tidak muncul.',
    );
    let downloadOption;
    if (message.mediaType === 'video') {
      downloadOption = menuItems.find((item) => new RegExp(
        `\\b${message.resolution}\\b`,
        'i',
      ).test(accessibleName(item)));
    } else {
      downloadOption = menuItems.find((item) => /ukuran asli|original/i.test(accessibleName(item)))
        || menuItems.find((item) => /\b2k\b/i.test(accessibleName(item)))
        || menuItems.find((item) => /\b1k\b/i.test(accessibleName(item)))
        || (menuItems.length === 1 ? menuItems[0] : null);
    }
    if (!downloadOption) throw new Error('Pilihan resolusi unduhan Google Flow tidak ditemukan.');
    const downloadReady = await chrome.runtime.sendMessage({
      type: 'FLOW_DOWNLOAD_READY',
      requestId: message.requestId,
    });
    if (!downloadReady?.ok) throw new Error('AMB tidak siap menerima unduhan Google Flow.');
    downloadOption.click();
    return { status: 'download_started' };
  }

  if (location.hostname !== 'flow.google.com') return;
  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === 'AMB_FLOW_CONTENT_READY') {
      sendResponse({ ready: true });
      return false;
    }
    if (message?.type !== 'GENERATE_FLOW') return false;
    generate(message)
      .then(sendResponse)
      .catch((error) => sendResponse({
        status: 'error',
        detail: error instanceof Error ? error.message : 'Generate Google Flow gagal.',
      }));
    return true;
  });
})();
