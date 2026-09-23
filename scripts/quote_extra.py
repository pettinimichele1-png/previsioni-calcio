"""
QUOTE SU CORNER E CARTELLINI: ARCHIVIO
======================================
Non esistono quote storiche gratuite su questi mercati: l'unico modo di
averle e' salvarle noi, giorno per giorno. Questo script lo fa.

Per ogni partita che stiamo prevedendo, quando mancano meno di sei ore
al fischio d'inizio, chiede all'API le quote e conserva:

    corner      totale calci d'angolo, linea piu' quotata (es. 9.5)
    cartellini  totale cartellini, stessa logica

di ognuna salva la quota MEDIA e la MIGLIORE fra i bookmaker, quante
case la quotavano e quanto mancava alla partita. Una fotografia sola per
partita: la prima che rientra nella finestra.

Costa una chiamata per partita, circa 40 al giorno.

PERCHE' I CORNER E NON I CARTELLINI
-----------------------------------
I corner si contano in un modo solo. I cartellini no: alcuni bookmaker
contano il rosso come due gialli, altri usano i punti disciplinari, e
il regolamento cambia da casa a casa. Archiviamo entrambi, ma il
giudizio sul modello lo daremo sui corner.

A COSA SERVE
------------
Fra tre o quattro settimane avremo abbastanza partite per chiedere:
il nostro modello sui corner batte il mercato? E' la stessa domanda che
ci siamo posti sull'1X2, con una differenza importante: sui corner i
bookmaker lavorano molto meno.

USO:
    python scripts/quote_extra.py        (lo lancia il pipeline)
    python scripts/quote_extra.py stato  quante partite abbiamo raccolto
"""

import os
import sys
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DB_PATH = "calcio_dati.db"
PREVISIONI = "previsioni.json"
SITO = os.environ.get("SITO_DIR", "docs")
API_KEY = os.environ.get("API_FOOTBALL_KEY", "")
BASE = "https://v3.football.api-sports.io"

FINESTRA_ORE = 6           # si fotografa quando mancano meno di sei ore
MIN_BOOKMAKER = 3          # sotto, la media non e' un consenso
MERCATI = {
    "Corners Over Under": "corner",
    "Cards Over/Under": "cartellini",
}


def trova(nome):
    for percorso in (nome, os.path.join(SITO, nome)):
        if os.path.exists(percorso):
            return percorso
    return None


def chiamata(endpoint, parametri):
    url = f"{BASE}/{endpoint}?" + urllib.parse.urlencode(parametri)
    richiesta = urllib.request.Request(url, headers={"x-apisports-key": API_KEY})
    with urllib.request.urlopen(richiesta, timeout=30) as r:
        dati = json.loads(r.read())
    return dati.get("response", []), dati.get("errors")


