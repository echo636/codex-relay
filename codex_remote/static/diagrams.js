'use strict';
(() => {
  let frame = null, ready = null, resolveReady, rejectReady, readyTimer, serial = 0, epoch = 0;
  let queue = Promise.resolve();
  const requests = new Map(), cache = new Map(), pending = new Map();
  const theme = () => document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
  function resetFrame() {
    clearTimeout(readyTimer);
    if (rejectReady) rejectReady(new Error(I18n.t("Diagram renderer closed.")));
    resolveReady = rejectReady = null;
    if (frame) frame.remove(); frame = ready = null;
    for (const job of requests.values()) { clearTimeout(job.timer); job.reject(new Error(I18n.t("Diagram rendering cancelled."))); }
    requests.clear();
  }
  function renderer() {
    if (ready) return ready;
    ready = new Promise((resolve, reject) => { resolveReady = resolve; rejectReady = reject; });
    frame = document.createElement('iframe'); frame.className = 'mermaid-renderer';
    frame.setAttribute('sandbox', 'allow-scripts'); frame.setAttribute('aria-hidden', 'true'); frame.tabIndex = -1;
    frame.title = I18n.t("Diagram renderer"); frame.src = '/mermaid-frame.html'; document.body.append(frame);
    readyTimer = setTimeout(resetFrame, 30000);
    return ready;
  }
  window.addEventListener('message', event => {
    if (!frame || event.source !== frame.contentWindow || !event.data) return;
    const data = event.data;
    if (data.type === 'mermaid-ready') {
      clearTimeout(readyTimer); if (resolveReady) resolveReady(); resolveReady = rejectReady = null;
    } else if (data.type === 'mermaid-result' && requests.has(data.id)) {
      const job = requests.get(data.id); requests.delete(data.id); clearTimeout(job.timer);
      if (data.error || typeof data.svg !== 'string') job.reject(new Error(data.error || I18n.t("Invalid diagram output.")));
      else job.resolve('data:image/svg+xml;charset=utf-8,' + encodeURIComponent(data.svg));
    }
  });
  function imageFor(source, color) {
    const key = color + '\0' + source, generation = epoch;
    if (cache.has(key)) return Promise.resolve(cache.get(key));
    if (pending.has(key)) return pending.get(key);
    const work = queue.then(async () => {
      if (generation !== epoch) throw new Error(I18n.t("Diagram rendering cancelled."));
      await renderer();
      if (generation !== epoch) throw new Error(I18n.t("Diagram rendering cancelled."));
      const id = ++serial;
      return new Promise((resolve, reject) => {
        requests.set(id, {resolve, reject, timer: setTimeout(() => { requests.delete(id); reject(new Error(I18n.t("Diagram rendering timed out. View the source."))); resetFrame(); }, 20000)});
        frame.contentWindow.postMessage({type: 'mermaid-render', id, source, theme: color}, '*');
      });
    }).then(url => {
      if (generation === epoch) { if (cache.size >= 32) cache.delete(cache.keys().next().value); cache.set(key, url); }
      return url;
    }).finally(() => { if (pending.get(key) === work) pending.delete(key); });
    pending.set(key, work); queue = work.catch(() => {}); return work;
  }
  async function draw(block) {
    if (!block.isConnected || block.dataset.complete !== 'true') return;
    const color = theme(), source = block.querySelector('code').textContent;
    if (block.dataset.theme === color) return;
    block.dataset.theme = color;
    const preview = block.querySelector('.mermaid-preview');
    try {
      const url = await imageFor(source, color);
      if (!block.isConnected || block.dataset.theme !== color) return;
      const image = document.createElement('img'); image.className = 'mermaid-image'; image.src = url; image.alt = I18n.t("Mermaid diagram");
      image.onload = () => { if (typeof scheduleTail === 'function') scheduleTail(); };
      image.onerror = () => { preview.textContent = I18n.t("Diagram display failed. View the source."); block.querySelector('details').open = true; };
      preview.replaceChildren(image); block.dataset.status = 'rendered';
      const toolbar = block.querySelector('.mermaid-tools');
      if (!toolbar.querySelector('.diagram-zoom')) {
        const zoom = document.createElement('button'); zoom.type = 'button'; zoom.className = 'diagram-zoom'; zoom.textContent = I18n.t("Zoom");
        zoom.onclick = () => { const expanded = block.classList.toggle('mermaid-expanded'); zoom.textContent = expanded ? I18n.t("Fit width") : I18n.t("Zoom"); };
        toolbar.append(zoom);
      }
    } catch (error) {
      if (!block.isConnected || block.dataset.theme !== color) return;
      preview.textContent = error.message; block.querySelector('details').open = true; block.dataset.status = 'error';
    }
  }
  const observer = new MutationObserver(() => document.querySelectorAll('.mermaid-block').forEach(draw));
  observer.observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});
  window.diagramRenderer = {
    render(parent) { queueMicrotask(() => parent.querySelectorAll('.mermaid-block').forEach(draw)); },
    clear() { epoch++; cache.clear(); pending.clear(); resetFrame(); queue = Promise.resolve(); }
  };
})();
