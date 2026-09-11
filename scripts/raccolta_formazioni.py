"""
RACCOLTA DELLE FORMAZIONI
==========================
Scarica per ogni partita gia' in database: undici titolare, panchina,
modulo e allenatore.

PERCHE' SOLO I TITOLARI
-----------------------
I minuti giocati sono un dato POST-partita: un espulso al 20' ne ha 20.
Usarli per prevedere significherebbe conoscere in anticipo l'espulsione.
I titolari annunciati escono invece circa un'ora prima del fischio
d'inizio: sono informazione legittimamente pre-partita.
Salviamo comunque anche la panchina, che serve a sapere chi era
disponibile ma non schierato (diverso da chi era assente del tutto).

COSTO
-----
Una chiamata per partita. Con ~2300 partite in database, circa 2300
chiamate. Lo script e' interrompibile: salta le partite gia' scaricate,
quindi puo' essere rilanciato piu' volte anche in giorni diversi.

USO:
    1. Inserisci la chiave in API_KEY
    2. python raccolta_formazioni.py
"""

import requests
import sqlite3
import json
import time
import os

# ---------------------------------------------------------------
import os as _os
API_KEY = _os.environ.get("API_FOOTBALL_KEY", "").strip()
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

DB_PATH = "calcio_dati.db"
PAUSA_TRA_CHIAMATE = 0.3

# limite di chiamate per singola esecuzione (per non esaurire la quota
# giornaliera; rilancia lo script il giorno dopo per continuare)
MAX_CHIAMATE_PER_ESECUZIONE = 6000


def chiamata_api(endpoint, params=None):
    try:
        resp = requests.get(f"{BASE_URL}/{endpoint}", headers=HEADERS,
                            params=params or {}, timeout=30)
    except requests.RequestException as e:
        print(f"    [ERRORE RETE] {e}")
        return None
    time.sleep(PAUSA_TRA_CHIAMATE)
    if resp.status_code != 200:
        print(f"    [ERRORE HTTP {resp.status_code}]")
        return None
    dati = resp.json()
    if dati.get("errors"):
        print(f"    [ERRORE API] {dati['errors']}")
        return None
    return dati.get("response", [])


