// Cloudflare-Worker: reicht die Live-Daten des offiziellen nuScore-Tickers an die App weiter.
// Erlaubt sind nur die zwei Abfragen, die auch der offizielle Ticker stellt.
// Jede Antwort wird 20 Sekunden zwischengespeichert, damit nuLiga kaum Last hat.

// Adresse deiner App (ohne Pfad). Andere Seiten bekommen keine Daten.
const ERLAUBT = "https://hoernsheim.github.io";

const QUELLE = "https://hbde-live.liga.nu/nuScoreLiveRestBackend/api/1/";
const WEGE = [/^meetings\/\d+\/time\/\d+$/, /^events\/\d+\/versions\/[\w-]+$/];

export default {
  async fetch(request) {
    const kopf = {
      "Access-Control-Allow-Origin": ERLAUBT,
      "Access-Control-Allow-Methods": "GET",
      "Vary": "Origin",
    };
    if (request.method === "OPTIONS") return new Response(null, { headers: kopf });
    if (request.method !== "GET") return new Response("Nicht erlaubt", { status: 405, headers: kopf });

    const weg = new URL(request.url).pathname.replace(/^\/+/, "");
    if (!WEGE.some(r => r.test(weg))) return new Response("Unbekannt", { status: 404, headers: kopf });

    // Zeitstempel auf 20 Sekunden runden, damit alle Nutzer dieselbe zwischengespeicherte Antwort bekommen
    const ziel = QUELLE + weg.replace(/\/time\/(\d+)$/, (_, t) => "/time/" + Math.floor(+t / 20) * 20);
    const antwort = await fetch(ziel, { cf: { cacheTtl: 20, cacheEverything: true } });
    return new Response(antwort.body, {
      status: antwort.status,
      headers: { ...kopf, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "public, max-age=20" },
    });
  },
};
