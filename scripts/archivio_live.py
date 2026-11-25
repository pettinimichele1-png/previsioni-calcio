"""
ARCHIVIO DELLE PARTITE IN CORSO (v25.3-25.4)
=======================================
Raccoglie dati per verificare la pagina "Calcolatore live" e le domande
sul primo tempo: com'erano minuto, punteggio, statistiche e quote live
durante la partita, e come e' finita. Nessuna archivio esistente ha le
statistiche minuto per minuto: le costruiamo da qui in avanti.

Ogni 5 minuti (cron):
  1. fixtures?live=all  (1 chiamata): minuto, punteggio, stato di tutte
     le partite in corso;
  2. odds/live          (1 chiamata): quote live di un bookmaker (l'API
     non dice quale); si tengono solo i mercati che usa la pagina;
  3. fixtures/statistics (1 chiamata a partita) per le partite con quote
     live, nel 1° tempo dal 10' e nel 2° tempo fino all'80': tiri, tiri
     in porta, angoli, possesso, xG dove c'e'. Una partita che risponde
     vuota 4 volte non si chiede piu'; un campionato si salta quando 3
     partite diverse non hanno dato nessuna statistica (v25.4: prima
     bastavano 3 risposte vuote, anche della stessa partita nei primi
     minuti, e alcuni campionati che le statistiche le hanno venivano
     saltati);
  4. fixtures?ids=      (1 chiamata ogni 20 partite): risultato del 1°
     tempo e finale delle partite sparite dal live.

Archivio a parte, fuori dal repository e da stato/: ~/archivio_live/live.db
(ARCHIVIO_LIVE_DB per cambiarlo). Non tocca app, previsioni e prova.
Freno: sopra il 70% del limite del giorno non chiama niente
(FRENO_ARCHIVIO_LIVE per cambiare la percentuale). Uso:
  python3 scripts/archivio_live.py          un giro
  python3 scripts/archivio_live.py conta    quante partite e foto ha
"""

import os
import sys
import json
import zlib
import sqlite3
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aggiorna_dati as ad
import consumo_api

DB = os.environ.get("ARCHIVIO_LIVE_DB", os.path.expanduser("~/archivio_live/live.db"))
FRENO = float(os.environ.get("FRENO_ARCHIVIO_LIVE", "0.70"))

MERCATI = {
    "1x2 (1st Half)", "Over/Under (1st Half)", "Over/Under Line (1st Half)",
    "Both Teams To Score (1st Half)", "Fulltime Result", "Double Chance",
    "Draw No Bet", "Both Teams to Score", "Match Goals", "Over/Under Line",
    "To Win 2nd Half", "Home Team Goals", "Away Team Goals",
    "Next 10 Minutes Total", "Both Teams To Score (2nd Half)",
}
FINITE = {"FT", "AET", "PEN", "PST", "CANC", "ABD", "AWD", "WO"}
VUOTE_PARTITA = 4   # risposte vuote prima di smettere di chiedere quella partita
PARTITE_VUOTE = 3   # partite senza statistiche prima di saltare il campionato


