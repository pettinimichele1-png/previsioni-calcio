"""
CORNER E CARTELLINI: ABBIAMO I DATI?
====================================
Prima di pensare a un modello sui mercati "sottili" servono due cose, e
questo script dice subito se le abbiamo. Non scrive niente: legge il
database e fa pochissime chiamate all'API.

1. LE STATISTICHE (quanti corner e cartellini ci sono stati)
   Sono gia' nel nostro database, raccolte a ogni partita. Qui si conta
   quante partite le hanno davvero, campionato per campionato: senza
   storico non si stima niente.

2. LE QUOTE su quei mercati
   E' il punto critico. Le quote storiche su corner e cartellini non
   esistono gratis da nessuna parte: chi le vende parte da qualche
   centinaio di euro l'anno. L'unica strada seria e' archiviarle noi da
   oggi in avanti, come abbiamo appena fatto per Over/Under e Gol/NoGol.
   Ma prima bisogna sapere se il nostro piano API le quota. Lo script
   chiede all'API tutti i mercati disponibili per qualche partita vera e
   li elenca.

USO:
    python scripts/esplora_mercati.py            oggi e domani (2 chiamate)
    python scripts/esplora_mercati.py 3          tre giorni (3 chiamate)
"""

import os
import sys
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

DB_PATH = "calcio_dati.db"
API_KEY = os.environ.get("API_FOOTBALL_KEY", "")
BASE = "https://v3.football.api-sports.io"
PAROLE_INTERESSANTI = ("corner", "card", "booking", "shot", "offside", "foul")


def chiamata(endpoint, parametri):
    url = f"{BASE}/{endpoint}?" + urllib.parse.urlencode(parametri)
    richiesta = urllib.request.Request(url, headers={"x-apisports-key": API_KEY})
    with urllib.request.urlopen(richiesta, timeout=30) as r:
        dati = json.loads(r.read())
    return dati.get("response", []), dati.get("errors")


# ---------------------------------------------------------------

def statistiche_disponibili(conn):
    print("=" * 78)
    print("1. STATISTICHE GIA' NEL NOSTRO DATABASE")
    print("=" * 78)
    try:
        righe = conn.execute("""
            SELECT COALESCE(l.country || ' - ' || l.name, 'id ' || t.league_id),
                   COUNT(*),
                   SUM(CASE WHEN t.corners IS NOT NULL THEN 1 ELSE 0 END),
                   SUM(CASE WHEN t.yellow_cards IS NOT NULL THEN 1 ELSE 0 END),
                   SUM(CASE WHEN t.fouls IS NOT NULL THEN 1 ELSE 0 END)
            FROM team_match t
            LEFT JOIN leagues l ON l.id = t.league_id
            GROUP BY t.league_id
        """).fetchall()
    except sqlite3.OperationalError as e:
        print(f"  Database non leggibile: {e}")
        return
    righe.sort(key=lambda r: -r[2])
    print(f"  {'campionato':<40}{'squadre-partita':>16}{'corner':>9}{'cartellini':>12}")
    totali = [0, 0, 0]
    for nome, n, corner, gialli, falli in righe:
        totali = [totali[0] + n, totali[1] + corner, totali[2] + gialli]
        if corner:
            print(f"  {nome[:38]:<40}{n:>16}{corner:>9}{gialli:>12}")
    print(f"\n  TOTALE{'':<34}{totali[0]:>16}{totali[1]:>9}{totali[2]:>12}")
    if totali[0]:
        print(f"  copertura corner: {totali[1] / totali[0]:.0%}, "
              f"cartellini: {totali[2] / totali[0]:.0%}")
    print("\n  Ogni riga e' una squadra in una partita: due righe per partita.")
    print("  Per stimare corner attesi servono almeno 8-10 partite per squadra.")


