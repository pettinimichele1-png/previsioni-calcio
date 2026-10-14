#!/usr/bin/env python3
"""
MODELLO DEL PRIMO TEMPO (dalla v21.7)
=====================================
Il modello dell'intervallo (stato/intervallo.json, test_intervallo.py)
guarda solo il punteggio e quanti gol si sono visti rispetto alle attese.
Questo prova ad aggiungere le statistiche del primo tempo: tiri, tiri in
porta, angoli, possesso di ciascuna squadra. Una squadra che ha tirato
molto in porta senza segnare dovrebbe segnare di piu' nel secondo tempo.

Dati: le partite di stato/intervallo_storico.db (storico_intervallo.py)
con le statistiche del primo tempo, unite ai file di football-data.co.uk
(test_valore.py) per le quote di chiusura dell'exchange Betfair (o di
Pinnacle), da cui vengono i gol attesi di partenza, come dal vivo con Pinnacle. Sedici campionati
europei, stagioni 2025/26 e 2026/27.

PROVA, REGOLA DECISA PRIMA: si impara sul 2025/26 e si giudica sul
2026/27, che il modello non ha visto, contro il modello attuale imparato
sulle stesse partite. Misura principale: quanto bene si prevedono i gol
del secondo tempo di ciascuna squadra (log loss di Poisson); poi i
mercati dell'intervallo uno per uno. Il modello con le statistiche
passa solo se la forbice al 95% del miglioramento e' tutta sopra zero.
Se passa, si impara su tutte le partite e si scrive
stato/intervallo_stat.json, che valore.py usa per l'analisi
dell'intervallo (non per le giocate di valore, che restano come sono).
Se non passa, il file non si scrive e l'analisi resta col modello attuale.

USO, dalla cartella del progetto:
    python3 scripts/modello_primo_tempo.py          prova e, se passa, salva
    python3 scripts/modello_primo_tempo.py prova    solo la prova, non salva niente
"""

import csv
import difflib
import io
import json
import math
import os
import random
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quote_giuste as Q
import test_intervallo as T
import test_valore as V

ARCHIVIO = os.path.join("stato", "intervallo_storico.db")
USCITA = os.path.join("stato", "intervallo_stat.json")
# codice di football-data.co.uk -> id del campionato nell'API
CODICI = {"E0": 39, "E1": 40, "SP1": 140, "SP2": 141, "I1": 135, "I2": 136, "D1": 78, "D2": 79,
          "F1": 61, "F2": 62, "N1": 88, "B1": 144, "P1": 94, "T1": 203, "G1": 197, "SC0": 179}
STAGIONI = {"2526": 2025, "2627": 2026}
# le statistiche del primo tempo usate, per la squadra e per l'avversaria
VOCI = ["porta", "tiri", "angoli"]
RICAMPIONAMENTI = 2000
random.seed(7)


def numero(v):
    try:
        x = float((v or "").strip())
        return x if x > 1.0 else None
    except ValueError:
        return None


def leggi_csv(percorso):
    grezzo = open(percorso, "rb").read()
    for cod in ("utf-8-sig", "latin-1"):
        try:
            return list(csv.DictReader(io.StringIO(grezzo.decode(cod))))
        except UnicodeDecodeError:
            pass
    return []


def quote_pinnacle(r):
    """
    Le quote di chiusura senza margine (quasi) da cui ricavare i gol attesi.
    Dal 2026/27 football-data.co.uk non ha piu' Pinnacle: si usa per tutte
    le stagioni l'exchange di Betfair alla chiusura (stesso riferimento per
    imparare e per giudicare), e Pinnacle solo dove Betfair manca.
    """
    for p1, po in (("BFEC", "BFEC"), ("PSC", "PC"), ("BFE", "BFE"), ("PS", "P")):
        q = [numero(r.get(f"{p1}{k}")) for k in "HDA"]
        o, u = numero(r.get(f"{po}>2.5")), numero(r.get(f"{po}<2.5"))
        if all(q) and o and u:
            return dict(zip("HDA", q)), {"O": o, "U": u}
    return None


def gol_attesi(q, ou, memoria):
    chiave = "|".join(str(q[k]) for k in "HDA") + f"|{ou['O']}|{ou['U']}"
    if chiave not in memoria:
        f1 = Q.giusto_potenza(q)
        fo = Q.giusto_potenza(ou)["O"]
        lc, lf, _ = Q.gol_attesi(f1["H"], f1["A"], fo)
        memoria[chiave] = [round(lc, 4), round(lf, 4)]
    return memoria[chiave]


def stat_squadra(d):
    return {k: d.get(k) for k in VOCI + ["possesso", "rossi"]}


