"""
STATISTICHE PER GIOCATORE: RACCOLTA
===================================
Scarica, per le partite che abbiamo gia' in archivio, le statistiche di
ogni giocatore: minuti, tiri, tiri in porta, falli fatti e subiti, ruolo,
voto. Servono per provare a prevedere i tiri di un singolo giocatore.

Una chiamata per partita. Si puo' interrompere in qualsiasi momento e
rilanciare: riprende da dove era rimasta, saltando le partite gia'
scaricate.

Di default i cinque grandi campionati europei - Serie A, Premier, Liga,
Bundesliga, Ligue 1 - e SOLO la stagione in corso: i mercati sui
giocatori esistono sui bookmaker italiani anche se la nostra API non li
quota, e il ruolo di un giocatore vale per la squadra in cui gioca ORA.

USO:
    python scripts/raccolta_giocatori.py              300 partite
    python scripts/raccolta_giocatori.py 100          100 partite
    python scripts/raccolta_giocatori.py stato        cosa abbiamo gia'

    LEGHE="Italy - Serie A,Spain - La Liga" python scripts/raccolta_giocatori.py
"""

import os
import sys
import json
import time
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DB_PATH = "calcio_dati.db"
API_KEY = os.environ.get("API_FOOTBALL_KEY", "")
BASE = "https://v3.football.api-sports.io"
LEGHE = [x.strip() for x in os.environ.get(
    "LEGHE",
    "Italy - Serie A,England - Premier League,Spain - La Liga,"
    "Germany - Bundesliga,France - Ligue 1").split(",") if x.strip()]
# Solo la stagione in corso: con il mercato estivo le rose cambiano, e i
# tiri di un giocatore dipendono dal ruolo che ha in QUESTA squadra.
STAGIONE = int(os.environ.get("SEASON", "2026"))
MAX_PREDEFINITO = 300
PAUSA = 0.3


def chiamata(endpoint, parametri):
    url = f"{BASE}/{endpoint}?" + urllib.parse.urlencode(parametri)
    richiesta = urllib.request.Request(url, headers={"x-apisports-key": API_KEY})
    with urllib.request.urlopen(richiesta, timeout=30) as r:
        dati = json.loads(r.read())
    return dati.get("response", []), dati.get("errors")


