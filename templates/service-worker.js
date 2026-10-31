'use strict';

const CACHE_NAME = 'fitkeep-pwa-v3';
const APP_SHELL = [
    '/static/css/main.css',
    '/static/js/main.js',
    '/static/manifest.webmanifest',
    '/static/images/fitkeep-icon-192.png',
    '/static/images/fitkeep-icon-512.png',
    '/static/pwa-offline.html',
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(APP_SHELL))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys()
            .then(keys => Promise.all(
                keys
                    .filter(key => key.startsWith('fitkeep-pwa-') && key !== CACHE_NAME)
                    .map(key => caches.delete(key))
            ))
            .then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', event => {
    const request = event.request;
    const url = new URL(request.url);

    if (request.method !== 'GET' || url.origin !== self.location.origin) return;

    if (request.mode === 'navigate') {
        event.respondWith(
            fetch(request).catch(async () => {
                const cache = await caches.open(CACHE_NAME);
                const offlinePage = await cache.match('/static/pwa-offline.html');
                if (!offlinePage) throw new Error('FitKeep offline page is missing from the app cache.');
                return offlinePage;
            })
        );
        return;
    }

    if (url.pathname.startsWith('/static/')) {
        event.respondWith((async () => {
            const cached = await caches.match(request);
            if (cached) return cached;

            const response = await fetch(request);
            if (response.ok) {
                try {
                    const cache = await caches.open(CACHE_NAME);
                    await cache.put(request, response.clone());
                } catch (error) {
                    console.error('FitKeep could not cache a static asset:', error);
                }
            }
            return response;
        })());
    }
});