def unisci():
    """Le partite dell'archivio con statistiche, unite alle quote di Pinnacle."""
    c = sqlite3.connect(f"file:{ARCHIVIO}?mode=ro", uri=True)
    archivio = {}
    for fid, lega, stagione, data, casa, fuori, hc, hf, fc, ff, pt in c.execute("""
            SELECT p.fid, p.lega, p.stagione, p.data, p.casa, p.fuori, p.ht_c, p.ht_f,
                   p.ft_c, p.ft_f, s.pt FROM partite p JOIN statistiche s ON s.fid = p.fid
            WHERE s.esito = 'ok'"""):
        giorno = data[:10]
        archivio.setdefault((lega, stagione, giorno, hc, hf, fc, ff), []).append(
            {"fid": fid, "casa": casa, "fuori": fuori, "pt": json.loads(pt)})
    c.close()
    percorso_mem = os.path.join(V.CARTELLA, "gol_attesi.json")
    try:
        memoria = json.load(open(percorso_mem, encoding="utf-8"))
    except (OSError, ValueError):
        memoria = {}
    partite, senza = [], 0
    for cod, lega in CODICI.items():
        for s, stagione in STAGIONI.items():
            percorso = os.path.join(V.CARTELLA, f"{s}_{cod}.csv")
            if not os.path.exists(percorso):
                continue
            for r in leggi_csv(percorso):
                r = {(k or "").strip().lstrip("﻿"): v for k, v in r.items()}
                try:
                    gc, ga = int(r["FTHG"]), int(r["FTAG"])
                    hc, hf = int(r["HTHG"]), int(r["HTAG"])
                    giorno = datetime.strptime(r["Date"].strip(), "%d/%m/%Y" if len(r["Date"].strip()) == 10 else "%d/%m/%y").date()
                except (KeyError, ValueError, TypeError):
                    continue
                qp = quote_pinnacle(r)
                if not qp:
                    continue
                cand = []
                for d in (0, 1, -1):
                    cand += archivio.get((lega, stagione, (giorno + timedelta(days=d)).isoformat(), hc, hf, gc, ga), [])
                if not cand:
                    senza += 1
                    continue
                nome = f"{r.get('HomeTeam', '')} {r.get('AwayTeam', '')}".lower()
                m = max(cand, key=lambda x: difflib.SequenceMatcher(None, nome, f"{x['casa']} {x['fuori']}".lower()).ratio())
                lc, lf = gol_attesi(qp[0], qp[1], memoria)
                pt = m["pt"]
                if (pt["casa"].get("rossi") or 0) + (pt["fuori"].get("rossi") or 0) > 0:
                    continue          # dal vivo le partite con un'espulsione si saltano
                if any(pt[x].get(k) is None for x in ("casa", "fuori") for k in VOCI):
                    continue
                partite.append({"fid": m["fid"], "stagione": s, "lc": lc, "lf": lf, "htc": hc, "hta": hf,
                                "gc": gc, "ga": ga, "casa": stat_squadra(pt["casa"]), "fuori": stat_squadra(pt["fuori"])})
    try:
        json.dump(memoria, open(percorso_mem, "w", encoding="utf-8"))
    except OSError:
        pass
    return partite, senza


# ---------------------------------------------------------------
#  il modello con le statistiche
# ---------------------------------------------------------------
# parametri: 0-4 stato della casa, 5-9 stato dell'ospite, 10 ritmo, poi
# per ogni voce statistica: la propria (11+2i) e quella dell'avversaria (12+2i)

def caratteristiche(p, s_c, s_f, chi):
    noi, loro = (p["casa"], p["fuori"]) if chi == "casa" else (p["fuori"], p["casa"])
    x = {}
    for i, k in enumerate(VOCI):
        x[11 + 2 * i] = (noi[k] or 0) / 5.0
        x[12 + 2 * i] = (loro[k] or 0) / 5.0
    return x


def adatta_stat(gruppo):
    s_c = sum(p["htc"] for p in gruppo) / max(1, sum(p["gc"] for p in gruppo))
    s_f = sum(p["hta"] for p in gruppo) / max(1, sum(p["ga"] for p in gruppo))
    oss = []
    for p in gruppo:
        ritmo = (p["htc"] + p["hta"]) - (p["lc"] * s_c + p["lf"] * s_f)
        xc = {T.stato_di(p["htc"] - p["hta"]): 1.0, 10: ritmo}
        xc.update(caratteristiche(p, s_c, s_f, "casa"))
        xf = {5 + T.stato_di(p["hta"] - p["htc"]): 1.0, 10: ritmo}
        xf.update(caratteristiche(p, s_c, s_f, "fuori"))
        oss.append((p["gc"] - p["htc"], math.log(p["lc"] * (1 - s_c)), xc))
        oss.append((p["ga"] - p["hta"], math.log(p["lf"] * (1 - s_f)), xf))
    return {"s_c": s_c, "s_f": s_f, "theta": T.poisson_glm(oss, 11 + 2 * len(VOCI))}


def attesi_stat(p, mod):
    t, s_c, s_f = mod["theta"], mod["s_c"], mod["s_f"]
    ritmo = (p["htc"] + p["hta"]) - (p["lc"] * s_c + p["lf"] * s_f)
    ec = t[T.stato_di(p["htc"] - p["hta"])] + t[10] * ritmo + sum(
        t[i] * v for i, v in caratteristiche(p, s_c, s_f, "casa").items())
    ef = t[5 + T.stato_di(p["hta"] - p["htc"])] + t[10] * ritmo + sum(
        t[i] * v for i, v in caratteristiche(p, s_c, s_f, "fuori").items())
    return p["lc"] * (1 - s_c) * math.exp(ec), p["lf"] * (1 - s_f) * math.exp(ef)


