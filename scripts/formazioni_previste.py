"""
FORMAZIONI PREVISTE PER LE PARTITE FUTURE
==========================================
Produce, per ogni partita in programma, l'undici atteso di entrambe le
squadre e i tre indicatori che il modello usa (attacco, difesa, portiere).

TRE MODALITA'
-------------
    verifica    misura quanto e' accurata la stima sulle partite passate
                (nessuna chiamata API)
    probabili   costruisce l'undici stimato dallo storico, meno gli
                infortunati. Da lanciare al mattino.
    ufficiali   cerca le formazioni ufficiali delle partite imminenti e
                sostituisce la stima dove le trova. Da lanciare spesso.

Le ufficiali hanno sempre la precedenza: la tabella conserva il campo
"tipo" e previsioni.py preferisce sempre quelle.

PERCHE' NON BASTAVA IL CODICE PRECEDENTE
----------------------------------------
La prima versione di previsioni.py leggeva gli indicatori dall'ultima
partita GIOCATA dalla squadra: erano le assenze di ieri, non quelle di
domani. Qui gli indicatori vengono calcolati sull'undici atteso per la
partita in programma, che e' la cosa giusta.

USO:
    python formazioni_previste.py verifica
    python formazioni_previste.py probabili
    python formazioni_previste.py ufficiali
"""

import os
import sys
import json
import time
import sqlite3
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

DB_PATH = "calcio_dati.db"
BASE_URL = "https://v3.football.api-sports.io"
API_KEY = os.environ.get("API_FOOTBALL_KEY", "").strip()

GIORNI_AVANTI = int(os.environ.get("GIORNI_AVANTI", "4"))
DECADIMENTO = 0.85
MIN_PARTITE = 4
DILUIZIONE = 0.25          # come in indicatori_formazione_v3
# finestra entro cui cercare le formazioni ufficiali (minuti dal via)
MINUTI_PRIMA = 150
MINUTI_DOPO = 60


def chiamata(endpoint, params):
    url = f"{BASE_URL}/{endpoint}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"x-apisports-key": API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            dati = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"    [ERRORE] {endpoint}: {e}")
        return []
    time.sleep(0.3)
    if dati.get("errors"):
        print(f"    [ERRORE API] {dati['errors']}")
        return []
    return dati.get("response", [])


def crea_tabella(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS formazioni_previste (
            fixture_id INTEGER, team_id INTEGER, data TEXT,
            tipo TEXT,                     -- 'probabile' oppure 'ufficiale'
            undici TEXT,
            indisponibili TEXT,
            produzione_out REAL,
            difesa_out REAL,
            portiere_nuovo INTEGER,
            aggiornato TEXT,
            PRIMARY KEY (fixture_id, team_id))
    """)
    conn.commit()


# ---------------------------------------------------------------
# Storico: chi e' partito titolare e con quale produzione
# ---------------------------------------------------------------

def carica_storico(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT lp.fixture_id, lp.team_id, lp.player_id, lp.position, f.date
        FROM lineup_players lp JOIN fixtures f ON f.id = lp.fixture_id
        WHERE lp.is_starter = 1 ORDER BY f.date
    """)
    per_partita = {}
    for fid, tid, pid, pos, data in cur.fetchall():
        k = (fid, tid)
        if k not in per_partita:
            per_partita[k] = {"data": data, "undici": set(), "ruoli": {}}
        per_partita[k]["undici"].add(pid)
        per_partita[k]["ruoli"][pid] = pos

    storico = {}
    for (fid, tid), v in per_partita.items():
        storico.setdefault(tid, []).append((v["data"], fid, v["undici"], v["ruoli"]))
    for tid in storico:
        storico[tid].sort(key=lambda x: x[0])
    return storico


def carica_produzione(conn):
    """(team, player) -> lista (data, gol, assist), in ordine di data."""
    cur = conn.cursor()
    cur.execute("""
        SELECT ps.team_id, ps.player_id, ps.raw_json, f.date
        FROM player_stats ps JOIN fixtures f ON f.id = ps.fixture_id
        ORDER BY f.date
    """)
    produzione = {}
    for tid, pid, raw, data in cur.fetchall():
        try:
            blocco = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for st in blocco.get("statistics", []):
            g = st.get("games") or {}
            if g.get("minutes") is None:
                continue
            gol = (st.get("goals") or {}).get("total") or 0
            ass = (st.get("goals") or {}).get("assists") or 0
            produzione.setdefault((tid, pid), []).append((data, float(gol), float(ass)))
    return produzione