def crea_tabella(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS player_match (
            fixture_id  INTEGER,
            player_id   INTEGER,
            nome        TEXT,
            team_id     INTEGER,
            league_id   INTEGER,
            season      INTEGER,
            date        TEXT,
            is_home     INTEGER,
            minuti      REAL,
            ruolo       TEXT,
            titolare    INTEGER,
            voto        REAL,
            tiri        REAL,
            tiri_porta  REAL,
            falli_fatti REAL,
            falli_subiti REAL,
            gol         REAL,
            assist      REAL,
            PRIMARY KEY (fixture_id, player_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pm_giocatore "
                 "ON player_match (player_id, date)")
    conn.commit()


def numero(valore):
    try:
        return float(valore)
    except (TypeError, ValueError):
        return None


def salva_partita(conn, fid, risposta, info):
    league_id, season, data, casa = info
    righe = []
    for squadra in risposta:
        tid = (squadra.get("team") or {}).get("id")
        for g in squadra.get("players") or []:
            p = g.get("player") or {}
            st = (g.get("statistics") or [{}])[0]
            gm = st.get("games") or {}
            tiri = st.get("shots") or {}
            falli = st.get("fouls") or {}
            gol = st.get("goals") or {}
            minuti = numero(gm.get("minutes"))
            if minuti is None:
                continue                      # mai entrato in campo
            righe.append((
                fid, p.get("id"), p.get("name"), tid, league_id, season, data,
                1 if tid == casa else 0, minuti, gm.get("position"),
                0 if gm.get("substitute") else 1, numero(gm.get("rating")),
                numero(tiri.get("total")) or 0.0, numero(tiri.get("on")) or 0.0,
                numero(falli.get("committed")) or 0.0,
                numero(falli.get("drawn")) or 0.0,
                numero(gol.get("total")) or 0.0, numero(gol.get("assists")) or 0.0))
    conn.executemany("INSERT OR REPLACE INTO player_match VALUES "
                     "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", righe)
    conn.commit()
    return len(righe)


def da_fare(conn, quante):
    segnaposto = ",".join("?" * len(LEGHE))
    return conn.execute(f"""
        SELECT f.id, f.league_id, f.season, f.date,
               (SELECT team_id FROM team_match t
                WHERE t.fixture_id = f.id AND t.is_home = 1)
        FROM fixtures f
        JOIN leagues l ON l.id = f.league_id
        WHERE l.country || ' - ' || l.name IN ({segnaposto})
          AND f.season = ?
          AND f.goals_home IS NOT NULL
          AND f.id NOT IN (SELECT DISTINCT fixture_id FROM player_match)
        ORDER BY f.date DESC LIMIT ?
    """, LEGHE + [STAGIONE, quante]).fetchall()


def raccogli(quante):
    if not API_KEY:
        print("Chiave API assente: lancia prima  source ~/.previsioni_env")
        return
    conn = sqlite3.connect(DB_PATH)
    crea_tabella(conn)
    elenco = da_fare(conn, quante)
    if not elenco:
        print("Niente da scaricare: tutte le partite di questi campionati sono gia' prese.")
        stato()
        return
    print(f"Campionati: {', '.join(LEGHE)}")
    print(f"Stagione: {STAGIONE}")
    print(f"Partite da scaricare in questo giro: {len(elenco)}\n")
    fatte = giocatori = 0
    for fid, lega, stagione, data, casa in elenco:
        try:
            risposta, errori = chiamata("fixtures/players", {"fixture": fid})
        except (urllib.error.URLError, OSError) as e:
            print(f"  errore di rete: {e}. Mi fermo, rilancia piu' tardi.")
            break
        if errori:
            print(f"  l'API risponde: {errori}. Mi fermo.")
            break
        giocatori += salva_partita(conn, fid, risposta, (lega, stagione, data, casa))
        fatte += 1
        if fatte % 25 == 0:
            print(f"  {fatte} partite, {giocatori} righe giocatore")
        time.sleep(PAUSA)
    print(f"\nScaricate {fatte} partite, {giocatori} righe giocatore.")
    conn.close()
    stato()


def stato():
    if not os.path.exists(DB_PATH):
        print("Database non trovato.")
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        righe = conn.execute("""
            SELECT COALESCE(l.country || ' - ' || l.name, 'id ' || p.league_id),
                   COUNT(DISTINCT p.fixture_id), COUNT(*),
                   COUNT(DISTINCT p.player_id),
                   SUM(CASE WHEN p.minuti >= 60 THEN 1 ELSE 0 END)
            FROM player_match p LEFT JOIN leagues l ON l.id = p.league_id
            GROUP BY p.league_id ORDER BY 2 DESC
        """).fetchall()
    except sqlite3.OperationalError:
        print("\nNessuna statistica per giocatore raccolta finora.")
        return
    if not righe:
        print("\nNessuna statistica per giocatore raccolta finora.")
        return
    print(f"\n{'campionato':<34}{'partite':>9}{'righe':>9}{'giocatori':>11}{'da 60+ min':>12}")
    for nome, partite, n, giocatori, titolari in righe:
        print(f"  {nome[:32]:<34}{partite:>9}{n:>9}{giocatori:>11}{titolari:>12}")

    # con una stagione appena iniziata conta quante partite ha ognuno
    print(f"\n{'partite da 45+ minuti':<26}{'giocatori':>11}")
    for soglia in (3, 4, 5, 6, 8):
        n = conn.execute("""
            SELECT COUNT(*) FROM (
                SELECT player_id FROM player_match WHERE minuti >= 45
                GROUP BY player_id HAVING COUNT(*) >= ?)
        """, (soglia,)).fetchone()[0]
        print(f"  almeno {soglia:<18}{n:>11}")
    tiri = conn.execute("""
        SELECT AVG(tiri), AVG(falli_fatti) FROM player_match WHERE minuti >= 45
    """).fetchone()
    if tiri and tiri[0] is not None:
        print(f"\nMedie per giocatore da 45+ minuti: {tiri[0]:.2f} tiri, "
              f"{tiri[1]:.2f} falli")
    restanti = len(da_fare(conn, 10000))
    if restanti:
        print(f"Partite ancora da scaricare: {restanti}")
    conn.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "stato":
        stato()
    else:
        quante = int(sys.argv[1]) if len(sys.argv) > 1 else MAX_PREDEFINITO
        raccogli(quante)
