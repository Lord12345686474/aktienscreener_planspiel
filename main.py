"""
Planspiel Börse – Aktien-Filter (Grundversion)
Lädt Aktienliste, berechnet RSI / MACD / KGV / EPS, vergibt einen Score (0-100)
und zeigt NUR Aktien, die Mindestscore + Mindestanzahl positiver Signale erreichen.

Keine Anlageberatung, keine Kursgarantie.
"""
import html
import os
import time
import webbrowser
from datetime import datetime

import numpy as np
import pandas as pd

# =============== EINSTELLUNGEN (hier leicht änderbar) ===============
AKTIENLISTE_URL = ""                    # optional: URL zu einer CSV mit Spalte "isin" oder "ticker" (und "name")
AKTIENLISTE_DATEI = "aktienliste_isin.csv"   # Liste mit Spalte "isin" ODER "ticker" (+ optional "name")
TICKER_CACHE_DATEI = "ticker_zuordnung.csv"  # Zuordnung ISIN -> Yahoo-Ticker (wird automatisch erstellt)
HTML_DATEI = "ergebnis.html"            # Ergebnis-Webseite (wird bei jedem Lauf neu erstellt)
BROWSER_OEFFNEN = True                  # Webseite nach der Analyse automatisch im Browser öffnen
HISTORIE = "1y"                         # Kursverlauf für die Indikatoren
BATCH_GROESSE = 100                     # Kurse werden in Blöcken geladen

GEWICHTE = {"RSI": 35, "MACD": 35, "KGV": 15, "EPS": 15}   # Summe = 100 empfohlen
MIN_SCORE = 65                          # Mindestscore (0-100)
MIN_POSITIVE_SIGNALE = 3                # so viele der 4 Kriterien müssen "positiv" sein
SIGNAL_SCHWELLE = 0.6                   # Teilscore (0-1), ab dem ein Kriterium als positiv zählt

RSI_PERIODE = 14
MACD_SCHNELL, MACD_LANGSAM, MACD_SIGNAL = 12, 26, 9
RUECKBLICK = 5                          # Anzahl letzter Perioden, die berücksichtigt werden

KGV_STUFEN = [(15, 1.0, "niedrig"), (25, 0.7, "moderat"), (40, 0.4, "erhöht")]  # sonst 0.1 "sehr hoch"
# ====================================================================


def num(x):
    try:
        v = float(x)
        return v if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


# ------------------------- 1. Aktienliste -------------------------
def isin_zu_ticker(isin):
    """Sucht bei Yahoo Finance den Ticker zu einer ISIN. None = nichts gefunden. Wirft bei Netzwerkfehlern."""
    import yfinance as yf
    for q in yf.Search(isin, max_results=5).quotes:
        if q.get("quoteType") == "EQUITY" and q.get("symbol"):
            return q["symbol"]
    return None


def speichere_cache(cache):
    pd.DataFrame({"isin": list(cache), "ticker": list(cache.values())}).to_csv(TICKER_CACHE_DATEI, index=False)


def ordne_tickers_zu(df):
    """Ergänzt Spalte 'ticker' anhand der ISIN. Ergebnisse werden zwischengespeichert (Datei ist von Hand korrigierbar)."""
    cache = {}
    if os.path.exists(TICKER_CACHE_DATEI):
        c = pd.read_csv(TICKER_CACHE_DATEI, dtype=str).fillna("")
        cache = dict(zip(c["isin"], c["ticker"]))
    fehlend = [i for i in df["isin"] if i not in cache]
    if fehlend:
        print(f"Ordne {len(fehlend)} ISINs per Yahoo-Suche zu (nur beim ersten Start, dauert einige Minuten) ...")
    fehler_in_folge = 0
    for n, isin in enumerate(fehlend, 1):
        try:
            cache[isin] = isin_zu_ticker(isin) or ""
            fehler_in_folge = 0
        except Exception as e:
            fehler_in_folge += 1
            print(f"  Warnung: Suche für {isin} fehlgeschlagen ({e}). Wird beim nächsten Start erneut versucht.")
            if fehler_in_folge >= 5:
                print("  Zu viele Fehler hintereinander (Internet oder Yahoo-Limit?). Bitte später erneut starten.")
                break
            continue
        time.sleep(0.2)
        if n % 25 == 0:
            speichere_cache(cache)
            print(f"  {n}/{len(fehlend)} zugeordnet ...")
    speichere_cache(cache)

    df = df.copy()
    df["ticker"] = df["isin"].map(cache).fillna("")
    ohne = df[df["ticker"] == ""]
    if len(ohne):
        print(f"{len(ohne)} von {len(df)} Aktien ohne Ticker (werden übersprungen). "
              f"Du kannst sie in {TICKER_CACHE_DATEI} von Hand eintragen.")
    return df[df["ticker"] != ""]


