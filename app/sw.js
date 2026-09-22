/* Previsioni - funzionamento offline.
 * L'interfaccia viene salvata e servita subito; i dati (app.json) si
 * chiedono sempre prima alla rete, e solo senza rete si usa l'ultima
 * copia salvata. Quando si cambiano i file dell'app si alza VERSIONE.
 */
const VERSIONE = "previsioni-2";
const GUSCIO = ["./", "./index.html", "./stile.css", "./app.js",
                "./manifest.webmanifest", "./icona-180.png",
                "./icona-192.png", "./icona-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSIONE).then((c) => c.addAll(GUSCIO)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((chiavi) =>
    Promise.all(chiavi.filter((k) => k !== VERSIONE).map((k) => caches.delete(k)))
  ).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;

  // i dati: prima la rete, e se va a buon fine se ne salva una copia
  if (url.pathname.endsWith("/app.json")) {
    e.respondWith(
      fetch(e.request).then((r) => {
        const copia = r.clone();
        caches.open(VERSIONE).then((c) => c.put("app.json", copia));
        return r;
      }).catch(() => caches.match("app.json"))
    );
    return;
  }

  // l'interfaccia: subito dalla copia salvata, aggiornata in background
  e.respondWith(
    caches.match(e.request).then((salvata) => {
      const rete = fetch(e.request).then((r) => {
        if (r.ok) { const copia = r.clone(); caches.open(VERSIONE).then((c) => c.put(e.request, copia)); }
        return r;
      }).catch(() => salvata);
      return salvata || rete;
    })
  );
});
