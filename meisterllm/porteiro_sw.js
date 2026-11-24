// Meister Chat: guarda só a página e os ícones, para a app abrir depressa e mostrar-se mesmo sem rede.
// Os pedidos aos modelos (/v1/...) e à voz nunca passam por aqui nem ficam guardados.
const CACHE = 'meister-chat-1';
const SHELL = ['/chat', '/manifest.webmanifest', '/icon-192.png', '/icon-512.png'];
self.addEventListener('install', event => { event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== location.origin || !SHELL.includes(url.pathname)) return;
  // primeiro a rede (a página atualiza-se sozinha); sem rede, a cópia guardada
  event.respondWith(fetch(event.request).then(answer => {
    if (answer.ok) { const copy = answer.clone(); caches.open(CACHE).then(cache => cache.put(event.request, copy)); }
    return answer;
  }).catch(() => caches.match(event.request)));
});