def apri():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB, timeout=30)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS partite (
        id INTEGER PRIMARY KEY, lega_id INTEGER, lega TEXT, paese TEXT,
        casa TEXT, ospite TEXT, inizio TEXT, ultima_foto TEXT,
        ht_casa INTEGER, ht_ospite INTEGER, ft_casa INTEGER, ft_ospite INTEGER,
        stato_finale TEXT, chiusa INTEGER DEFAULT 0, tentativi INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS foto (
        fixture_id INTEGER, ora TEXT, stato TEXT, minuto INTEGER, extra INTEGER,
        gol_casa INTEGER, gol_ospite INTEGER, quote BLOB, statistiche TEXT,
        PRIMARY KEY (fixture_id, ora));
    CREATE TABLE IF NOT EXISTS leghe_statistiche (
        lega_id INTEGER PRIMARY KEY, vuote INTEGER DEFAULT 0, piene INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS partite_statistiche (
        fixture_id INTEGER PRIMARY KEY, lega_id INTEGER,
        vuote INTEGER DEFAULT 0, piene INTEGER DEFAULT 0);
    """)
    return con


def quote_compatte(blocco):
    d = {}
    for b in blocco.get("odds", []):
        if b.get("name") in MERCATI:
            d[b["name"]] = [[v.get("value"), v.get("handicap"), v.get("odd"),
                             1 if v.get("suspended") else 0] for v in b.get("values", [])]
    if not d:
        return None
    return zlib.compress(json.dumps(d, separators=(",", ":")).encode())


def statistiche_compatte(risposta):
    """{"casa": {...}, "ospite": {...}} con le voci dell'API, o None se vuote."""
    if not risposta or len(risposta) < 2:
        return None
    out = {}
    for lato, blocco in zip(("casa", "ospite"), risposta[:2]):
        voci = {s["type"]: s["value"] for s in blocco.get("statistics", [])
                if s.get("value") is not None}
        out[lato] = voci
    if not out["casa"] and not out["ospite"]:
        return None
    return json.dumps(out, separators=(",", ":"), ensure_ascii=False)


def serve_statistica(stato, minuto):
    if stato == "1H":
        return minuto >= 10
    if stato == "2H":
        return minuto <= 80
    return False


def chiudi_finite(con, ora):
    """Risultato del 1° tempo e finale delle partite non piu' nel live."""
    limite = (ora - timedelta(minutes=15)).isoformat(timespec="seconds")
    ids = [r[0] for r in con.execute(
        "SELECT id FROM partite WHERE chiusa=0 AND ultima_foto < ? AND tentativi < 30 "
        "ORDER BY ultima_foto LIMIT 200", (limite,))]
    chiamate = 0
    for i in range(0, len(ids), 20):
        gruppo = ids[i:i + 20]
        risp = ad.chiamata_api("fixtures", {"ids": "-".join(map(str, gruppo))})
        chiamate += 1
        if risp is None:
            continue
        visti = set()
        for fx in risp:
            fid = fx["fixture"]["id"]
            visti.add(fid)
            st = fx["fixture"]["status"]["short"]
            ht = (fx.get("score") or {}).get("halftime") or {}
            ft = fx.get("goals") or {}
            if st in FINITE:
                con.execute("""UPDATE partite SET ht_casa=?, ht_ospite=?, ft_casa=?, ft_ospite=?,
                               stato_finale=?, chiusa=1 WHERE id=?""",
                            (ht.get("home"), ht.get("away"), ft.get("home"), ft.get("away"), st, fid))
            else:
                con.execute("UPDATE partite SET tentativi=tentativi+1 WHERE id=?", (fid,))
        for fid in set(gruppo) - visti:
            con.execute("UPDATE partite SET tentativi=tentativi+1 WHERE id=?", (fid,))
    return chiamate


def giro():
    usate, limite = consumo_api.contatore()
    if usate is not None and limite and usate >= limite * FRENO:
        print(f"[freno] {usate} chiamate oggi su {limite}: salto il giro")
        return
    ora = datetime.now(timezone.utc).replace(microsecond=0)
    ora_s = ora.isoformat(timespec="seconds")
    con = apri()

    live = ad.chiamata_api("fixtures", {"live": "all"})
    if live is None:
        print("live=all non risponde")
        return
    quote = ad.chiamata_api("odds/live") or []
    chiamate = 2
    per_id = {q["fixture"]["id"]: q for q in quote}
    # v25.4: si conta per partita, non per risposta
    senza_stat = {r[0] for r in con.execute(
        """SELECT lega_id FROM partite_statistiche GROUP BY lega_id
           HAVING SUM(piene) = 0 AND SUM(vuote >= ?) >= ?""", (VUOTE_PARTITA, PARTITE_VUOTE))}
    partite_vuote = {r[0] for r in con.execute(
        "SELECT fixture_id FROM partite_statistiche WHERE piene = 0 AND vuote >= ?", (VUOTE_PARTITA,))}

    foto = stat_fatte = 0
    for fx in live:
        fid = fx["fixture"]["id"]
        if fid not in per_id:
            continue  # senza quote live non serve alla pagina
        st = fx["fixture"]["status"]
        stato, minuto, extra = st.get("short"), st.get("elapsed") or 0, st.get("extra")
        lega = fx["league"]
        con.execute("""INSERT INTO partite (id, lega_id, lega, paese, casa, ospite, inizio, ultima_foto)
                       VALUES (?,?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET ultima_foto=excluded.ultima_foto""",
                    (fid, lega["id"], lega.get("name"), lega.get("country"),
                     fx["teams"]["home"]["name"], fx["teams"]["away"]["name"],
                     fx["fixture"].get("date"), ora_s))
        ht = (fx.get("score") or {}).get("halftime") or {}
        if ht.get("home") is not None and stato in ("2H", "ET", "BT", "P"):
            con.execute("UPDATE partite SET ht_casa=?, ht_ospite=? WHERE id=?",
                        (ht["home"], ht["away"], fid))
        stat = None
        if (serve_statistica(stato, minuto) and lega["id"] not in senza_stat
                and fid not in partite_vuote):
            risp = ad.chiamata_api("fixtures/statistics", {"fixture": fid})
            chiamate += 1
            stat_fatte += 1
            stat = statistiche_compatte(risp)
            campo = "piene" if stat else "vuote"
            con.execute(f"""INSERT INTO leghe_statistiche (lega_id, {campo}) VALUES (?, 1)
                            ON CONFLICT(lega_id) DO UPDATE SET {campo}={campo}+1""", (lega["id"],))
            con.execute(f"""INSERT INTO partite_statistiche (fixture_id, lega_id, {campo}) VALUES (?, ?, 1)
                            ON CONFLICT(fixture_id) DO UPDATE SET {campo}={campo}+1""", (fid, lega["id"]))
        con.execute("INSERT OR REPLACE INTO foto VALUES (?,?,?,?,?,?,?,?,?)",
                    (fid, ora_s, stato, minuto, extra,
                     fx["goals"]["home"], fx["goals"]["away"],
                     quote_compatte(per_id[fid]), stat))
        foto += 1
    con.commit()
    chiamate += chiudi_finite(con, ora)
    con.commit()
    con.close()
    print(f"{ora_s} live {len(live)}, con quote {len(per_id)}, foto {foto}, "
          f"statistiche {stat_fatte}, chiamate {chiamate}")


def conta():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    q = lambda s: con.execute(s).fetchone()[0]
    print("partite", q("SELECT COUNT(*) FROM partite"),
          "chiuse", q("SELECT COUNT(*) FROM partite WHERE chiusa=1"))
    print("foto", q("SELECT COUNT(*) FROM foto"),
          "con statistiche", q("SELECT COUNT(*) FROM foto WHERE statistiche IS NOT NULL"))
    print("campionati saltati (senza statistiche)",
          q(f"""SELECT COUNT(*) FROM (SELECT lega_id FROM partite_statistiche GROUP BY lega_id
                 HAVING SUM(piene) = 0 AND SUM(vuote >= {VUOTE_PARTITA}) >= {PARTITE_VUOTE})"""))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "conta":
        conta()
    else:
        giro()