def lade_aktienliste():
    if AKTIENLISTE_URL:
        try:
            neu = pd.read_csv(AKTIENLISTE_URL)
            neu.to_csv(AKTIENLISTE_DATEI, index=False)
            print("Aktienliste von URL aktualisiert.")
        except Exception as e:
            print(f"Warnung: Download der Aktienliste fehlgeschlagen ({e}). Nutze lokale Datei.")
    df = pd.read_csv(AKTIENLISTE_DATEI, dtype=str)
    if "ticker" not in df.columns and "isin" in df.columns:
        df = ordne_tickers_zu(df)
    if "name" not in df.columns:
        df["name"] = df["ticker"]
    return df.dropna(subset=["ticker"]).drop_duplicates("ticker").reset_index(drop=True)


# ------------------------- 2. Daten abrufen -------------------------
def lade_kurse(tickers):
    """Gibt {ticker: Close-Series} zurück (nur Ticker mit Daten)."""
    import yfinance as yf
    ergebnis = {}
    for i in range(0, len(tickers), BATCH_GROESSE):
        block = tickers[i:i + BATCH_GROESSE]
        try:
            d = yf.download(block, period=HISTORIE, group_by="ticker",
                            auto_adjust=True, progress=False, threads=True)
        except Exception as e:
            print(f"Warnung: Kursabruf fehlgeschlagen für Block {i}: {e}")
            continue
        if d is None or d.empty:
            continue
        for tk in block:
            try:
                s = d[tk]["Close"] if isinstance(d.columns, pd.MultiIndex) else d["Close"]
                s = s.dropna()
                if len(s) >= 60:
                    ergebnis[tk] = s
            except KeyError:
                pass
    return ergebnis


def lade_fundamentals(tk):
    """Gibt (pe, forward_pe, eps_aktuell, eps_vergleich, eps_quelle) zurück; fehlende Werte = None."""
    import yfinance as yf
    t = yf.Ticker(tk)
    try:
        info = t.info or {}
    except Exception:
        info = {}
    pe, fpe = num(info.get("trailingPE")), num(info.get("forwardPE"))

    eps_aktuell, eps_vergleich, quelle = None, None, ""
    try:
        inc = t.income_stmt
        for zeile in ("Diluted EPS", "Basic EPS"):
            if inc is not None and zeile in inc.index:
                s = inc.loc[zeile].dropna().sort_index(ascending=False)
                if len(s) >= 2:
                    eps_aktuell, eps_vergleich, quelle = num(s.iloc[0]), num(s.iloc[1]), "Vorjahr"
                    break
    except Exception:
        pass
    if eps_aktuell is None:  # Fallback: aktuell vs. erwartetes EPS
        eps_aktuell = num(info.get("trailingEps"))
        eps_vergleich = num(info.get("forwardEps"))
        quelle = "Ausblick"
    return pe, fpe, eps_aktuell, eps_vergleich, quelle


# ------------------------- 3. RSI -------------------------
def berechne_rsi(close, n=RSI_PERIODE):
    d = close.diff()
    auf = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    ab = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + auf / ab)


