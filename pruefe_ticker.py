"""
Prüft alle Ticker aus ticker_zuordnung.csv gegen Yahoo Finance.
Start:  python pruefe_ticker.py   (im selben Ordner wie main.py)
Ergebnis: Liste aller Aktien ohne brauchbare Kursdaten + Vorschläge.
Die Datei ticker_zuordnung.csv wird NICHT verändert.
"""
import pandas as pd
import yfinance as yf

MIN_TAGE = 60          # so viele Kurstage braucht main.py mindestens
BLOCK = 40

zuordnung = pd.read_csv("ticker_zuordnung.csv", dtype=str).fillna("")
liste = pd.read_csv("aktienliste_isin.csv", dtype=str).fillna("")
df = liste.merge(zuordnung, on="isin", how="left").fillna("")
df["zeile"] = df.index + 2

schlecht = []
ticker = [t for t in df["ticker"] if t]
print(f"Prüfe {len(ticker)} Ticker ...")

ok = set()
for i in range(0, len(ticker), BLOCK):
    block = ticker[i:i + BLOCK]
    try:
        d = yf.download(block, period="6mo", group_by="ticker",
                        auto_adjust=True, progress=False, threads=True)
    except Exception as e:
        print("Blockfehler:", e)
        continue
    for tk in block:
        try:
            s = d[tk]["Close"].dropna() if isinstance(d.columns, pd.MultiIndex) else d["Close"].dropna()
            if len(s) >= MIN_TAGE:
                ok.add(tk)
        except Exception:
            pass
    print(f"  {min(i + BLOCK, len(ticker))}/{len(ticker)} geprüft")

fehler = df[(df["ticker"] == "") | (~df["ticker"].isin(ok))]
print(f"\n{len(fehler)} Aktien ohne brauchbare Kursdaten:\n")

zeilen = []
for _, r in fehler.iterrows():
    vorschlaege = []
    try:
        for q in yf.Search(r["isin"], max_results=8).quotes + yf.Search(r["name"], max_results=8).quotes:
            if q.get("quoteType") == "EQUITY" and q.get("symbol") and q["symbol"] not in vorschlaege:
                vorschlaege.append(q["symbol"])
    except Exception:
        pass
    text = (f"Zeile {r['zeile']}: {r['name']} | {r['isin']} | aktuell: '{r['ticker']}' "
            f"| Vorschläge: {', '.join(vorschlaege[:5]) or 'keine gefunden'}")
    print(text)
    zeilen.append(text)

with open("fehlende_ticker.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(zeilen) if zeilen else "Alle Ticker in Ordnung.")
print("\nFertig. Ergebnis steht auch in fehlende_ticker.txt")
if not zeilen:
    print("Alle Ticker liefern Daten.")
input("Enter drücken zum Beenden ...")