def crea_tabelle(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lineups (
            fixture_id  INTEGER,
            team_id     INTEGER,
            formation   TEXT,
            coach_id    INTEGER,
            coach_name  TEXT,
            PRIMARY KEY (fixture_id, team_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lineup_players (
            fixture_id   INTEGER,
            team_id      INTEGER,
            player_id    INTEGER,
            player_name  TEXT,
            position     TEXT,
            grid         TEXT,
            is_starter   INTEGER,
            PRIMARY KEY (fixture_id, player_id)
        )
    """)
    # segna le partite gia' interrogate, anche quando l'API non ha dati:
    # evita di richiamarle inutilmente ad ogni esecuzione
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lineup_tentativi (
            fixture_id INTEGER PRIMARY KEY,
            esito      TEXT,
            quando     TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_lp_team ON lineup_players(team_id, player_id)")
    conn.commit()


def main():
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} non trovato.")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    crea_tabelle(conn)

    # partite ancora da scaricare
    cur.execute("""
        SELECT f.id, f.date, f.home_team_name, f.away_team_name
        FROM fixtures f
        WHERE f.id NOT IN (SELECT fixture_id FROM lineup_tentativi)
        ORDER BY f.date DESC
    """)
    da_fare = cur.fetchall()

    cur.execute("SELECT COUNT(*) FROM fixtures")
    totale = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM lineup_tentativi")
    gia_fatte = cur.fetchone()[0]

    print(f"Partite in database:        {totale}")
    print(f"Gia' interrogate:           {gia_fatte}")
    print(f"Da scaricare in questo giro: {len(da_fare)}")

    if not da_fare:
        print("\nTutte le partite sono gia' state interrogate.")
        conn.close()
        return

    limite = min(len(da_fare), MAX_CHIAMATE_PER_ESECUZIONE)
    print(f"Ne elaboro al massimo {limite} (limite di sicurezza sulla quota)\n")

    n_ok = n_vuote = n_errori = 0

    for i, (fid, data, casa, fuori) in enumerate(da_fare[:limite], 1):
        if i % 25 == 0 or i == 1:
            print(f"[{i}/{limite}] {data[:10]} {casa} - {fuori}")

        risposta = chiamata_api("fixtures/lineups", {"fixture": fid})

        if risposta is None:
            n_errori += 1
            continue

        if not risposta:
            # l'API non ha formazioni per questa partita: lo registro
            cur.execute("INSERT OR REPLACE INTO lineup_tentativi VALUES (?, ?, datetime('now'))",
                        (fid, "vuoto"))
            n_vuote += 1
            if n_vuote % 20 == 0:
                conn.commit()
            continue

        for blocco in risposta:
            team_id = blocco["team"]["id"]
            allenatore = blocco.get("coach") or {}
            cur.execute("INSERT OR REPLACE INTO lineups VALUES (?, ?, ?, ?, ?)",
                        (fid, team_id, blocco.get("formation"),
                         allenatore.get("id"), allenatore.get("name")))

            for voce in blocco.get("startXI", []) or []:
                g = voce.get("player") or {}
                cur.execute("INSERT OR REPLACE INTO lineup_players VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (fid, team_id, g.get("id"), g.get("name"),
                             g.get("pos"), g.get("grid"), 1))

            for voce in blocco.get("substitutes", []) or []:
                g = voce.get("player") or {}
                cur.execute("INSERT OR REPLACE INTO lineup_players VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (fid, team_id, g.get("id"), g.get("name"),
                             g.get("pos"), g.get("grid"), 0))

        cur.execute("INSERT OR REPLACE INTO lineup_tentativi VALUES (?, ?, datetime('now'))",
                    (fid, "ok"))
        n_ok += 1

        if n_ok % 20 == 0:
            conn.commit()

    conn.commit()

    # ---------------------------------------------------------------
    print("\n" + "=" * 62)
    print("RISULTATO")
    print("=" * 62)
    print(f"  partite con formazioni: {n_ok}")
    print(f"  partite senza dati:     {n_vuote}")
    print(f"  errori:                 {n_errori}")

    cur.execute("SELECT COUNT(DISTINCT fixture_id) FROM lineups")
    print(f"\nTotale partite con formazioni in database: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM lineup_players WHERE is_starter = 1")
    print(f"Titolari registrati: {cur.fetchone()[0]}")

    cur.execute("SELECT COUNT(*) FROM fixtures WHERE id NOT IN (SELECT fixture_id FROM lineup_tentativi)")
    rimaste = cur.fetchone()[0]
    if rimaste:
        print(f"\nPartite ancora da fare: {rimaste} -> rilancia lo script")

    # copertura per campionato
    print("\nCopertura formazioni per campionato:")
    cur.execute("""
        SELECT COALESCE(l.country || ' - ' || l.name, 'id=' || f.league_id),
               COUNT(DISTINCT f.id),
               COUNT(DISTINCT ln.fixture_id)
        FROM fixtures f
        LEFT JOIN leagues l ON l.id = f.league_id
        LEFT JOIN lineups ln ON ln.fixture_id = f.id
        GROUP BY f.league_id
        ORDER BY COUNT(DISTINCT ln.fixture_id) * 1.0 / MAX(COUNT(DISTINCT f.id), 1) ASC
        LIMIT 30
    """)
    for nome, tot, con in cur.fetchall():
        perc = con / tot * 100 if tot else 0
        allarme = "  <-- scarsa" if perc < 50 else ""
        print(f"  {nome:<40} {con:>5}/{tot:<5} ({perc:>5.1f}%){allarme}")

    # moduli piu' usati, come verifica di sanita'
    cur.execute("""
        SELECT formation, COUNT(*) FROM lineups
        WHERE formation IS NOT NULL GROUP BY formation
        ORDER BY COUNT(*) DESC LIMIT 8
    """)
    moduli = cur.fetchall()
    if moduli:
        print("\nModuli piu' usati:")
        for m, n in moduli:
            print(f"  {m:<10} {n}")

    conn.close()


if __name__ == "__main__":
    main()
