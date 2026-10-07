// App shell (kecil, sering berubah) dan aset statis (model AI ±7MB & library bersematan versi) dipisah:
// menaikkan CACHE_NAME saat rilis tidak lagi memaksa seluruh HP mengunduh ulang model AI.
const CACHE_NAME = 'attendance-pwa-v49';
const STATIC_CACHE = 'attendance-static-v1'; // naikkan nomor ini HANYA bila berkas model/library di STATIC_ASSETS diganti isinya
const NETWORK_TIMEOUT_MS = 2500; // jaringan lambat: pakai salinan cache bila server belum menjawab dalam waktu ini

const APP_SHELL = [
  './',
  './index.html',
  './pwa_app.js'
];

const STATIC_ASSETS = [
  'https://cdn.jsdelivr.net/npm/jsqr@1.4.0/dist/jsQR.js',
  'https://cdn.jsdelivr.net/npm/@vladmandic/face-api@1.7.15/dist/face-api.js',
  'https://cdn.jsdelivr.net/npm/html5-qrcode@2.3.8/html5-qrcode.min.js',
  './models/tiny_face_detector_model-weights_manifest.json',
  './models/tiny_face_detector_model.bin',
  './models/face_landmark_68_model-weights_manifest.json',
  './models/face_landmark_68_model.bin',
  './models/face_recognition_model-weights_manifest.json',
  './models/face_recognition_model.bin'
];

function isStaticUrl(url) {
  return url.includes('/models/') || url.startsWith('https://cdn.jsdelivr.net/');
}

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    console.log('[Service Worker] Caching app shell and AI face models');

    const shell = await caches.open(CACHE_NAME);
    await Promise.allSettled(
      APP_SHELL.map(url => shell.add(url).catch(err => console.warn('[Service Worker Cache Error]', url, err)))
    );

    const statics = await caches.open(STATIC_CACHE);
    await Promise.allSettled(
      STATIC_ASSETS.map(async (url) => {
        try {
          if (await statics.match(url)) return;
          // Pakai ulang salinan dari cache versi lama (mis. attendance-pwa-v48) sebelum mengunduh ulang lewat jaringan
          const existing = await caches.match(url);
          if (existing) {
            await statics.put(url, existing);
          } else {
            await statics.add(url);
          }
        } catch (err) {
          console.warn('[Service Worker Cache Error]', url, err);
        }
      })
    );
  })());
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keyList) => {
      return Promise.all(
        keyList.map((key) => {
          if (key !== CACHE_NAME && key !== STATIC_CACHE) {
            return caches.delete(key);
          }
        })
      );
    })
  );
  self.clients.claim();
});

// Network-First dengan batas waktu: versi terbaru bila jaringan responsif, salinan cache bila jaringan lambat/mati.
function networkFirstWithTimeout(request) {
  return new Promise((resolve) => {
    let settled = false;
    const settle = (response) => {
      if (settled || !response) return false;
      settled = true;
      resolve(response);
      return true;
    };

    const timer = setTimeout(async () => {
      // Tanpa salinan cache, tetap menunggu jaringan
      settle(await caches.match(request, { ignoreSearch: true }));
    }, NETWORK_TIMEOUT_MS);

    fetch(request).then((networkResponse) => {
      clearTimeout(timer);
      if (networkResponse && networkResponse.status === 200) {
        const responseToCache = networkResponse.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(request, responseToCache)).catch(() => {});
      }
      settle(networkResponse);
    }).catch(async () => {
      clearTimeout(timer);
      const cached = await caches.match(request, { ignoreSearch: true });
      if (!settle(cached) && !settled) {
        settled = true;
        resolve(Response.error());
      }
    });
  });
}

self.addEventListener('fetch', (event) => {
  const url = event.request.url;

  // Jangan intersepsi API request ke Google Apps Script / external API / non-GET
  if (
    event.request.method !== 'GET' ||
    url.includes('script.google.com') ||
    url.includes('script.googleusercontent.com')
  ) {
    return; // Serahkan langsung ke jaringan native browser
  }

  // Network-First untuk pwa_app.js & index.html agar pengguna selalu mendapat versi terbaru
  if (url.includes('pwa_app.js') || url.includes('index.html') || url.endsWith('/')) {
    event.respondWith(networkFirstWithTimeout(event.request));
    return;
  }

  event.respondWith(
    caches.match(event.request).then((cachedResponse) => {
      if (cachedResponse) {
        return cachedResponse;
      }
      return fetch(event.request).then((networkResponse) => {
        if (networkResponse.status === 200 && url.startsWith('http')) {
          const responseToCache = networkResponse.clone();
          caches.open(isStaticUrl(url) ? STATIC_CACHE : CACHE_NAME).then((cache) => {
            cache.put(event.request, responseToCache).catch(() => {});
          });
        }
        return networkResponse;
      }).catch(err => {
        console.warn('[Service Worker] Fetch failed:', err);
      });
    })
  );
});
