const CACHE_PREFIX = "chief-shell";
const CACHE_VERSION = "v1";
const SHELL_CACHE = `${CACHE_PREFIX}-${CACHE_VERSION}`;
const SHELL_FILES = [
  "/offline.html",
  "/manifest.webmanifest",
  "/icons/chief-192.svg",
  "/icons/chief-512.svg",
];
const PRIVATE_PATH_PREFIXES = [
  "/audit",
  "/assumptions",
  "/briefing",
  "/business",
  "/chat",
  "/dashboard",
  "/decisions",
  "/events",
  "/foresight",
  "/goals",
  "/health",
  "/kpis",
  "/memory",
  "/notifications",
  "/plans",
  "/portfolio",
  "/ready",
  "/runs",
  "/schedules",
  "/sessions",
  "/signals",
  "/system",
  "/tasks",
  "/tools",
];
const STATIC_DESTINATIONS = new Set(["font", "image", "manifest", "script", "style"]);
const STATIC_FILE_PATHS = new Set([
  "/manifest.webmanifest",
  "/icons/chief-192.svg",
  "/icons/chief-512.svg",
]);

function isPrivatePath(pathname) {
  return PRIVATE_PATH_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

function isStaticShellPath(pathname) {
  return pathname.startsWith("/assets/") || STATIC_FILE_PATHS.has(pathname);
}

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_FILES)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith(CACHE_PREFIX) && key !== SHELL_CACHE)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin || isPrivatePath(url.pathname)) return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(async () => {
        const cache = await caches.open(SHELL_CACHE);
        return (await cache.match("/offline.html")) ?? Response.error();
      }),
    );
    return;
  }

  if (!STATIC_DESTINATIONS.has(request.destination) || !isStaticShellPath(url.pathname)) return;
  event.respondWith(
    caches.match(request).then(async (cached) => {
      if (cached) return cached;
      const response = await fetch(request);
      if (response.ok && response.type === "basic") {
        const cache = await caches.open(SHELL_CACHE);
        await cache.put(request, response.clone());
      }
      return response;
    }),
  );
});
