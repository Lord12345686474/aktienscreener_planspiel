"""
Planspiel Börse – lokale Webseite mit "Analyse starten"-Knopf.
Start:  python app.py   (öffnet automatisch http://127.0.0.1:8765 im Browser)
Die Analyse-Logik steckt in main.py. Alle Einstellungen stehen oben in main.py.
"""
import contextlib
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import main as engine

PORT = 8765

STATE = {"laeuft": False, "fertig": False, "fehler": "", "log": [], "karten": "", "meta": ""}
LOCK = threading.Lock()


class LogSchreiber:
    """Fängt alle print()-Ausgaben der Analyse ab und zeigt sie auf der Webseite."""
    def write(self, text):
        for zeile in text.splitlines():
            if zeile.strip():
                STATE["log"].append(zeile.rstrip())

    def flush(self):
        pass


def analyse_lauf():
    try:
        with contextlib.redirect_stdout(LogSchreiber()):
            treffer, namen, letzte_daten = engine.analysiere()
            engine.schreibe_webseite(treffer, namen, letzte_daten)
        STATE["karten"] = engine.baue_karten(treffer, namen)
        STATE["meta"] = engine.meta_text(letzte_daten)
        STATE["fertig"] = True
    except Exception as e:
        STATE["fehler"] = f"{type(e).__name__}: {e}"
    finally:
        STATE["laeuft"] = False


SEITE = """<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Planspiel Börse – Aktien-Filter</title>
<style>{{CSS}}
.knopf{background:var(--accent);color:#fff;border:0;border-radius:10px;padding:12px 22px;font-size:1rem;font-weight:600;cursor:pointer}
.knopf:disabled{opacity:.55;cursor:wait}
#log{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;margin:16px 0;font-size:.85rem;white-space:pre-wrap;max-height:220px;overflow:auto}
.fehler{color:var(--accent);font-weight:600;margin:12px 0}
</style></head><body><main>
<h1>Planspiel Börse – Aktien-Filter</h1>
<div class="meta" id="meta">Noch keine Analyse in dieser Sitzung.</div>
<button class="knopf" id="knopf">Analyse starten</button>
<div class="fehler" id="fehler" hidden></div>
<pre id="log" hidden></pre>
<div id="ergebnis" style="margin-top:20px"></div>
<footer>Keine Anlageberatung und keine Garantie für steigende Kurse. Automatische Auswertung nach festgelegten Kriterien (RSI, MACD, KGV, EPS) für das Planspiel Börse. Der erste Lauf dauert länger, weil die Ticker zugeordnet werden.</footer>
</main>
<script>
const knopf = document.getElementById("knopf"), logEl = document.getElementById("log"),
      fehlerEl = document.getElementById("fehler"), metaEl = document.getElementById("meta"),
      ergebnisEl = document.getElementById("ergebnis");
async function poll() {
  let s;
  try { s = await (await fetch("/status")).json(); }
  catch (e) { fehlerEl.hidden = false; fehlerEl.textContent = "Verbindung zum Programm verloren. Läuft das schwarze Fenster noch?"; return; }
  knopf.disabled = s.laeuft;
  knopf.textContent = s.laeuft ? "Analyse läuft …" : "Analyse starten";
  logEl.hidden = s.log.length === 0;
  logEl.textContent = s.log.slice(-12).join("\\n");
  logEl.scrollTop = logEl.scrollHeight;
  fehlerEl.hidden = !s.fehler; fehlerEl.textContent = s.fehler;
  if (s.fertig) { metaEl.textContent = s.meta; ergebnisEl.innerHTML = s.karten; }
  if (s.laeuft) setTimeout(poll, 1500);
}
knopf.onclick = async () => { await fetch("/start", {method: "POST"}); poll(); };
poll();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keine Server-Meldungen im schwarzen Fenster

    def antworte(self, daten, typ):
        self.send_response(200)
        self.send_header("Content-Type", typ + "; charset=utf-8")
        self.send_header("Content-Length", str(len(daten)))
        self.end_headers()
        self.wfile.write(daten)

    def do_GET(self):
        if self.path == "/status":
            self.antworte(json.dumps(STATE).encode("utf-8"), "application/json")
        elif self.path == "/":
            self.antworte(SEITE.replace("{{CSS}}", engine.CSS).encode("utf-8"), "text/html")
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/start":
            with LOCK:
                if not STATE["laeuft"]:
                    STATE.update(laeuft=True, fertig=False, fehler="", log=[], karten="", meta="")
                    threading.Thread(target=analyse_lauf, daemon=True).start()
            self.antworte(b"{}", "application/json")
        else:
            self.send_error(404)


def main():
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"Port {PORT} ist schon belegt. Läuft das Programm vielleicht schon in einem anderen Fenster?")
        return
    url = f"http://127.0.0.1:{PORT}"
    print(f"Webseite läuft unter {url}")
    print("Dieses Fenster offen lassen. Zum Beenden: Strg+C oder Fenster schließen.")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Beendet.")


if __name__ == "__main__":
    main()