def crea_tabella(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS quote_extra (
            fixture_id  INTEGER,
            mercato     TEXT,
            linea       REAL,
            media_over  REAL,
            media_under REAL,
            max_over    REAL,
            max_under   REAL,
            n_book      INTEGER,
            ore_prima   REAL,
            data        TEXT,
            campionato  TEXT,
            catturato   TEXT,
            PRIMARY KEY (fixture_id, mercato)
        )
    """)
    conn.commit()


def leggi_linee(voce):
    """
    Dalla risposta dell'API ai due mercati che ci interessano: per ogni
    linea (9.5, 10, ...) le quote Over e Under di ogni bookmaker.
    """
    per_mercato = {}
    for book in voce.get("bookmakers", []):
        for bet in book.get("bets", []):
            chiave = MERCATI.get(bet.get("name"))
            if not chiave:
                continue
            quote = {}
            for v in bet.get("values") or []:
                testo = str(v.get("value") or "").strip()
                parti = testo.split()
                if len(parti) != 2 or parti[0] not in ("Over", "Under"):
                    continue
                try:
                    linea, quota = float(parti[1]), float(v.get("odd"))
                except (TypeError, ValueError):
                    continue
                if quota <= 1.0:
                    continue
                quote.setdefault(linea, {})[parti[0]] = quota
            for linea, lati in quote.items():
                if "Over" in lati and "Under" in lati:
                    per_mercato.setdefault(chiave, {}).setdefault(linea, []).append(lati)
    return per_mercato


def consenso(per_linea):
    """La linea quotata da piu' bookmaker, con media e migliore quota."""
    migliore = None
    for linea, elenco in per_linea.items():
        if len(elenco) < MIN_BOOKMAKER:
            continue
        if migliore is None or len(elenco) > len(per_linea[migliore]):
            migliore = linea
    if migliore is None:
        return None
    elenco = per_linea[migliore]
    over = [x["Over"] for x in elenco]
    under = [x["Under"] for x in elenco]
    return {"linea": migliore, "n_book": len(elenco),
            "media_over": sum(over) / len(over), "media_under": sum(under) / len(under),
            "max_over": max(over), "max_under": max(under)}


def raccogli():
    if not API_KEY:
        print("Chiave API assente: niente da fare.")
        return
    percorso = trova(PREVISIONI)
    if not percorso:
        print(f"{PREVISIONI} non trovato.")
        return
    with open(percorso, encoding="utf-8") as f:
        previsioni = json.load(f).get("previsioni", [])

    conn = sqlite3.connect(DB_PATH)
    crea_tabella(conn)
    gia = {r[0] for r in conn.execute("SELECT DISTINCT fixture_id FROM quote_extra")}
    adesso = datetime.now(timezone.utc)

    da_fare = []
    for p in previsioni:
        if p["fixture_id"] in gia:
            continue
        try:
            quando = datetime.fromisoformat(p["data"])
        except (KeyError, ValueError):
            continue
        ore = (quando - adesso).total_seconds() / 3600
        if 0 < ore <= FINESTRA_ORE:
            da_fare.append((p, ore))

    if not da_fare:
        print(f"Nessuna partita nella finestra delle {FINESTRA_ORE} ore.")
        conn.close()
        return

    salvate = chiamate = 0
    for p, ore in da_fare:
        try:
            risposta, errori = chiamata("odds", {"fixture": p["fixture_id"]})
            chiamate += 1
        except (urllib.error.URLError, OSError) as e:
            print(f"  errore di rete: {e}")
            break
        if errori:
            print(f"  l'API risponde: {errori}")
            break
        for voce in risposta:
            for mercato, per_linea in leggi_linee(voce).items():
                c = consenso(per_linea)
                if not c:
                    continue
                conn.execute("""
                    INSERT OR IGNORE INTO quote_extra VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """, (p["fixture_id"], mercato, c["linea"], c["media_over"],
                      c["media_under"], c["max_over"], c["max_under"], c["n_book"],
                      round(ore, 2), p["data"], p.get("campionato"),
                      adesso.isoformat(timespec="minutes")))
                salvate += 1
    conn.commit()
    print(f"Partite nella finestra: {len(da_fare)}, chiamate {chiamate}, "
          f"quote salvate {salvate}")
    conn.close()


def stato():
    if not os.path.exists(DB_PATH):
        print("Database non trovato.")
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        righe = conn.execute("""
            SELECT mercato, COUNT(*), AVG(n_book), AVG(ore_prima),
                   MIN(data), MAX(data)
            FROM quote_extra GROUP BY mercato
        """).fetchall()
    except sqlite3.OperationalError:
        print("Nessuna quota archiviata: lo script non e' ancora partito.")
        return
    if not righe:
        print("Nessuna quota archiviata finora.")
        return
    print(f"{'mercato':<14}{'partite':>9}{'bookmaker':>11}{'ore prima':>11}   periodo")
    for mercato, n, book, ore, primo, ultimo in righe:
        print(f"  {mercato:<12}{n:>9}{book:>11.1f}{ore:>11.1f}   "
              f"{primo[:10]} - {ultimo[:10]}")
    conclusi = conn.execute("""
        SELECT COUNT(*) FROM quote_extra q
        JOIN fixtures f ON f.id = q.fixture_id
        WHERE q.mercato = 'corner' AND f.goals_home IS NOT NULL
    """).fetchone()[0]
    print(f"\nPartite sui corner gia' giocate e verificabili: {conclusi}")
    print("Per un giudizio affidabile ne servono almeno 300.")
    per_lega = conn.execute("""
        SELECT campionato, COUNT(*) FROM quote_extra WHERE mercato = 'corner'
        GROUP BY campionato ORDER BY COUNT(*) DESC LIMIT 10
    """).fetchall()
    if per_lega:
        print("\nDove stiamo raccogliendo di piu':")
        for nome, n in per_lega:
            print(f"  {(nome or '?')[:44]:<46}{n:>5}")
    conn.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "stato":
        stato()
    else:
        raccogli()