def undici_probabile(precedenti, esclusi=()):
    """precedenti: dal piu' recente. Restituisce (set undici, ruoli noti)."""
    titolarita, ruolo = {}, {}
    for k, (_, _, undici, ruoli) in enumerate(precedenti):
        peso = DECADIMENTO ** k
        for pid in undici:
            titolarita[pid] = titolarita.get(pid, 0.0) + peso
            if ruoli.get(pid):
                ruolo[pid] = ruoli[pid]
    disponibili = {p: v for p, v in titolarita.items() if p not in esclusi}
    if not disponibili:
        return None, {}
    scelta = []
    portieri = [(p, v) for p, v in disponibili.items() if ruolo.get(p) == "G"]
    if portieri:
        scelta.append(max(portieri, key=lambda x: x[1])[0])
    for p, _ in sorted(((p, v) for p, v in disponibili.items() if p not in scelta),
                       key=lambda x: -x[1]):
        if len(scelta) >= 11:
            break
        scelta.append(p)
    return set(scelta), ruolo


def indicatori_per_undici(tid, undici, ruoli_oggi, precedenti, produzione):
    """
    Calcola i tre indicatori usati dal modello, con la stessa logica di
    indicatori_formazione_v3 ma applicata a un undici futuro.
    precedenti: partite passate della squadra, dalla piu' recente.
    """
    if not precedenti or not undici:
        return None, None, None

    titolarita, ruolo_storico = {}, {}
    peso_tot = 0.0
    for k, (_, _, u, r) in enumerate(precedenti):
        peso = DECADIMENTO ** k
        peso_tot += peso
        for pid in u:
            titolarita[pid] = titolarita.get(pid, 0.0) + peso
            if r.get(pid):
                ruolo_storico[pid] = r[pid]
    if peso_tot <= 0 or not titolarita:
        return None, None, None
    for pid in titolarita:
        titolarita[pid] /= peso_tot

    somma = sum(titolarita.values())
    quota_out = (sum(t for p, t in titolarita.items() if p not in undici) / somma
                 if somma > 0 else 0.0)

    # produzione offensiva
    contributo = {}
    for (t, pid), storia in produzione.items():
        if t != tid or not storia:
            continue
        recenti = list(reversed(storia))
        valore = sum((gol + 0.7 * ass) * (DECADIMENTO ** k)
                     for k, (_, gol, ass) in enumerate(recenti))
        if valore > 0:
            contributo[pid] = valore
    produzione_out = None
    if contributo:
        tot = sum(contributo.values())
        fuori = sum(v for p, v in contributo.items() if p not in undici)
        produzione_out = (1 - DILUIZIONE) * (fuori / tot) + DILUIZIONE * quota_out

    # reparto difensivo
    dif = {p: t for p, t in titolarita.items() if ruolo_storico.get(p) == "D"}
    difesa_out = None
    if dif:
        tot_d = sum(dif.values())
        difesa_out = sum(v for p, v in dif.items() if p not in undici) / tot_d

    # portiere
    portiere_nuovo = None
    portieri = {p: t for p, t in titolarita.items() if ruolo_storico.get(p) == "G"}
    if portieri:
        atteso = max(portieri, key=portieri.get)
        oggi = next((p for p, r in (ruoli_oggi or {}).items() if r == "G"), None)
        if oggi is None:
            oggi = next((p for p in undici if ruolo_storico.get(p) == "G"), None)
        portiere_nuovo = 0 if oggi == atteso else 1

    return produzione_out, difesa_out, portiere_nuovo


# ---------------------------------------------------------------
def partite_in_programma(conn):
    cur = conn.cursor()
    cur.execute("SELECT id FROM leagues")
    leghe = {r[0] for r in cur.fetchall()}
    oggi = datetime.now(timezone.utc).date()
    partite = []
    for scarto in range(GIORNI_AVANTI + 1):
        giorno = (oggi + timedelta(days=scarto)).isoformat()
        for p in chiamata("fixtures", {"date": giorno}):
            if p["league"]["id"] in leghe and p["fixture"]["status"]["short"] in ("NS", "TBD"):
                partite.append(p)
    return partite


def verifica(conn):
    storico = carica_storico(conn)
    esiti = []
    for tid, partite in storico.items():
        for i, (data, fid, vero, _) in enumerate(partite):
            prec = list(reversed(partite[:i]))
            if len(prec) < MIN_PARTITE:
                continue
            stima, _ = undici_probabile(prec)
            if stima:
                esiti.append(len(stima & vero))
    if not esiti:
        print("Nessuna partita verificabile.")
        return
    media = sum(esiti) / len(esiti)
    print(f"Partite verificate: {len(esiti)}")
    print(f"Indovinati in media: {media:.2f} su 11 ({media/11*100:.0f}%)")


