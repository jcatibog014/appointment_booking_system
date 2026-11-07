// The service worker: what makes ShiroMoto Garage work like an app on a phone.
// - It keeps a copy of the look (CSS, scripts, fonts, icons) and an offline page.
// - Pages AND the look come from the network first, so a new style or script shows
//   at once. The kept copy is only used when there is no network.
// - It shows push notices, and opens the right page when one is tapped.
// Changing VERSION clears the kept copies on every phone.
const VERSION = "shiromoto-v2";
const KEEP = [
  "/offline",
  "/static/style.css",
  "/static/app.js",
  "/static/theme.js",
  "/static/icon.svg",
  "/static/icons/icon-192.png",
  "/static/fonts/barlow-condensed-600.woff2",
  "/static/fonts/barlow-condensed-700.woff2",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(VERSION).then((cache) => cache.addAll(KEEP)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(names.filter((name) => name !== VERSION).map((name) => caches.delete(name))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET" || new URL(request.url).origin !== self.location.origin) return;
  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => caches.match("/offline")));
    return;
  }
  if (new URL(request.url).pathname.startsWith("/static/")) {
    event.respondWith(
      fetch(request)
        .then((fresh) => {
          if (fresh.ok) {
            const copy = fresh.clone();
            caches.open(VERSION).then((cache) => cache.put(request, copy));
          }
          return fresh;
        })
        .catch(() => caches.match(request)),
    );
  }
});

self.addEventListener("push", (event) => {
  let notice = { title: "ShiroMoto Garage", body: "", url: "/" };
  try {
    notice = { ...notice, ...event.data.json() };
  } catch (error) {
    if (event.data) notice.body = event.data.text();
  }
  event.waitUntil(self.registration.showNotification(notice.title, {
    body: notice.body,
    icon: "/static/icons/icon-192.png",
    badge: "/static/icons/icon-192.png",
    data: { url: notice.url || "/" },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data && event.notification.data.url ? event.notification.data.url : "/";
  event.waitUntil(self.clients.matchAll({ type: "window" }).then((windows) => {
    const open = windows.find((client) => client.url === url);
    return open ? open.focus() : self.clients.openWindow(url);
  }));
});