def mercati_quotati(conn, giorni):
    """
    Le partite future non stanno nel database: si chiedono all'API le
    quote per data, una chiamata per giorno, e si guarda quali mercati
    compaiono e in quali campionati.
    """
    print("\n" + "=" * 78)
    print("2. MERCATI CHE L'API CI QUOTA DAVVERO")
    print("=" * 78)
    if not API_KEY:
        print("  Chiave API assente: lancia prima  source ~/.previsioni_env")
        return

    conteggio, esempi, leghe, chiamate, partite = {}, {}, {}, 0, 0
    oggi = datetime.now(timezone.utc).date()
    for giorno in range(giorni):
        data = (oggi + timedelta(days=giorno)).isoformat()
        try:
            risposta, errori = chiamata("odds", {"date": data, "page": 1})
            chiamate += 1
        except (urllib.error.URLError, OSError) as e:
            print(f"  errore di rete: {e}")
            break
        if errori:
            print(f"  l'API risponde: {errori}")
            break
        partite += len(risposta)
        for voce in risposta:
            lega = voce.get("league") or {}
            nome_lega = f"{(lega.get('country') or '')} - {lega.get('name', '?')}".strip(" -")
            for book in voce.get("bookmakers", []):
                for bet in book.get("bets", []):
                    nome = bet.get("name", "?")
                    conteggio.setdefault(nome, set()).add(book.get("name"))
                    if any(p in nome.lower() for p in PAROLE_INTERESSANTI):
                        leghe.setdefault(nome, set()).add(nome_lega)
                    if nome not in esempi and bet.get("values"):
                        esempi[nome] = bet["values"][:4]

    if not conteggio:
        print(f"  Nessuna quota trovata in {chiamate} chiamate.")
        return
    print(f"  {chiamate} chiamate, {partite} partite quotate, "
          f"{len(conteggio)} mercati diversi.\n")

    interessanti = {k: v for k, v in conteggio.items()
                    if any(p in k.lower() for p in PAROLE_INTERESSANTI)}
    print(f"  MERCATI SU CORNER, CARTELLINI, TIRI: {len(interessanti)}")
    if not interessanti:
        print("    nessuno: il nostro piano non li quota, e questa strada e'")
        print("    chiusa senza cambiare fornitore di dati.")
    for nome, books in sorted(interessanti.items(), key=lambda kv: -len(kv[1])):
        valori = ", ".join(f"{v.get('value')} @ {v.get('odd')}"
                           for v in esempi.get(nome, []))
        campionati = sorted(leghe.get(nome, []))
        print(f"    {nome[:44]:<46}{len(books):>3} bookmaker, "
              f"{len(campionati)} campionati")
        if valori:
            print(f"      esempio: {valori}")
        if campionati:
            print(f"      campionati: {', '.join(c[:28] for c in campionati[:6])}"
                  + (f" e altri {len(campionati) - 6}" if len(campionati) > 6 else ""))

    altri = [k for k in conteggio if k not in interessanti]
    print(f"\n  ALTRI MERCATI: {len(altri)}")
    for nome in sorted(altri, key=lambda k: -len(conteggio[k]))[:15]:
        print(f"    {nome[:44]:<46}{len(conteggio[nome]):>3} bookmaker")
    if len(altri) > 15:
        print(f"    ... e altri {len(altri) - 15}")


def main():
    giorni = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    if not os.path.exists(DB_PATH):
        print(f"{DB_PATH} non trovato: lancialo dalla cartella del codice.")
        return
    conn = sqlite3.connect(DB_PATH)
    statistiche_disponibili(conn)
    mercati_quotati(conn, giorni)
    conn.close()
    print("\n" + "=" * 78)
    print("""COME LEGGERE
  - Se nella parte 2 compaiono mercati su corner o cartellini con almeno
    3-4 bookmaker, la strada e' percorribile: si comincia ad archiviare
    quelle quote ogni giorno, e fra tre o quattro settimane si verifica
    se il nostro modello le batte, esattamente come stiamo facendo per
    Over/Under e Gol/NoGol.
  - Se non compaiono, servirebbe un fornitore diverso: i piani che
    quotano corner e cartellini partono da qualche centinaio di euro
    l'anno, e li valuteremmo solo dopo aver visto che il modello sui
    corner e' calibrato.""")


if __name__ == "__main__":
    main()
