'use strict';
const CACHE = 'codex-remote-shell-v20';
const FONT_NAMES = ['AMS-Regular', 'Caligraphic-Bold', 'Caligraphic-Regular', 'Fraktur-Bold', 'Fraktur-Regular', 'Main-Bold', 'Main-BoldItalic', 'Main-Italic', 'Main-Regular', 'Math-BoldItalic', 'Math-Italic', 'SansSerif-Bold', 'SansSerif-Italic', 'SansSerif-Regular', 'Script-Regular', 'Size1-Regular', 'Size2-Regular', 'Size3-Regular', 'Size4-Regular', 'Typewriter-Regular'];
const SHELL = ['/', '/i18n.js', '/locales/zh-CN.js', '/app.js', '/activity.js', '/theme.js', '/features.js', '/commands.js', '/commands.css', '/markdown.js', '/style.css', '/manifest.webmanifest', '/icon.svg', '/icon-192.png', '/icon-512.png',
  '/vendor/markdown-it/markdown-it.min.js', '/vendor/katex/katex.min.js', '/vendor/katex/katex.min.css', '/vendor/highlight/highlight.min.js',
  '/diagrams.js', '/mermaid-frame.html', '/mermaid-frame.js',
  ...FONT_NAMES.map(name => '/vendor/katex/fonts/KaTeX_' + name + '.woff2')];
const OPTIONAL = ['/vendor/mermaid/mermaid.min.js'];
self.addEventListener('install', event => { event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL))); });
self.addEventListener('activate', event => { event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('codex-remote-shell-') && key !== CACHE).map(key => caches.delete(key))))); });
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  // Chat history, tokens, API responses, and mutations are never cached or queued offline.
  if (event.request.method === 'GET' && url.origin === self.location.origin && OPTIONAL.includes(url.pathname) && !url.search) {
    event.respondWith(caches.open(CACHE).then(async cache => {
      const cached = await cache.match(event.request); if (cached) return cached;
      const response = await fetch(event.request); if (response.ok) await cache.put(event.request, response.clone()); return response;
    })); return;
  }
  if (event.request.method !== 'GET' || url.origin !== self.location.origin || !SHELL.includes(url.pathname) || url.search) return;
  event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
});
self.addEventListener('push', event => {
  let data = {}; try { data = event.data.json(); } catch (_) {}
  event.waitUntil(self.registration.showNotification(data.title || "Codex workspace", {
    body: data.body || "Your task has an update.", icon: '/icon-192.png', badge: '/icon-192.png',
    tag: 'codex-' + (data.threadId || 'status'), data: {threadId: data.threadId}
  }));
});
self.addEventListener('notificationclick', event => {
  event.notification.close(); const tid = event.notification.data && event.notification.data.threadId;
  const target = new URL('/' + (tid ? '?thread=' + encodeURIComponent(tid) : ''), self.location.origin).href;
  event.waitUntil(clients.matchAll({type: 'window', includeUncontrolled: true}).then(async windows => {
    for (const client of windows) if (new URL(client.url).origin === self.location.origin) { await client.navigate(target); return client.focus(); }
    return clients.openWindow(target);
  }));
});