def bewerte_rsi(rsi):
    r = rsi.dropna()
    if len(r) < RUECKBLICK + 1:
        return None, "nicht verfügbar"
    w = r.iloc[-RUECKBLICK:]
    jetzt, davor = w.iloc[-1], w.iloc[-2]
    steigend = jetzt > davor
    war_unter_30 = w.iloc[:-1].min() < 30
    verlauf = " → ".join(f"{x:.0f}" for x in w.iloc[-4:]) + (" ↑" if steigend else " ↓")

    if jetzt > 70:
        return 0.0, f"{verlauf} (überkauft, Warnsignal)"
    if war_unter_30 and jetzt >= 30 and steigend:
        return 1.0, f"{verlauf} (kreuzt 30 nach oben)"
    if (war_unter_30 or jetzt < 30) and steigend:
        return 0.8, f"{verlauf} (aus Überverkauft steigend)"
    if jetzt < 30:
        return 0.2, f"{verlauf} (überverkauft, fällt noch)"
    if steigend:
        return (0.5 if jetzt < 50 else 0.4), f"{verlauf} (steigend)"
    return 0.1, f"{verlauf} (fallend)"


# ------------------------- 4. MACD -------------------------
def berechne_macd(close):
    m = close.ewm(span=MACD_SCHNELL, adjust=False).mean() - close.ewm(span=MACD_LANGSAM, adjust=False).mean()
    s = m.ewm(span=MACD_SIGNAL, adjust=False).mean()
    return m, s


def bewerte_macd(m, s):
    h = (m - s).dropna()
    if len(h) < RUECKBLICK + 1:
        return None, "nicht verfügbar"
    hw = h.iloc[-RUECKBLICK:]
    bull = bool(((hw.shift(1) <= 0) & (hw > 0)).any())
    bear = bool(((hw.shift(1) >= 0) & (hw < 0)).any())
    jetzt = h.iloc[-1]
    unter_null = m.iloc[-1] < 0
    steigt = m.iloc[-1] > m.iloc[-2]

    if bull and jetzt > 0:
        if unter_null:
            return 1.0, "Bullisher Crossover unter der Nulllinie (frühes Erholungssignal)"
        return 0.7, "Bullisher Crossover über der Nulllinie"
    if bear and jetzt < 0:
        return 0.0, "Bearisher Crossover"
    if jetzt > 0 and steigt:
        return 0.5, "über Signallinie, MACD steigt"
    if jetzt < 0 and h.iloc[-1] > h.iloc[-2]:
        return 0.45, "verbessert sich (noch unter Signallinie)"
    if jetzt > 0:
        return 0.3, "über Signallinie, aber abflachend"
    return 0.1, "fällt"


# ------------------------- 5. KGV -------------------------
def bewerte_kgv(pe, fpe):
    basis = pe if pe is not None else fpe
    if basis is None:
        return None, "nicht verfügbar"
    if basis <= 0:
        return 0.0, f"negativ ({basis:.1f}, Verlust)"
    sub, stufe = 0.1, "sehr hoch"
    for grenze, wert, name in KGV_STUFEN:
        if basis < grenze:
            sub, stufe = wert, name
            break
    trend = ""
    if pe is not None and fpe is not None and fpe > 0:
        if fpe < pe * 0.95:
            sub, trend = min(1.0, sub + 0.2), ", sinkend (erwartet)"
        elif fpe > pe * 1.05:
            sub, trend = max(0.0, sub - 0.2), ", steigend (erwartet)"
    return sub, f"{basis:.1f} ({stufe}{trend})"


# ------------------------- 6. EPS -------------------------
def bewerte_eps(aktuell, vergleich, quelle):
    if aktuell is None:
        return None, "nicht verfügbar"
    if vergleich is None:
        return (0.4 if aktuell > 0 else 0.0), f"{aktuell:.2f} (Entwicklung nicht verfügbar)"
    steigend = vergleich < aktuell if quelle == "Vorjahr" else vergleich > aktuell
    pfeil = "steigend" if steigend else "fallend"
    if aktuell > 0 and steigend:
        return 1.0, f"{aktuell:.2f}, {pfeil} (positiv)"
    if aktuell > 0:
        return 0.3, f"{aktuell:.2f}, {pfeil} (positiv)"
    if steigend:
        return 0.3, f"{aktuell:.2f}, {pfeil} (noch negativ)"
    return 0.0, f"{aktuell:.2f}, {pfeil} (negativ)"


