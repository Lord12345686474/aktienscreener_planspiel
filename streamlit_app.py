"""
Planspiel Börse – Aktien-Filter als Streamlit-Webseite (für iPad, Handy und PC).
Die Analyse-Logik steckt unverändert in main.py. Alle Einstellungen stehen oben in main.py.
Start am PC zum Testen:  streamlit run streamlit_app.py
"""
import contextlib
from datetime import datetime

import streamlit as st
import streamlit.components.v1 as components

import main as engine

st.set_page_config(page_title="Planspiel Börse – Aktien-Filter", page_icon="📈", layout="centered")


@st.cache_resource
def speicher():
    """Gemeinsamer Speicher: Das letzte Ergebnis bleibt erhalten, solange der Server wach ist."""
    return {"ergebnis": None}


class LogSchreiber:
    """Fängt die print()-Ausgaben der Analyse ab und zeigt sie live auf der Seite."""
    def __init__(self, platz):
        self.platz = platz
        self.zeilen = []

    def write(self, text):
        for zeile in text.splitlines():
            if zeile.strip():
                self.zeilen.append(zeile.rstrip())
        self.platz.code("\n".join(self.zeilen[-12:]), language=None)

    def flush(self):
        pass


def analyse_starten(log_platz):
    with contextlib.redirect_stdout(LogSchreiber(log_platz)):
        treffer, namen, letzte_daten = engine.analysiere()
    speicher()["ergebnis"] = {
        "karten": engine.baue_karten(treffer, namen),
        "meta": engine.meta_text(letzte_daten),
        "anzahl": len(treffer),
    }


st.title("Planspiel Börse – Aktien-Filter")

ergebnis = speicher()["ergebnis"]
meta_platz = st.empty()
meta_platz.caption(ergebnis["meta"] if ergebnis else "Noch keine Analyse in dieser Sitzung.")

start = st.button("Analyse starten", type="primary", disabled=False)
log_platz = st.empty()

if start:
    try:
        with st.spinner("Analyse läuft … das dauert einige Minuten. Bitte die Seite offen lassen."):
            analyse_starten(log_platz)
        log_platz.empty()
        ergebnis = speicher()["ergebnis"]
        meta_platz.caption(ergebnis["meta"])
    except Exception as e:
        st.error(f"{type(e).__name__}: {e}")
        ergebnis = speicher()["ergebnis"]

if ergebnis:
    hoehe = 140 + 330 * max(ergebnis["anzahl"], 1)
    seite = f"<style>{engine.CSS}</style><main>{ergebnis['karten']}</main>"
    components.html(seite, height=min(hoehe, 12000), scrolling=True)

st.caption(
    "Keine Anlageberatung und keine Garantie für steigende Kurse. Automatische Auswertung nach "
    "festgelegten Kriterien (RSI, MACD, KGV, EPS) für das Planspiel Börse."
)
