#!/usr/bin/env python3
"""
STORICO DELL'INTERVALLO (dalla v21.6)
=====================================
Un archivio separato, stato/intervallo_storico.db, con le partite di cui
si conoscono punteggio dell'intervallo e finale e, dove l'API le da',
le statistiche del solo primo tempo (e del solo secondo) di ogni
squadra: tiri, tiri in porta, angoli, possesso, expected goals.

Serve a due cose dell'analisi dell'intervallo in Live:
  - come reagisce ogni squadra quando all'intervallo e' sotto, pari o
    avanti (reazioni(), letta da valore.py);
  - il modello del primo tempo (modello_primo_tempo.py), che impara dalle
    statistiche come cambia il secondo tempo.

Il database del modello (calcio_dati.db) si legge soltanto: le partite
dei suoi 45 campionati si copiano da li' senza chiamate. Le altre:
  - i 16 campionati europei di football-data.co.uk, stagione 2025/26,
    che hanno le quote di Pinnacle per imparare il modello (1 chiamata a
    campionato per il calendario);
  - tutti gli altri campionati che l'app analizza: quelli aggiunti per
    le giocate di valore (campionati_valore.py) e, dalla v21.8, tutti
    quelli che l'intervallo puo' analizzare (leghe_intervallo()),
    stagione in corso.
Le statistiche costano 1 chiamata a partita (fixtures/statistics con
half=true). Ci si ferma sopra META' del limite giornaliero, per lasciare
tutto il resto al pipeline e a valore.py.

USO, dalla cartella del progetto (con . ~/.previsioni_env):
    python3 scripts/storico_intervallo.py calendario     calendari, poche chiamate
    python3 scripts/storico_intervallo.py statistiche N  al massimo N partite
    python3 scripts/storico_intervallo.py aggiorna       i due di seguito (ogni mattina)
    python3 scripts/storico_intervallo.py conta          cosa c'e' nell'archivio
"""

import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ARCHIVIO = os.path.join("stato", "intervallo_storico.db")
DB_MODELLO = "calcio_dati.db"
API_KEY = os.environ.get("API_FOOTBALL_KEY", "").strip()
BASE_URL = "https://v3.football.api-sports.io"
STAGIONE = 2026
# i campionati di football-data.co.uk (test_valore.py) con le quote di Pinnacle
LEGHE_PINNACLE = (39, 40, 140, 141, 135, 136, 78, 79, 61, 62, 88, 144, 94, 203, 197, 179)
STAGIONE_PINNACLE = 2025
QUOTA_MASSIMA = 0.5          # del limite giornaliero di chiamate
PROVE_A_VUOTO = 12
SMORZA = 6                   # partite "medie" aggiunte a ogni squadra nella stima
FINITE = ("FT", "AET", "PEN")
VOCI = {"Shots on Goal": "porta", "Total Shots": "tiri", "Corner Kicks": "angoli",
        "Ball Possession": "possesso", "expected_goals": "xg", "Shots insidebox": "area",
        "Dangerous Attacks": "pericolosi", "Attacks": "attacchi", "Red Cards": "rossi"}