# ------------------------- 7. Score -------------------------
def score(teil):
    """teil = {"RSI": (sub, text), ...}. Fehlende Daten (None) zählen als 0 Punkte."""
    gesamt = sum(GEWICHTE.values())
    punkte = sum(GEWICHTE[k] * (v[0] or 0) for k, v in teil.items())
    return 100 * punkte / gesamt


def positive(teil):
    return [k for k, v in teil.items() if v[0] is not None and v[0] >= SIGNAL_SCHWELLE]


# ------------------------- Webseite -------------------------
CSS = """
:root{--bg:#f4f5f7;--card:#fff;--text:#1d2330;--muted:#667085;--line:#e3e6ec;--accent:#e30613;--pos:#0a7d3b}
@media (prefers-color-scheme:dark){:root{--bg:#14171d;--card:#1e232c;--text:#eceff4;--muted:#9aa4b5;--line:#2f3541;--accent:#ff4d5a;--pos:#4cd98a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 system-ui,Segoe UI,Arial,sans-serif}
main{max-width:760px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:1.6rem;margin:0 0 4px}.meta{color:var(--muted);font-size:.9rem;margin-bottom:20px}
.karte{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:16px}
.karte h2{font-size:1.15rem;margin:0 0 10px}.karte small{color:var(--muted);font-weight:400}
.score{position:relative;height:26px;background:var(--line);border-radius:13px;overflow:hidden;margin-bottom:12px}
.balken{height:100%;background:var(--accent)}.score span{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-weight:600;font-size:.9rem;color:#fff;text-shadow:0 0 3px rgba(0,0,0,.5)}
table{width:100%;border-collapse:collapse}th{text-align:left;width:70px;color:var(--muted);font-weight:600;padding:5px 8px 5px 0;vertical-align:top}
td{padding:5px 0}td.pos{color:var(--pos);font-weight:600}
.warum{margin:10px 0 0;font-size:.92rem;color:var(--muted)}
.leer{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px;text-align:center}
footer{color:var(--muted);font-size:.85rem;margin-top:24px}
"""

SEITE = """<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Planspiel Börse – Interessante Aktien</title>
<style>{{CSS}}</style></head><body><main>
<h1>Interessante Aktien</h1>
<div class="meta">{{META}}</div>
{{KARTEN}}
<footer>Keine Anlageberatung und keine Garantie für steigende Kurse. Automatische Auswertung nach festgelegten Kriterien (RSI, MACD, KGV, EPS) für das Planspiel Börse.</footer>
</main></body></html>"""


def meta_text(letzte_daten):
    stand = letzte_daten.strftime("%d.%m.%Y") if letzte_daten is not None else "nicht verfügbar"
    return f"Abruf: {datetime.now():%d.%m.%Y %H:%M} · Letzter Kurs-Stand: {stand} · Mindestscore: {MIN_SCORE}"


def baue_karten(treffer, namen):
    if not treffer:
        return ("<p class=\"leer\">Aktuell wurden keine Aktien gefunden, die die festgelegten "
                "Kriterien ausreichend erfüllen.</p>")
    karten = ""
    for i, (s, tk, teil, pos) in enumerate(sorted(treffer, key=lambda x: -x[0]), 1):
        zeilen = "".join(
            f"<tr><th>{k}</th><td class=\"{'pos' if k in pos else ''}\">{html.escape(teil[k][1])}</td></tr>"
            for k in ("RSI", "MACD", "KGV", "EPS"))
        karten += (f"<section class=\"karte\"><h2>{i}. {html.escape(str(namen.get(tk, tk)))} "
                   f"<small>({html.escape(tk)})</small></h2>"
                   f"<div class=\"score\"><div class=\"balken\" style=\"width:{s:.0f}%\"></div><span>{s:.0f}/100</span></div>"
                   f"<table>{zeilen}</table>"
                   f"<p class=\"warum\">Warum interessant: positive Signale bei {html.escape(', '.join(pos))}</p></section>")
    return karten


