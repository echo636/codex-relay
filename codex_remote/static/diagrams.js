'use strict';
(() => {
  let frame = null, ready = null, resolveReady, rejectReady, readyTimer, serial = 0, epoch = 0;
  let queue = Promise.resolve(), loadError = null;
  const requests = new Map(), cache = new Map(), pending = new Map();
  const theme = () => document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
  function resetFrame(reason) {
    clearTimeout(readyTimer);
    if (rejectReady) rejectReady(reason || new Error(I18n.t("Diagram renderer closed.")));
    resolveReady = rejectReady = null;
    if (frame) frame.remove(); frame = ready = null;
    for (const job of requests.values()) { clearTimeout(job.timer); job.reject(new Error(I18n.t("Diagram rendering cancelled."))); }
    requests.clear();
  }
  function renderer() {
    if (loadError) return Promise.reject(loadError);
    if (ready) return ready;
    ready = new Promise((resolve, reject) => { resolveReady = resolve; rejectReady = reject; });
    frame = document.createElement('iframe'); frame.className = 'mermaid-renderer';
    frame.setAttribute('sandbox', 'allow-scripts'); frame.setAttribute('aria-hidden', 'true'); frame.tabIndex = -1;
    frame.title = I18n.t("Diagram renderer"); frame.src = '/mermaid-frame.html'; document.body.append(frame);
    readyTimer = setTimeout(() => {
      loadError = new Error(I18n.t('Diagram renderer timed out while loading. Retry or reload the page.'));
      resetFrame(loadError);
    }, 90000);
    return ready;
  }
  window.addEventListener('message', event => {
    if (!frame || event.source !== frame.contentWindow || !event.data) return;
    const data = event.data;
    if (data.type === 'mermaid-ready') {
      clearTimeout(readyTimer); if (resolveReady) resolveReady(); resolveReady = rejectReady = null;
    } else if (data.type === 'mermaid-load-error') {
      loadError = new Error(I18n.t('Diagram library could not start. Reload the page or update your browser.'));
      resetFrame(loadError);
    } else if (data.type === 'mermaid-result' && requests.has(data.id)) {
      const job = requests.get(data.id); requests.delete(data.id); clearTimeout(job.timer);
      if (data.error || typeof data.svg !== 'string') job.reject(new Error(data.error || I18n.t("Invalid diagram output.")));
      else job.resolve('data:image/svg+xml;charset=utf-8,' + encodeURIComponent(data.svg));
    }
  });
  function imageFor(source, color, block) {
    const key = color + '\0' + source, generation = epoch;
    if (cache.has(key)) return Promise.resolve(cache.get(key));
    if (pending.has(key)) { const job = pending.get(key); job.blocks.add(block); return job.work; }
    const blocks = new Set([block]);
    const work = queue.then(async () => {
      if (generation !== epoch) throw new Error(I18n.t("Diagram rendering cancelled."));
      // Streaming and history refreshes can replace nodes before their turn.
      // Do not make current diagrams wait for discarded transcript versions.
      if (![...blocks].some(node => node.isConnected)) return null;
      await renderer();
      if (generation !== epoch) throw new Error(I18n.t("Diagram rendering cancelled."));
      const id = ++serial;
      return new Promise((resolve, reject) => {
        requests.set(id, {resolve, reject, timer: setTimeout(() => { requests.delete(id); reject(new Error(I18n.t("Diagram rendering timed out. View the source."))); resetFrame(); }, 20000)});
        frame.contentWindow.postMessage({type: 'mermaid-render', id, source, theme: color}, '*');
      });
    }).then(url => {
      if (url && generation === epoch) { if (cache.size >= 32) cache.delete(cache.keys().next().value); cache.set(key, url); }
      return url;
    }).finally(() => { if (pending.get(key)?.work === work) pending.delete(key); });
    pending.set(key, {work, blocks}); queue = work.catch(() => {}); return work;
  }
  async function draw(block) {
    if (!block.isConnected || block.dataset.complete !== 'true') return;
    const color = theme(), source = block.querySelector('code').textContent;
    if (block.dataset.theme === color) return;
    block.dataset.theme = color;
    const preview = block.querySelector('.mermaid-preview'), toolbar = block.querySelector('.mermaid-tools');
    toolbar.querySelector('.diagram-retry')?.remove();
    block.dataset.status = 'loading'; preview.textContent = I18n.t('Rendering diagram…');
    function failed(message) {
      if (!block.isConnected || block.dataset.theme !== color) return;
      preview.textContent = message; block.querySelector('details').open = true; block.dataset.status = 'error';
      if (toolbar.querySelector('.diagram-retry')) return;
      const retry = document.createElement('button'); retry.type = 'button'; retry.className = 'diagram-retry'; retry.textContent = I18n.t('Retry');
      retry.onclick = () => { loadError = null; cache.delete(color + '\0' + source); delete block.dataset.theme; draw(block); };
      toolbar.append(retry);
    }
    try {
      const url = await imageFor(source, color, block);
      if (!url || !block.isConnected || block.dataset.theme !== color) return;
      const image = document.createElement('img'); image.className = 'mermaid-image'; image.src = url; image.alt = I18n.t("Mermaid diagram");
      image.onload = () => { if (typeof scheduleTail === 'function') scheduleTail(); };
      image.onerror = () => failed(I18n.t("Diagram display failed. View the source."));
      preview.replaceChildren(image); block.dataset.status = 'rendered';
      if (!toolbar.querySelector('.diagram-zoom')) {
        const zoom = document.createElement('button'); zoom.type = 'button'; zoom.className = 'diagram-zoom'; zoom.textContent = I18n.t("Zoom");
        zoom.onclick = () => { const expanded = block.classList.toggle('mermaid-expanded'); zoom.textContent = expanded ? I18n.t("Fit width") : I18n.t("Zoom"); };
        toolbar.append(zoom);
      }
    } catch (error) {
      if (!block.isConnected || block.dataset.theme !== color) return;
      failed(error.message);
    }
  }
  const observer = new MutationObserver(() => document.querySelectorAll('.mermaid-block').forEach(draw));
  observer.observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});
  window.diagramRenderer = {
    render(parent) { queueMicrotask(() => parent.querySelectorAll('.mermaid-block').forEach(draw)); },
    clear() { epoch++; cache.clear(); pending.clear(); loadError = null; resetFrame(); queue = Promise.resolve(); }
  };
})();
