#!/usr/bin/env python3
"""
TIRI, TIRI IN PORTA E FALLI DEI GIOCATORI: FASE 1, IN PROVA
============================================================
Prova sulla carta decisa dall'utente il 27/10/2026, dopo che la fase 0
(scripts/test_giocatori_stagione.py) e' passata: la media del giocatore,
corretta per l'avversario, prevede meglio della media del suo ruolo su
tutti e tre i mercati. Niente giocate e niente notifiche: le probabilita'
si mostrano nella pagina della partita ("Giocatori (in prova)") e il
giorno dopo si controlla come sono andate.

Solo i cinque grandi campionati, stagione in corso, solo i TITOLARI delle
formazioni ufficiali (formazioni_previste, tipo 'ufficiale'). Nessuna
chiamata all'API: statistiche e formazioni le scaricano gia'
raccolta_giocatori.py e formazioni_previste.py.

IL CALCOLO (quello che ha vinto nella prova del 27/10/2026)
    eventi attesi = media del giocatore ogni 90 minuti, tirata verso
                    quella del suo ruolo nel suo campionato (smorzata)
                  x quanto concede l'avversario rispetto alla media
                    (tiri subiti; per i falli commessi, i falli che
                    l'avversario subisce di solito)
                  x i minuti che il giocatore gioca di solito da titolare
    poi la binomiale negativa (piu' larga del Poisson: i tiri di un
    giocatore variano piu' di quanto il Poisson preveda) per le soglie
    almeno 1, 2 e 3. Accanto si salva anche la media di ruolo, per
    vedere dal vivo se il giocatore la batte ancora.

USO, dalla cartella del progetto:
    python3 scripts/giocatori.py prevedi    probabilita' delle partite con le formazioni ufficiali
    python3 scripts/giocatori.py verifica   come sono andate quelle giocate
Scrive stato/giocatori_previsioni.json.
"""

import json
import math
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

DB_PATH = "calcio_dati.db"
USCITA = os.path.join("stato", "giocatori_previsioni.json")
STAGIONE = 2026
LEGHE = (39, 140, 135, 78, 61)
SOGLIE = (1, 2, 3)
# mercato: (colonna, colonna dell'avversario, offensivo, smorzamento, smorzamento avversario, dispersione)
MERCATI = {
    "tiri": ("tiri", "tiri", True, 5.0, 8.0, 4.0),
    "porta": ("tiri_porta", "tiri", True, 8.0, 16.0, 2.0),
    "falli": ("falli_fatti", "falli_subiti", False, 8.0, 8.0, 4.0),
}
MIN_PRECEDENTI = 3
MIN_DA_TITOLARE = 2
TIENI_GIORNI = 90


def nb_almeno(m, soglia, r):
    """Probabilita' di almeno `soglia` eventi, binomiale negativa (r=None: Poisson)."""
    if r is None:
        t = somma = math.exp(-m)
        for k in range(1, soglia):
            t *= m / k
            somma += t
    else:
        p = r / (r + m)
        t = somma = p ** r
        for k in range(1, soglia):
            t *= (k - 1 + r) / k * (1 - p)
            somma += t
    return max(0.001, min(0.999, 1 - somma))


def carica_file():
    try:
        with open(USCITA, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def salva_file(dati):
    limite = (datetime.now(timezone.utc) - timedelta(days=TIENI_GIORNI)).isoformat()
    dati = {k: v for k, v in dati.items() if v.get("data", "") >= limite}
    tmp = USCITA + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dati, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, USCITA)