def schreibe_webseite(treffer, namen, letzte_daten):
    meta = meta_text(letzte_daten)
    seite = (SEITE.replace("{{CSS}}", CSS).replace("{{META}}", meta)
             .replace("{{KARTEN}}", baue_karten(treffer, namen)))
    with open(HTML_DATEI, "w", encoding="utf-8") as f:
        f.write(seite)
    return os.path.abspath(HTML_DATEI)


# ------------------------- Hauptprogramm -------------------------
def analysiere():
    """Führt die komplette Analyse aus. Gibt (treffer, namen, letzte_daten) zurück. Fortschritt per print()."""
    print("Lade Aktienliste ...")
    liste = lade_aktienliste()
    namen = dict(zip(liste["ticker"], liste["name"]))
    print(f"{len(liste)} Aktien in der Liste. Lade Kursdaten ...")
    kurse = lade_kurse(list(liste["ticker"]))
    print(f"Kursdaten für {len(kurse)} Aktien verfügbar. Analysiere ...")

    fundamental_max = GEWICHTE["KGV"] + GEWICHTE["EPS"]
    gesamt_gewicht = sum(GEWICHTE.values())
    treffer, letzte_daten = [], None

    for n, (tk, close) in enumerate(kurse.items(), 1):
        if n % 25 == 0:
            print(f"  {n}/{len(kurse)} analysiert, bisher {len(treffer)} Treffer ...")
        try:
            letzte_daten = max(letzte_daten, close.index[-1]) if letzte_daten is not None else close.index[-1]
            teil = {
                "RSI": bewerte_rsi(berechne_rsi(close)),
                "MACD": bewerte_macd(*berechne_macd(close)),
            }
            # Vorfilter: Ohne genug technische Punkte ist der Mindestscore nicht mehr erreichbar
            tech_punkte = sum(GEWICHTE[k] * (v[0] or 0) for k, v in teil.items())
            if 100 * (tech_punkte + fundamental_max) / gesamt_gewicht < MIN_SCORE:
                continue
            pe, fpe, eps, eps_v, quelle = lade_fundamentals(tk)
            teil["KGV"] = bewerte_kgv(pe, fpe)
            teil["EPS"] = bewerte_eps(eps, eps_v, quelle)

            sc, pos = score(teil), positive(teil)
            if sc >= MIN_SCORE and len(pos) >= MIN_POSITIVE_SIGNALE:
                treffer.append((sc, tk, teil, pos))
        except Exception as e:
            print(f"Warnung: {tk} übersprungen ({e})")
    print("Analyse abgeschlossen.")
    return treffer, namen, letzte_daten


def main():
    """Konsolen-Variante: python main.py"""
    treffer, namen, letzte_daten = analysiere()
    print("\n" + "=" * 60)
    print(meta_text(letzte_daten))
    print("=" * 60)

    if not treffer:
        print("\nAktuell wurden keine Aktien gefunden, die die festgelegten Kriterien ausreichend erfüllen.")
    else:
        print("\nINTERESSANTE AKTIEN\n")
        for i, (sc, tk, teil, pos) in enumerate(sorted(treffer, key=lambda x: -x[0]), 1):
            print(f"{i}. {namen.get(tk, tk)} ({tk}) – {sc:.0f}/100")
            for k in ("RSI", "MACD", "KGV", "EPS"):
                print(f"   {k}: {teil[k][1]}")
            print(f"   Warum interessant: positive Signale bei {', '.join(pos)}\n")

    print("Hinweis: Keine Anlageberatung und keine Garantie für steigende Kurse.")
    pfad = schreibe_webseite(treffer, namen, letzte_daten)
    print(f"\nWebseite gespeichert: {pfad}")
    if BROWSER_OEFFNEN:
        webbrowser.open("file:///" + pfad.replace("\\", "/"))


if __name__ == "__main__":
    main()
