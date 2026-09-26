// Service worker: lets the game load and play with no network after the
// first visit. web/build.py stamps __BUILD_ID__ with the build's hash, so a
// new deploy changes this file, installs a fresh cache and drops the old.
//
// - The game's own files are cached per build (cache-first; updates arrive
//   through a new build of this file).
// - Pyodide's files come from a versioned CDN URL, so they never change and
//   are cached once, cache-first, surviving game updates. The page reports
//   which ones it loaded (see app.js), so even the very first visit -- which
//   loads Pyodide before this worker controls the page -- ends offline-ready.

const BUILD_ID = "__BUILD_ID__";
const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";
const SHELL_CACHE = `terminalgames-${BUILD_ID}`;
const PYODIDE_CACHE = "pyodide-v314.0.7";
const SHELL = ["./", "index.html", "style.css", "app.js", `terminalgames.zip?v=${BUILD_ID}`];

self.addEventListener("install", (event) => {
  // cache: "reload" skips the HTTP cache, so a new build never precaches a
  // stale file the browser kept from the previous one.
  const requests = SHELL.map((url) => new Request(url, { cache: "reload" }));
  event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll(requests)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => ![SHELL_CACHE, PYODIDE_CACHE].includes(k)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  if (request.url.startsWith(PYODIDE_URL)) {
    event.respondWith(cacheFirst(PYODIDE_CACHE, request));
  } else if (new URL(request.url).origin === self.location.origin) {
    event.respondWith(cacheFirst(SHELL_CACHE, request));
  }
});

async function cacheFirst(cacheName, request) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) cache.put(request, response.clone());
  return response;
}

// The page sends the Pyodide URLs it loaded; cache any that aren't yet, then
// tell it whether the game is now fully playable offline.
self.addEventListener("message", (event) => {
  const urls = (event.data && event.data.cachePyodide) || [];
  event.waitUntil(
    (async () => {
      const cache = await caches.open(PYODIDE_CACHE);
      let ok = true;
      for (const url of urls.filter((u) => u.startsWith(PYODIDE_URL))) {
        if (await cache.match(url)) continue;
        try {
          const response = await fetch(url);
          if (response.ok) await cache.put(url, response);
          else ok = false;
        } catch {
          ok = false;
        }
      }
      event.source.postMessage({ offlineReady: ok });
    })(),
  );
});