def storia(conn, prima_di):
    """Tutto quello che serve, calcolato sulle partite giocate prima di `prima_di`."""
    righe = conn.execute(f"""
        SELECT pm.fixture_id, pm.player_id, pm.nome, pm.team_id, pm.league_id, pm.minuti, pm.ruolo,
               pm.titolare, pm.tiri, pm.tiri_porta, pm.falli_fatti, pm.falli_subiti
        FROM player_match pm
        WHERE pm.season = ? AND pm.league_id IN ({",".join("?" * len(LEGHE))})
          AND pm.minuti IS NOT NULL AND pm.minuti > 0 AND pm.date < ?
    """, (STAGIONE,) + LEGHE + (prima_di,)).fetchall()
    gioc = defaultdict(lambda: {"righe": [], "ruoli": Counter(), "nome": None, "lega": None})
    ruolo = defaultdict(lambda: defaultdict(float))       # (lega, ruolo) -> colonna -> eventi; "_90" minuti
    per_partita = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))  # fid -> squadra -> colonna
    lega_di = {}
    for fid, pid, nome, tid, lega, minuti, ru, tit, tiri, porta, ff, fs in righe:
        v = {"tiri": tiri or 0, "tiri_porta": porta or 0, "falli_fatti": ff or 0, "falli_subiti": fs or 0}
        g = gioc[pid]
        g["righe"].append((v, minuti / 90.0, bool(tit)))
        g["nome"], g["lega"] = nome, lega
        if ru:
            g["ruoli"][ru] += 1
        k = (lega, ru or "?")
        ruolo[k]["_90"] += minuti / 90.0
        for c, x in v.items():
            ruolo[k][c] += x
            per_partita[fid][tid][c] += x
        lega_di[fid] = lega
    # quanto concede ogni squadra e la media del campionato, per partita
    conc = defaultdict(lambda: defaultdict(float))        # squadra -> colonna "subiti"/"provocati" -> somma; "_n"
    lega_tot = defaultdict(lambda: defaultdict(float))
    for fid, sq in per_partita.items():
        if len(sq) != 2:
            continue
        a, b = list(sq)
        lega = lega_di[fid]
        for x, y in ((a, b), (b, a)):
            conc[y]["_n"] += 1
            conc[y]["tiri"] += sq[x]["tiri"]                # tiri concessi da y
            conc[y]["falli_subiti"] += sq[y]["falli_subiti"]  # falli che y provoca
        lega_tot[lega]["_n"] += 1
        lega_tot[lega]["tiri"] += (sq[a]["tiri"] + sq[b]["tiri"]) / 2
        lega_tot[lega]["falli_subiti"] += (sq[a]["falli_subiti"] + sq[b]["falli_subiti"]) / 2
    return gioc, ruolo, conc, lega_tot


def probabilita(g, lega, avversario, ruolo, conc, lega_tot):
    """Per un titolare: {mercato: ([nostre per soglia], [di ruolo per soglia])}, o None se mancano dati."""
    righe = g["righe"]
    da_tit = [n for _, n, t in righe if t]
    ru = g["ruoli"].most_common(1)[0][0] if g["ruoli"] else "?"
    kr = (lega, ru)
    if (len(righe) < MIN_PRECEDENTI or len(da_tit) < MIN_DA_TITOLARE or ruolo[kr]["_90"] < 20
            or lega_tot[lega]["_n"] < 10):
        return None, ru, None
    novanta = max(0.3, sum(da_tit) / len(da_tit))
    novanta_prima = sum(n for _, n, _ in righe)
    out = {}
    for nome, (col, col_avv, _, k, ka, r) in MERCATI.items():
        mu_ruolo = ruolo[kr][col] / ruolo[kr]["_90"]
        eventi = sum(v[col] for v, _, _ in righe)
        smorzata = (eventi + k * mu_ruolo) / (novanta_prima + k)
        ma = lega_tot[lega][col_avv] / lega_tot[lega]["_n"]
        cn = conc[avversario]["_n"]
        f_avv = (conc[avversario][col_avv] + ka * ma) / (cn * ma + ka * ma) if ma > 0 else 1.0
        lam = max(0.02, smorzata * f_avv * novanta)
        lam_ruolo = max(0.02, mu_ruolo * novanta)
        out[nome] = ([round(nb_almeno(lam, s, r), 3) for s in SOGLIE],
                     [round(nb_almeno(lam_ruolo, s, None), 3) for s in SOGLIE])
    return out, ru, round(novanta * 90)