def probabili(conn):
    if not API_KEY:
        print("API_FOOTBALL_KEY mancante.")
        return
    crea_tabella(conn)
    cur = conn.cursor()

    storico = carica_storico(conn)
    produzione = carica_produzione(conn)
    partite = partite_in_programma(conn)
    print(f"Partite in programma: {len(partite)}")
    if not partite:
        return

    print("Scarico gli infortuni...")
    indisponibili = {}
    for giorno in sorted({p["fixture"]["date"][:10] for p in partite}):
        for v in chiamata("injuries", {"date": giorno}):
            indisponibili.setdefault(v["team"]["id"], {})[v["player"]["id"]] = \
                v["player"].get("reason", "n/d")
    print(f"  giocatori segnalati: {sum(len(v) for v in indisponibili.values())}")

    salvati = 0
    for p in partite:
        fid = p["fixture"]["id"]
        for lato in ("home", "away"):
            tid = p["teams"][lato]["id"]
            prec = list(reversed(storico.get(tid, [])))
            if len(prec) < MIN_PARTITE:
                continue
            fuori = set(indisponibili.get(tid, {}))
            undici, ruoli = undici_probabile(prec, esclusi=fuori)
            if not undici:
                continue
            ruoli_oggi = {p2: ruoli.get(p2) for p2 in undici}
            prod, dif, port = indicatori_per_undici(tid, undici, ruoli_oggi,
                                                    prec, produzione)
            # non sovrascrivo una formazione gia' ufficiale
            cur.execute("SELECT tipo FROM formazioni_previste WHERE fixture_id=? AND team_id=?",
                        (fid, tid))
            esistente = cur.fetchone()
            if esistente and esistente[0] == "ufficiale":
                continue
            cur.execute("""INSERT OR REPLACE INTO formazioni_previste
                           VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))""",
                        (fid, tid, p["fixture"]["date"], "probabile",
                         json.dumps(sorted(undici)),
                         json.dumps(indisponibili.get(tid, {})),
                         prod, dif, port))
            salvati += 1
    conn.commit()
    print(f"\nFormazioni probabili salvate: {salvati}")


def ufficiali(conn):
    if not API_KEY:
        print("API_FOOTBALL_KEY mancante.")
        return
    crea_tabella(conn)
    cur = conn.cursor()

    storico = carica_storico(conn)
    produzione = carica_produzione(conn)
    adesso = datetime.now(timezone.utc)

    cur.execute("""
        SELECT DISTINCT fixture_id, data FROM formazioni_previste
        WHERE tipo = 'probabile' ORDER BY data
    """)
    candidate = []
    for fid, data in cur.fetchall():
        inizio = datetime.fromisoformat(data.replace("Z", "+00:00"))
        minuti = (inizio - adesso).total_seconds() / 60
        if -MINUTI_DOPO <= minuti <= MINUTI_PRIMA:
            candidate.append((fid, minuti))

    print(f"Partite nella finestra utile: {len(candidate)}")
    aggiornate = 0
    for fid, minuti in sorted(candidate, key=lambda x: x[1]):
        blocchi = chiamata("fixtures/lineups", {"fixture": fid})
        if not blocchi:
            continue
        for b in blocchi:
            tid = b["team"]["id"]
            undici, ruoli_oggi = set(), {}
            for voce in b.get("startXI", []) or []:
                g = voce.get("player") or {}
                if g.get("id") is not None:
                    undici.add(g["id"])
                    ruoli_oggi[g["id"]] = g.get("pos")
            if len(undici) < 7:
                continue
            prec = list(reversed(storico.get(tid, [])))
            prod, dif, port = indicatori_per_undici(tid, undici, ruoli_oggi,
                                                    prec, produzione)
            cur.execute("""UPDATE formazioni_previste
                           SET tipo='ufficiale', undici=?, produzione_out=?,
                               difesa_out=?, portiere_nuovo=?, aggiornato=datetime('now')
                           WHERE fixture_id=? AND team_id=?""",
                        (json.dumps(sorted(undici)), prod, dif, port, fid, tid))
            aggiornate += 1
        conn.commit()
        print(f"  formazione ufficiale acquisita ({minuti:+.0f} min dal via)")

    print(f"\nSquadre aggiornate con formazione ufficiale: {aggiornate}")
    cur.execute("SELECT tipo, COUNT(*) FROM formazioni_previste GROUP BY tipo")
    for tipo, n in cur.fetchall():
        print(f"  {tipo}: {n}")


def main():
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} non trovato.")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lineup_players'")
    if not cur.fetchone():
        print("Formazioni storiche assenti: esegui prima raccolta_formazioni.py")
        conn.close()
        sys.exit(1)

    modalita = sys.argv[1] if len(sys.argv) > 1 else "probabili"
    if modalita == "verifica":
        verifica(conn)
    elif modalita == "probabili":
        probabili(conn)
    elif modalita == "ufficiali":
        ufficiali(conn)
    else:
        print("Modalita' ammesse: verifica, probabili, ufficiali")
    conn.close()


if __name__ == "__main__":
    main()
