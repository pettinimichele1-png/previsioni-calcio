"""
Aggiornamento incrementale del database
========================================
Aggiunge le partite concluse dall'ultimo aggiornamento in poi.
NON cancella mai nulla: lo storico cresce a ogni esecuzione.

Strategia economica: interroga ogni campionato per intervallo di date
(23 chiamate) invece di ogni singola squadra (400 chiamate).

USO MANUALE:
    python aggiorna_dati.py

USO AUTOMATICO:
    vedi aggiorna.bat + Utilita' di pianificazione di Windows
"""

import requests
import sqlite3
import json
import time
import os
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------
# CONFIGURAZIONE
# ---------------------------------------------------------------

import os as _os
API_KEY = _os.environ.get("API_FOOTBALL_KEY", "").strip()
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

DB_PATH = "calcio_dati.db"
LOG_PATH = "log_aggiornamenti.txt"

SEASON = int(_os.environ.get("SEASON", "2026"))
PAUSA_TRA_CHIAMATE = 0.3
STATI_VALIDI = ("FT", "AET", "PEN")

# Giorni di margine all'indietro rispetto all'ultima partita salvata.
# Serve a recuperare le partite che erano ancora in corso durante
# l'aggiornamento precedente e che nel frattempo sono terminate.
GIORNI_MARGINE = 3

SCARICA_STATISTICHE = True


def log(messaggio, file_log):
    """Stampa a schermo e scrive nel file di log."""
    print(messaggio)
    file_log.write(messaggio + "\n")


def chiamata_api(endpoint, params=None):
    try:
        resp = requests.get(f"{BASE_URL}/{endpoint}", headers=HEADERS,
                            params=params or {}, timeout=30)
    except requests.RequestException as e:
        print(f"    [ERRORE RETE] {e}")
        return None
    time.sleep(PAUSA_TRA_CHIAMATE)
    if resp.status_code != 200:
        print(f"    [ERRORE HTTP {resp.status_code}] {endpoint}")
        return None
    data = resp.json()
    if data.get("errors"):
        print(f"    [ERRORE API] {endpoint} -> {data['errors']}")
        return None
    return data.get("response", [])


def salva_fixture(cur, fx):
    cur.execute("""
        INSERT OR REPLACE INTO fixtures
        (id, league_id, season, date, home_team_id, away_team_id,
         home_team_name, away_team_name, goals_home, goals_away, status, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        fx["fixture"]["id"], fx["league"]["id"], fx["league"].get("season"),
        fx["fixture"]["date"],
        fx["teams"]["home"]["id"], fx["teams"]["away"]["id"],
        fx["teams"]["home"]["name"], fx["teams"]["away"]["name"],
        fx["goals"]["home"], fx["goals"]["away"],
        fx["fixture"]["status"]["short"],
        json.dumps(fx, ensure_ascii=False),
    ))


def scarica_statistiche(cur, fixture_id):
    stats = chiamata_api("fixtures/statistics", {"fixture": fixture_id})
    if stats:
        for blocco in stats:
            cur.execute(
                "INSERT OR REPLACE INTO fixture_stats (fixture_id, team_id, raw_json) "
                "VALUES (?, ?, ?)",
                (fixture_id, blocco["team"]["id"], json.dumps(blocco, ensure_ascii=False))
            )
    players = chiamata_api("fixtures/players", {"fixture": fixture_id})
    if players:
        for blocco in players:
            for pl in blocco.get("players", []):
                cur.execute("""
                    INSERT OR REPLACE INTO player_stats
                    (fixture_id, team_id, player_id, player_name, raw_json)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    fixture_id, blocco["team"]["id"],
                    pl["player"]["id"], pl["player"]["name"],
                    json.dumps(pl, ensure_ascii=False),
                ))


def main():
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} non trovato.")
        return

    file_log = open(LOG_PATH, "a", encoding="utf-8")
    adesso = datetime.now(timezone.utc)
    log(f"\n{'=' * 60}", file_log)
    log(f"AGGIORNAMENTO {adesso.strftime('%Y-%m-%d %H:%M')}", file_log)
    log(f"{'=' * 60}", file_log)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM fixtures")
    partite_prima = cur.fetchone()[0]

    cur.execute("SELECT id FROM fixtures")
    gia_presenti = {r[0] for r in cur.fetchall()}

    cur.execute("SELECT id, country, name FROM leagues ORDER BY country, name")
    campionati = cur.fetchall()
    log(f"Campionati da aggiornare: {len(campionati)}", file_log)

    oggi = adesso.date()
    totale_nuove = 0

    for league_id, paese, nome in campionati:
        # data dell'ultima partita salvata per questo campionato
        cur.execute(
            "SELECT MAX(date) FROM fixtures WHERE league_id = ? AND season = ?",
            (league_id, SEASON)
        )
        ultima = cur.fetchone()[0]

        if ultima:
            data_da = (datetime.fromisoformat(ultima.replace("Z", "+00:00")).date()
                       - timedelta(days=GIORNI_MARGINE))
        else:
            # campionato senza partite di questa stagione: guardo l'ultimo mese
            data_da = oggi - timedelta(days=30)

        nuove = chiamata_api("fixtures", {
            "league": league_id,
            "season": SEASON,
            "from": data_da.isoformat(),
            "to": oggi.isoformat(),
        }) or []

        concluse = [f for f in nuove if f["fixture"]["status"]["short"] in STATI_VALIDI]
        da_aggiungere = [f for f in concluse if f["fixture"]["id"] not in gia_presenti]

        if not da_aggiungere:
            log(f"  {paese} - {nome}: nessuna partita nuova", file_log)
            continue

        log(f"  {paese} - {nome}: {len(da_aggiungere)} partite nuove", file_log)

        for fx in da_aggiungere:
            fid = fx["fixture"]["id"]
            salva_fixture(cur, fx)
            if SCARICA_STATISTICHE:
                scarica_statistiche(cur, fid)
            gia_presenti.add(fid)
            totale_nuove += 1
            log(f"     + {fx['fixture']['date'][:10]} "
                f"{fx['teams']['home']['name']} {fx['goals']['home']}-"
                f"{fx['goals']['away']} {fx['teams']['away']['name']}", file_log)

        conn.commit()

    # riepilogo
    cur.execute("SELECT COUNT(*) FROM fixtures")
    partite_dopo = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM fixture_stats")
    n_stat = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM player_stats")
    n_players = cur.fetchone()[0]

    log(f"\nPartite nuove aggiunte: {totale_nuove}", file_log)
    log(f"Totale partite in database: {partite_prima} -> {partite_dopo}", file_log)
    log(f"Righe statistiche squadra: {n_stat}", file_log)
    log(f"Righe statistiche giocatori: {n_players}", file_log)

    conn.close()
    file_log.close()


if __name__ == "__main__":
    main()