def prevedi(conn, ore_indietro=2):
    dati = carica_file()
    adesso = datetime.now(timezone.utc)
    da = (adesso - timedelta(hours=ore_indietro)).isoformat()
    # le partite future non sono nella tabella fixtures: si parte dalle
    # formazioni ufficiali, e casa e fuori si prendono da previsioni.json
    # (per le partite gia' giocate, dalla tabella fixtures)
    form = defaultdict(dict)
    for fid, tid, data, undici in conn.execute("""SELECT fixture_id, team_id, data, undici FROM formazioni_previste
                                                 WHERE tipo = 'ufficiale' AND data >= ?""", (da,)):
        form[fid][tid] = (data, undici)
    nomi = dict(conn.execute("SELECT id, name FROM teams"))
    prev = {}
    for percorso in ("previsioni.json", os.path.join(os.environ.get("SITO_DIR", "docs"), "previsioni.json")):
        try:
            with open(percorso, encoding="utf-8") as f:
                prev = {x["fixture_id"]: (x["casa"], x["fuori"]) for x in json.load(f).get("previsioni", [])}
            break
        except (OSError, ValueError, KeyError):
            continue
    partite = []
    for fid, sq in form.items():
        if len(sq) != 2 or str(fid) in dati:
            continue
        a, b = list(sq)
        leghe = {t: (conn.execute("SELECT league_id FROM player_match WHERE team_id = ? AND season = ? "
                                  "ORDER BY date DESC LIMIT 1", (t, STAGIONE)).fetchone() or [None])[0] for t in (a, b)}
        if leghe[a] not in LEGHE or leghe[a] != leghe[b]:
            continue
        f = conn.execute("SELECT home_team_id, away_team_id, home_team_name, away_team_name FROM fixtures WHERE id = ?",
                         (fid,)).fetchone()
        if f:
            casa, fuori, n_casa, n_fuori = f
        elif fid in prev and nomi.get(a) in prev[fid] and nomi.get(b) in prev[fid]:
            n_casa, n_fuori = prev[fid]
            casa, fuori = (a, b) if nomi.get(a) == n_casa else (b, a)
        else:
            continue
        partite.append((fid, sq[a][0], leghe[a], casa, fuori, n_casa, n_fuori))
    nuove = 0
    for fid, data, lega, casa, fuori, n_casa, n_fuori in partite:
        # calcolata una volta sola, con la formazione ufficiale
        undici = {t: u for t, (_, u) in form[fid].items()}
        gioc, ruolo, conc, lega_tot = storia(conn, data)
        lista, senza = [], 0
        for tid, avv, in_casa in ((casa, fuori, True), (fuori, casa, False)):
            for pid in json.loads(undici.get(tid) or "[]"):
                g = gioc.get(pid)
                if not g:
                    senza += 1
                    continue
                p, ru, minuti = probabilita(g, lega, avv, ruolo, conc, lega_tot)
                if p is None:
                    senza += 1
                    continue
                lista.append({"id": pid, "nome": g["nome"], "casa": in_casa, "ruolo": ru, "min": minuti,
                              "p": {k: v[0] for k, v in p.items()}, "r": {k: v[1] for k, v in p.items()}})
        dati[str(fid)] = {"data": data, "casa": n_casa, "fuori": n_fuori, "lega": lega,
                          "calcolato": adesso.isoformat(timespec="minutes"), "senza_dati": senza,
                          "giocatori": lista}
        nuove += 1
        print(f"  {n_casa} - {n_fuori}: {len(lista)} titolari calcolati, {senza} senza dati")
    salva_file(dati)
    print(f"Partite nuove: {nuove} (in archivio {len(dati)})")


def verifica(conn):
    dati = carica_file()
    chiuse = 0
    for fid, v in dati.items():
        if v.get("verificata"):
            continue
        righe = conn.execute("""SELECT player_id, minuti, tiri, tiri_porta, falli_fatti FROM player_match
                                WHERE fixture_id = ?""", (int(fid),)).fetchall()
        if not righe:
            continue
        fatti = {r[0]: r for r in righe}
        for g in v["giocatori"]:
            r = fatti.get(g["id"])
            g["esito"] = None if not r or not r[1] else {"min": r[1], "tiri": r[2] or 0, "porta": r[3] or 0,
                                                          "falli": r[4] or 0}
        v["verificata"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
        chiuse += 1
    salva_file(dati)
    print(f"Partite verificate ora: {chiuse}")
    r = riepilogo(dati)
    for k, x in r.items():
        print(f"  {k:<6} {x['n']:>5} previsioni  previsto {x['previsto']:.1%}  uscito {x['uscito']:.1%}  "
              f"log loss noi {x['noi']:.4f}  ruolo {x['ruolo']:.4f}")


def riepilogo(dati):
    """Come vanno dal vivo: per mercato, su tutte le soglie."""
    out = {}
    for k in MERCATI:
        n, prev, usc, noi, ru = 0, 0.0, 0, 0.0, 0.0
        for v in dati.values():
            for g in v.get("giocatori", []):
                e = g.get("esito")
                if not e:
                    continue
                for i, s in enumerate(SOGLIE):
                    si = e[k] >= s
                    p, q = g["p"][k][i], g["r"][k][i]
                    n += 1
                    prev += p
                    usc += si
                    noi -= math.log(p if si else 1 - p)
                    ru -= math.log(q if si else 1 - q)
        if n:
            out[k] = {"n": n, "previsto": prev / n, "uscito": usc / n, "noi": noi / n, "ruolo": ru / n}
    return out


def main():
    comando = sys.argv[1] if len(sys.argv) > 1 else ""
    if comando not in ("prevedi", "verifica"):
        print(__doc__)
        sys.exit(1)
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        prevedi(conn) if comando == "prevedi" else verifica(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
