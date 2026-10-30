// Offline-Betrieb: alles Nötige wird beim ersten Besuch gespeichert, danach lädt die Seite auch ohne Empfang.
// Bei Änderungen an der Seite VERSION erhöhen, damit Handys die neue Fassung holen.
const VERSION = 'v1';
const CACHE = `adler-ergebnis-${VERSION}`;
const CORE = [
  './', 'index.html', 'app.js', 'manifest.webmanifest', 'teams.json',
  'img/adler.png', 'img/eagle.png', 'icon-192.png', 'icon-512.png', 'apple-touch-icon.png',
  'fonts/BarlowCondensed-ExtraBoldItalic.ttf', 'fonts/BarlowCondensed-BoldItalic.ttf',
  'fonts/BarlowCondensed-MediumItalic.ttf',
];

self.addEventListener('install', (e) => {
  e.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    await Promise.all(CORE.map((u) => cache.add(u).catch(() => null)));
    try {
      const teams = await (await fetch('teams.json', { cache: 'no-cache' })).json();
      await Promise.all((teams.teams || []).filter((t) => t.crest).map((t) => cache.add(t.crest).catch(() => null)));
    } catch (err) { /* ohne Teamliste kein Vorab-Laden der Wappen */ }
    self.skipWaiting();
  })());
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

// Erst Netz (aktuell), bei fehlendem Empfang aus dem Speicher.
self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET' || new URL(req.url).origin !== location.origin) return;
  e.respondWith((async () => {
    const cache = await caches.open(CACHE);
    try {
      const res = await fetch(req);
      if (res.ok) cache.put(req, res.clone());
      return res;
    } catch (err) {
      return (await cache.match(req, { ignoreSearch: true })) || (await cache.match('index.html'));
    }
  })());
});