def chiama(endpoint, parametri):
    url = f"{BASE_URL}/{endpoint}?" + urllib.parse.urlencode(parametri)
    req = urllib.request.Request(url, headers={"x-apisports-key": API_KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def apri():
    os.makedirs(os.path.dirname(ARCHIVIO), exist_ok=True)
    c = sqlite3.connect(ARCHIVIO, timeout=60)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS partite (
            fid INTEGER PRIMARY KEY, lega INTEGER, stagione INTEGER, data TEXT,
            campionato TEXT, casa_id INTEGER, fuori_id INTEGER, casa TEXT, fuori TEXT,
            ht_c INTEGER, ht_f INTEGER, ft_c INTEGER, ft_f INTEGER, stato TEXT);
        CREATE TABLE IF NOT EXISTS statistiche (
            fid INTEGER PRIMARY KEY, pt TEXT, st TEXT, letta TEXT, esito TEXT);
        CREATE TABLE IF NOT EXISTS calendari (
            lega INTEGER, stagione INTEGER, letto TEXT, PRIMARY KEY (lega, stagione));
        CREATE INDEX IF NOT EXISTS partite_casa ON partite (casa_id);
        CREATE INDEX IF NOT EXISTS partite_fuori ON partite (fuori_id);
    """)
    return c


def numero(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip().rstrip("%")
        try:
            return float(v)
        except ValueError:
            return None
    return float(v)


def riduci(elenco):
    """Dalla lista dell'API ai soli numeri che servono."""
    out = {}
    for x in elenco or []:
        k = VOCI.get(str(x.get("type")))
        if k:
            n = numero(x.get("value"))
            if n is not None:
                out[k] = n
    return out


# ---------------------------------------------------------------
#  calendari
# ---------------------------------------------------------------

def da_db_modello(c):
    """Le partite finite dei 45 campionati, copiate dal database del modello."""
    if not os.path.exists(DB_MODELLO):
        return 0
    m = sqlite3.connect(f"file:{DB_MODELLO}?mode=ro", uri=True, timeout=60)
    nomi = {r[0]: f"{r[1]} - {r[2]}" for r in m.execute("SELECT id, country, name FROM leagues")}
    righe = []
    for fid, lega, stagione, data, hid, aid, hn, an, raw in m.execute(
            "SELECT id, league_id, season, date, home_team_id, away_team_id, home_team_name,"
            " away_team_name, raw_json FROM fixtures WHERE status IN ('FT','AET','PEN')"):
        try:
            s = json.loads(raw)["score"]
        except Exception:
            continue
        ht, ft = s.get("halftime") or {}, s.get("fulltime") or {}
        if ht.get("home") is None or ft.get("home") is None:
            continue
        righe.append((fid, lega, stagione, data, nomi.get(lega, ""), hid, aid, hn, an,
                      ht["home"], ht["away"], ft["home"], ft["away"], "FT"))
    m.close()
    c.executemany("INSERT OR REPLACE INTO partite VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", righe)
    c.commit()
    return len(righe)


def leghe_viste():
    """Gli id dei campionati che vediamo all'intervallo, dai nomi gia' raccolti."""
    nomi = set()
    for f in (os.path.join("stato", "intervallo_statistiche.json"),
              os.path.join("stato", "intervallo_analisi.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                nomi |= {x.get("campionato") for x in json.load(fh).values()}
        except (OSError, ValueError):
            pass
    ids = set()
    try:
        from campionati_valore import CAMPIONATI_VALORE
        ids |= set(CAMPIONATI_VALORE)
    except Exception:
        pass
    try:
        with open(os.path.join("stato", "leghe_tipo.json"), encoding="utf-8") as fh:
            tipi = json.load(fh)["leghe"]
        for lid, (tipo, paese, nome) in tipi.items():
            if tipo == "League" and f"{paese} - {nome}" in nomi:
                ids.add(int(lid))
    except (OSError, ValueError, KeyError):
        pass
    return ids


def leghe_intervallo():
    """
    (dalla v21.8) Tutti i campionati che l'intervallo puo' analizzare: quelli
    veri ammessi da previsioni.ammessa_intervallo (niente coppe, Russia,
    femminili, giovanili, riserve), con la loro stagione in corso; dalla
    v22.5 anche quelli senza quote nell'API. 1 chiamata (leagues?current=true).
    {id: stagione}; vuoto se l'API non risponde.
    """
    try:
        risp = chiama("leagues", {"current": "true"}).get("response") or []
        import previsioni as P
    except Exception as e:
        print(f"  elenco dei campionati non letto: {e}")
        return {}
    tipi = {x["league"]["id"]: [x["league"]["type"], x["country"]["name"], x["league"]["name"]] for x in risp}
    out = {}
    for x in risp:
        lid = x["league"]["id"]
        st = (x.get("seasons") or [{}])[0]
        # v22.5: anche senza quote nell'API, per stimare i gol attesi dallo storico
        if P.ammessa_intervallo(lid, tipi):
            out[lid] = int(st.get("year") or STAGIONE)
    return out


def calendario(c, solo_vecchi=True):
    """Calendari via API: 1 chiamata per campionato e stagione."""
    n = da_db_modello(c)
    print(f"  dal database del modello: {n} partite")
    nel_modello = {r[0] for r in c.execute(
        "SELECT DISTINCT lega FROM partite WHERE stagione = ?", (STAGIONE,))}
    lavori = [(l, STAGIONE_PINNACLE) for l in LEGHE_PINNACLE]
    lavori += [(l, STAGIONE) for l in sorted(leghe_viste() - nel_modello)]
    # v21.8: tutti i campionati dell'intervallo, non solo quelli gia' visti
    gia = {l for l, _ in lavori} | nel_modello
    lavori += [(l, st) for l, st in sorted(leghe_intervallo().items()) if l not in gia]
    oggi = datetime.now(timezone.utc).date().isoformat()
    fatte = 0
    for lega, stagione in lavori:
        letto = c.execute("SELECT letto FROM calendari WHERE lega=? AND stagione=?",
                          (lega, stagione)).fetchone()
        # una stagione finita si legge una volta; quella in corso una volta al giorno
        if letto and ((lega, stagione) in [(l, STAGIONE_PINNACLE) for l in LEGHE_PINNACLE]
                      or letto[0][:10] == oggi):
            continue
        try:
            risp = chiama("fixtures", {"league": lega, "season": stagione}).get("response") or []
        except Exception as e:
            print(f"  calendario {lega}/{stagione} non letto: {e}")
            continue
        righe = []
        for f in risp:
            fx, lg, sq, sc = f.get("fixture") or {}, f.get("league") or {}, f.get("teams") or {}, f.get("score") or {}
            st = (fx.get("status") or {}).get("short")
            ht, ft = sc.get("halftime") or {}, sc.get("fulltime") or {}
            if st not in FINITE or ht.get("home") is None or ft.get("home") is None:
                continue
            righe.append((fx["id"], lega, stagione, fx.get("date"),
                          f"{lg.get('country', '')} - {lg.get('name', '')}",
                          (sq.get("home") or {}).get("id"), (sq.get("away") or {}).get("id"),
                          (sq.get("home") or {}).get("name"), (sq.get("away") or {}).get("name"),
                          ht["home"], ht["away"], ft["home"], ft["away"], st))
        c.executemany("INSERT OR REPLACE INTO partite VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", righe)
        c.execute("INSERT OR REPLACE INTO calendari VALUES (?,?,?)",
                  (lega, stagione, datetime.now(timezone.utc).isoformat(timespec="minutes")))
        c.commit()
        fatte += 1
        time.sleep(0.2)
    print(f"  calendari letti dall'API: {fatte} (su {len(lavori)} campionati)")


# ---------------------------------------------------------------
#  statistiche del primo e del secondo tempo
# ---------------------------------------------------------------

def sotto_il_tetto():
    try:
        import consumo_api
        usate, limite = consumo_api.contatore()
    except Exception:
        return True
    if usate is None:
        return True
    if usate >= (limite or 7500) * QUOTA_MASSIMA:
        print(f"  gia' {usate} chiamate oggi su {limite}: mi fermo, riprendo domani")
        return False
    return True


def leghe_con_statistiche():
    """
    (v21.9, scelta dell'utente) I campionati di cui si leggono le statistiche
    del primo tempo: quelli del modello, quelli aggiunti per il valore, i 16
    di football-data e quelli comparsi davvero all'intervallo (si
    aggiungono da soli). Degli altri si tengono solo i calendari, che
    bastano per le reazioni delle squadre.
    """
    ids = set(LEGHE_PINNACLE) | leghe_viste()
    if os.path.exists(DB_MODELLO):
        m = sqlite3.connect(f"file:{DB_MODELLO}?mode=ro", uri=True, timeout=60)
        ids |= {r[0] for r in m.execute("SELECT DISTINCT league_id FROM fixtures")}
        m.close()
    return ids


def statistiche(c, massimo):
    ammesse = leghe_con_statistiche()
    # campionato e stagione dove l'API non ha le statistiche: dopo
    # PROVE_A_VUOTO partite tutte senza, si smette di chiederle
    prove = {}
    for lega, stagione, ok, n in c.execute("""SELECT p.lega, p.stagione, SUM(s.esito = 'ok'), COUNT(*)
            FROM partite p JOIN statistiche s ON s.fid = p.fid GROUP BY p.lega, p.stagione"""):
        prove[(lega, stagione)] = [ok or 0, n]
    # prima le partite della stagione in corso, le piu' recenti prima; si
    # riprovano le vuote lette entro un giorno dalla partita (troppo presto)
    da_fare = c.execute("""
        SELECT p.fid, p.lega, p.stagione FROM partite p LEFT JOIN statistiche s ON s.fid = p.fid
        WHERE p.data >= '2025-07-01' AND (s.fid IS NULL OR (s.esito = 'vuote'
              AND s.letta < datetime(p.data, '+1 day') AND p.data < datetime('now', '-1 day')))
        ORDER BY (p.stagione >= ?) DESC, p.data DESC""", (STAGIONE,)).fetchall()
    print(f"  statistiche da leggere: al massimo {min(massimo, len(da_fare))} di {len(da_fare)}")
    fatte = vuote = saltate = 0
    da_fare = [x for x in da_fare if x[1] in ammesse]
    print(f"  di cui nei campionati che compaiono all'intervallo: {len(da_fare)}")
    for fid, lega, stagione in da_fare:
        if fatte >= massimo:
            break
        pr = prove.setdefault((lega, stagione), [0, 0])
        if pr[0] == 0 and pr[1] >= PROVE_A_VUOTO:
            saltate += 1
            continue
        if fatte % 250 == 0 and not sotto_il_tetto():
            break
        try:
            risp = chiama("fixtures/statistics", {"fixture": fid, "half": "true"})
        except Exception as e:
            print(f"  {fid}: {e}")
            time.sleep(2)
            continue
        if risp.get("errors"):
            print(f"  {fid}: {risp['errors']}")
            if "rateLimit" in str(risp["errors"]):
                time.sleep(30)
            continue
        blocchi = risp.get("response") or []
        casa_id = c.execute("SELECT casa_id FROM partite WHERE fid=?", (fid,)).fetchone()[0]
        pt, st = {}, {}
        for b in blocchi:
            chi = "casa" if (b.get("team") or {}).get("id") == casa_id else "fuori"
            pt[chi] = riduci(b.get("statistics_1h"))
            st[chi] = riduci(b.get("statistics_2h"))
        ok = bool(len(pt) == 2 and pt["casa"] and pt["fuori"])
        vuote += not ok
        pr[0] += ok
        pr[1] += 1
        c.execute("INSERT OR REPLACE INTO statistiche VALUES (?,?,?,?,?)",
                  (fid, json.dumps(pt) if ok else None, json.dumps(st) if ok else None,
                   datetime.now(timezone.utc).isoformat(timespec="minutes"), "ok" if ok else "vuote"))
        fatte += 1
        if fatte % 50 == 0:
            c.commit()
        if fatte % 1000 == 0:
            print(f"  ... {fatte} lette ({vuote} senza statistiche)", flush=True)
        time.sleep(0.5)          # ~90 al minuto: resta posto per pipeline e valore.py
    c.commit()
    print(f"  statistiche lette: {fatte}, di cui senza dati del primo tempo: {vuote}; "
          f"saltate {saltate} di campionati senza statistiche")


# ---------------------------------------------------------------
#  per valore.py: come reagisce una squadra all'intervallo
# ---------------------------------------------------------------

def reazioni(team_id, prima_di, stagione=None, percorso=ARCHIVIO):
    """
    Le partite della squadra in questa stagione, prima della data data,
    divise per come era all'intervallo (sotto, pari, avanti). Per ognuna:
    quante, come e' finita (vinte, pari, perse), gol fatti e subiti nel
    secondo tempo a partita, e gli stessi numeri medi del campionato.
    """
    if not team_id or not os.path.exists(percorso):
        return None
    c = sqlite3.connect(f"file:{percorso}?mode=ro", uri=True, timeout=30)
    try:
        if stagione is None:
            # la stagione in corso della squadra (2026, o 2027 dove si conta cosi')
            stagione = c.execute("""SELECT MAX(stagione) FROM partite WHERE (casa_id = ? OR fuori_id = ?)
                AND stagione >= ?""", (team_id, team_id, STAGIONE)).fetchone()[0] or STAGIONE
        righe = c.execute("""SELECT lega, casa_id, ht_c, ht_f, ft_c, ft_f FROM partite
            WHERE stagione = ? AND data < ? AND (casa_id = ? OR fuori_id = ?)""",
                          (stagione, prima_di, team_id, team_id)).fetchall()
        if not righe:
            return None
        lega = max({r[0] for r in righe}, key=lambda l: sum(1 for r in righe if r[0] == l))
        media = {}
        for hc, hf, fc, ff in c.execute("""SELECT ht_c, ht_f, ft_c, ft_f FROM partite
                WHERE stagione = ? AND lega = ? AND data < ?""", (stagione, lega, prima_di)):
            for noi, loro, fn, fl in ((hc, hf, fc, ff), (hf, hc, ff, fc)):
                k = "sotto" if noi < loro else "pari" if noi == loro else "avanti"
                m = media.setdefault(k, [0, 0, 0])
                m[0] += 1
                m[1] += fn - noi
                m[2] += fl - loro
    finally:
        c.close()
    out = {}
    for _, casa_id, hc, hf, fc, ff in righe:
        noi, loro, fn, fl = (hc, hf, fc, ff) if casa_id == team_id else (hf, hc, ff, fc)
        k = "sotto" if noi < loro else "pari" if noi == loro else "avanti"
        x = out.setdefault(k, {"n": 0, "v": 0, "x": 0, "p": 0, "fatti": 0, "subiti": 0})
        x["n"] += 1
        x["v" if fn > fl else "x" if fn == fl else "p"] += 1
        x["fatti"] += fn - noi
        x["subiti"] += fl - loro
    for k, x in out.items():
        x["fatti"] = round(x["fatti"] / x["n"], 2)
        x["subiti"] = round(x["subiti"] / x["n"], 2)
        m = media.get(k)
        if m and m[0]:
            x["lega_fatti"] = round(m[1] / m[0], 2)
            x["lega_subiti"] = round(m[2] / m[0], 2)
    return {"partite": len(righe), "stati": out}


def gol_attesi_stima(lega, casa_id, fuori_id, prima_di, percorso=ARCHIVIO):
    """
    (v22.5) I gol attesi di una partita senza quote, stimati dallo storico
    della stagione del suo campionato: quanto segna e subisce ogni squadra
    (in casa e fuori insieme), tirato verso la media del campionato con
    SMORZA partite finte, per non fidarsi di due o tre partite. (lc, lf) o
    None se il campionato ha meno di 20 partite o una squadra meno di 3.
    """
    if not (lega and casa_id and fuori_id) or not os.path.exists(percorso):
        return None
    c = sqlite3.connect(f"file:{percorso}?mode=ro", uri=True, timeout=30)
    try:
        st = c.execute("SELECT MAX(stagione) FROM partite WHERE lega = ? AND stagione >= ?",
                       (lega, STAGIONE)).fetchone()[0]
        righe = c.execute("""SELECT casa_id, fuori_id, ft_c, ft_f FROM partite
            WHERE lega = ? AND stagione = ? AND data < ?""", (lega, st, prima_di)).fetchall() if st else []
    finally:
        c.close()
    if len(righe) < 20:
        return None
    gol_casa = sum(r[2] for r in righe) / len(righe)
    gol_fuori = sum(r[3] for r in righe) / len(righe)
    mu = (gol_casa + gol_fuori) / 2
    fatti, subiti, n = {}, {}, {}
    for hc, ha, gc, ga in righe:
        for t, f, s_ in ((hc, gc, ga), (ha, ga, gc)):
            fatti[t] = fatti.get(t, 0) + f
            subiti[t] = subiti.get(t, 0) + s_
            n[t] = n.get(t, 0) + 1
    if n.get(casa_id, 0) < 3 or n.get(fuori_id, 0) < 3 or mu <= 0:
        return None

    def att(t):
        return (fatti[t] + SMORZA * mu) / (n[t] + SMORZA) / mu

    def dif(t):
        return (subiti[t] + SMORZA * mu) / (n[t] + SMORZA) / mu
    return gol_casa * att(casa_id) * dif(fuori_id), gol_fuori * att(fuori_id) * dif(casa_id)


def id_squadra(nome, percorso=ARCHIVIO):
    """L'id di una squadra dal nome (per le analisi salvate senza id)."""
    if not nome or not os.path.exists(percorso):
        return None
    c = sqlite3.connect(f"file:{percorso}?mode=ro", uri=True, timeout=30)
    try:
        r = c.execute("""SELECT casa_id FROM partite WHERE casa = ? AND stagione >= ?
            UNION SELECT fuori_id FROM partite WHERE fuori = ? AND stagione >= ?""",
                      (nome, STAGIONE, nome, STAGIONE)).fetchall()
    finally:
        c.close()
    return r[0][0] if len(r) == 1 else None


def conta(c):
    for r in c.execute("""SELECT p.stagione, COUNT(*), COUNT(s.fid), SUM(s.esito = 'ok'),
            COUNT(DISTINCT p.lega) FROM partite p LEFT JOIN statistiche s ON s.fid = p.fid
            GROUP BY p.stagione ORDER BY p.stagione"""):
        print(f"  stagione {r[0]}: {r[1]} partite in {r[4]} campionati, statistiche cercate {r[2]}, trovate {r[3] or 0}")


def main():
    comando = sys.argv[1] if len(sys.argv) > 1 else "conta"
    c = apri()
    if comando in ("calendario", "aggiorna"):
        calendario(c)
    if comando == "statistiche":
        statistiche(c, int(sys.argv[2]) if len(sys.argv) > 2 else 500)
    if comando == "aggiorna":
        statistiche(c, 3000)
    conta(c)
    c.close()


if __name__ == "__main__":
    main()