def perdita_poisson(y, mu):
    return mu - y * math.log(max(mu, 1e-9)) + math.lgamma(y + 1)


def forbice(d):
    n = len(d)
    medie = sorted(sum(d[random.randrange(n)] for _ in range(n)) / n for _ in range(RICAMPIONAMENTI))
    return sum(d) / n, medie[int(.025 * RICAMPIONAMENTI)], medie[int(.975 * RICAMPIONAMENTI)]


def main():
    solo_prova = len(sys.argv) > 1 and sys.argv[1] == "prova"
    print("=" * 80)
    print("MODELLO DEL PRIMO TEMPO: LE STATISTICHE AIUTANO A PREVEDERE IL SECONDO?")
    print("=" * 80)
    V.scarica(False)             # aggiorna i file della stagione in corso
    partite, senza = unisci()
    impara = [p for p in partite if p["stagione"] == "2526"]
    giudica = [p for p in partite if p["stagione"] == "2627"]
    print(f"  partite con quote di chiusura e statistiche del primo tempo: {len(partite)}"
          f"  (2025/26 {len(impara)}, 2026/27 {len(giudica)}; non trovate nell'archivio {senza})")
    if len(impara) < 1500 or len(giudica) < 300:
        print("  Troppo poche partite per una prova seria: riprova quando l'archivio e' piu' pieno.")
        return

    base, stat = T.adatta(impara), adatta_stat(impara)
    print("\n  Cosa ha imparato il modello con le statistiche (effetto sui gol del secondo tempo")
    print("  di una squadra, per ogni 5 in piu' nel primo tempo):")
    for i, k in enumerate(VOCI):
        print(f"    {k:<8} suoi {math.exp(stat['theta'][11 + 2 * i]) - 1:+.1%}   "
              f"dell'avversaria {math.exp(stat['theta'][12 + 2 * i]) - 1:+.1%}")

    d_gol, righe = [], {nome: {"b": [], "s": []} for nome, _ in T.MERCATI}
    for p in giudica:
        sc, sf = p["gc"] - p["htc"], p["ga"] - p["hta"]
        ab, bb = T.attesi_st(p, base)
        a_s, b_s = attesi_stat(p, stat)
        d_gol.append(perdita_poisson(sc, ab) + perdita_poisson(sf, bb)
                     - perdita_poisson(sc, a_s) - perdita_poisson(sf, b_s))
        pr_b = T.probabilita(p["htc"], p["hta"], ab, bb)
        pr_s = T.probabilita(p["htc"], p["hta"], a_s, b_s)
        for nome, regola in T.MERCATI:
            if 0.02 <= pr_b[nome] <= 0.98:
                reale = regola(p["gc"], p["ga"], sc, sf)
                righe[nome]["b"].append((pr_b[nome], reale))
                righe[nome]["s"].append((pr_s[nome], reale))
    m, lo, hi = forbice(d_gol)
    print(f"\n  PROVA sul 2026/27 ({len(giudica)} partite mai viste)")
    print(f"  Gol del secondo tempo, miglioramento a partita: {m * 1000:+.1f} millesimi "
          f"[{lo * 1000:+.1f}, {hi * 1000:+.1f}]  (positivo = meglio con le statistiche)")
    print("\n  Mercato per mercato (millesimi di log loss guadagnati, positivo = meglio):")
    for nome, _ in T.MERCATI:
        d = [x - y for x, y in zip(T.perdita(righe[nome]["b"]), T.perdita(righe[nome]["s"]))]
        if len(d) < 100:
            continue
        mm, l2, h2 = forbice(d)
        print(f"    {nome:<32}{len(d):>6}  {mm * 1000:+6.1f}  [{l2 * 1000:+6.1f}, {h2 * 1000:+6.1f}]")
    passa = lo > 0
    print("\n" + "=" * 80)
    print("VERDETTO: " + ("le statistiche del primo tempo migliorano la previsione: il modello passa."
                          if passa else "nessun miglioramento dimostrato: si resta col modello attuale."))
    if passa and not solo_prova:
        finale = adatta_stat(partite)
        with open(USCITA, "w", encoding="utf-8") as f:
            json.dump({"creato": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                       "partite": len(partite), "voci": VOCI,
                       "prova": {"partite": len(giudica), "millesimi": round(m * 1000, 2),
                                 "forbice": [round(lo * 1000, 2), round(hi * 1000, 2)]},
                       "quota_pt_casa": round(finale["s_c"], 4), "quota_pt_ospite": round(finale["s_f"], 4),
                       "theta": [round(x, 5) for x in finale["theta"]]}, f, indent=1)
        print(f"Salvato in {USCITA}: l'analisi dell'intervallo lo usa dal prossimo giro.")
    elif not passa and not solo_prova and os.path.exists(USCITA):
        print(f"(Il {USCITA} di prima resta finche' una prova nuova non dice altro.)")


if __name__ == "__main__":
    main()
