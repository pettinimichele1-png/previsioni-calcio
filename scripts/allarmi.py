"""
ALLARME QUANDO UNA PARTE DEL SISTEMA SI BLOCCA
==============================================
Il pipeline lo chiama alla fine di ogni giro. Manda una notifica al
telefono se:
  - una fase del giro e' fallita (uno script si e' fermato con un errore);
  - le giocate di valore non girano da piu' di 3 ore e mezza, di giorno
    (valore.py auto lavora alle 7, 10, 13, 16, 18 e 20).

Ogni problema si avvisa una volta al giorno, non a ogni giro. Dalle 23
alle 7 gli avvisi aspettano il primo giro del mattino.

Lo stato sta in stato/allarmi.json.
"""

import os
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("Europe/Rome")
FILE = os.path.join("stato", "allarmi.json")
FILE_VALORE = os.path.join("stato", "valore_stato.json")
VALORE_FERMO = timedelta(hours=3, minutes=30)
ORA_SVEGLIA, ORA_SILENZIO = 7, 23


def carica(percorso):
    try:
        with open(percorso, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def valore_fermo(adesso):
    """Le giocate di valore ferme, di giorno: [problema] oppure []."""
    if not 8 <= adesso.hour <= 22:
        return []
    try:
        ultimo = datetime.fromisoformat(carica(FILE_VALORE)["ultimo_giro"])
    except (KeyError, TypeError, ValueError):
        return []
    if adesso - ultimo > VALORE_FERMO:
        return [f"giocate di valore ferme dalle {ultimo:%H:%M} del {ultimo:%d/%m}"]
    return []


def spedisci(dati):
    try:
        import notifiche as N
        if not os.path.exists(N.CHIAVE_PRIVATA):
            return "notifica non inviata: notifiche non configurate"
        conn = N.db()
        ok, _, errori = N.invia_a_tutti(conn, N.carica_chiave(), dati)
        conn.close()
        return f"notifica consegnata a {ok} telefoni" + (f", errori {errori}" if errori else "")
    except Exception as e:
        return f"notifica non inviata ({e})"


def controlla(fallite):
    """fallite: le descrizioni delle fasi fallite in questo giro."""
    adesso = datetime.now(FUSO)
    oggi = adesso.date().isoformat()
    st = carica(FILE)
    if st.get("giorno") != oggi:
        st = {"giorno": oggi, "avvisati": [], "da_avvisare": st.get("da_avvisare", [])}
    gia = set(st["avvisati"]) | {p for p, _ in st["da_avvisare"]}
    for problema in [f"fase fallita: {d}" for d in fallite] + valore_fermo(adesso):
        if problema not in gia:
            st["da_avvisare"].append([problema, f"{adesso:%H:%M}"])
            gia.add(problema)
            print(f"  [allarme] {problema}")
    if st["da_avvisare"] and ORA_SVEGLIA <= adesso.hour < ORA_SILENZIO:
        testo = "; ".join(f"{p} (alle {ora})" for p, ora in st["da_avvisare"])
        print("  [allarme] " + spedisci({
            "titolo": "Previsioni: qualcosa si e' bloccato",
            "testo": testo[:300] + ". Dettagli nel log del server.",
            "url": "./#/risultati", "tag": "allarme"}))
        st["avvisati"] += [p for p, _ in st["da_avvisare"]]
        st["da_avvisare"] = []
    os.makedirs(os.path.dirname(FILE), exist_ok=True)
    with open(FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
