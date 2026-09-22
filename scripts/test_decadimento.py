"""
QUANTO DEVONO PESARE LE PARTITE VECCHIE
=======================================
La forza di una squadra e' una media pesata delle sue partite precedenti:
ognuna pesa l'85% della successiva (DECADIMENTO = 0.85), e non si
distingue fra questa stagione e la scorsa.

A inizio stagione e' un problema: le squadre sono cambiate col mercato
estivo, ma buona parte del nostro storico e' della stagione precedente.
La verifica dal vivo, fatta tutta sulle prime giornate, dice che il
mercato ci batte. Qui si prova se pesare diversamente il passato aiuta.

DUE PARAMETRI
-------------
    decadimento      quanto pesa ogni partita rispetto alla successiva
    peso stagione    moltiplicatore per le partite della stagione scorsa
                     (1 = come oggi, 0.5 = contano la meta')

METODO
------
La forza si ricalcola partita per partita con la stessa normalizzazione
di produzione (nucleo.normalizza), cambiando solo i pesi. Il modello e'
quello di base: Poisson con Dixon-Coles, shrinkage, xG pesato. I sei
indicatori aggiuntivi restano fuori, per isolare l'effetto dei pesi.

Le partite si dividono ALTERNANDO LE SETTIMANE: quelle pari servono a
scegliere la combinazione migliore, quelle dispari - mai usate per
scegliere - a misurarla. Tagliare per data non andrebbe bene qui: un
blocco potrebbe cadere tutto a meta' stagione, senza nessun cambio di
stagione su cui scegliere il peso. Alternando, entrambi i gruppi coprono
tutto il periodo. Resta onesto perche' la forza di ogni partita si
calcola comunque solo con le partite precedenti. Un bootstrap appaiato
dice se il guadagno e' reale.

Si misurano a parte:
  - le partite di INIZIO STAGIONE, dove almeno una squadra ha meno di
    5 partite della stagione in corso: e' li' che ci aspettiamo l'effetto
  - le partite VERIFICATE DAL VIVO con quote: di quanto si accorcia la
    distanza dal mercato

Il database non viene modificato. Se il risultato e' buono, il valore
nuovo va applicato in nucleo.py e il modello riaddestrato.

USO:
    python scripts/test_decadimento.py
"""

import os
import sys
import math
import random
import sqlite3
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nucleo import normalizza, carica_conservazione

DB_PATH = "calcio_dati.db"
SEED = 808
N_BOOTSTRAP = 4000
K, PESO_XG, RHO = 8.0, 0.75, -0.05
MIN_PARTITE = 3
MAX_GOL = 8
INIZIO_STAGIONE = 5

DECADIMENTI = [0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
PESI_STAGIONE = [1.0, 0.7, 0.5, 0.3]
ATTUALE = (0.85, 1.0)


# ---------------------------------------------------------------
#  dati
# ---------------------------------------------------------------

def carica(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT fixture_id, team_id, opponent_id, league_id, season, date, is_home,
               goals_for, goals_against, xg_finale
        FROM team_match ORDER BY date
    """)
    campi = [d[0] for d in cur.description]
    righe = [dict(zip(campi, r)) for r in cur.fetchall()]
    xg = {(r["fixture_id"], r["team_id"]): r["xg_finale"] for r in righe}
    for r in righe:
        r["xg_against"] = xg.get((r["fixture_id"], r["opponent_id"]))
    return righe


def medie_campionato(righe):
    """Le stesse due medie di indicatori.py: complessiva per campionato e
    stagione, ed espansiva (solo passato) per il campionato attuale."""
    somme = {}
    for r in righe:
        s = somme.setdefault((r["league_id"], r["season"]), [0.0, 0, 0.0, 0])
        if r["goals_for"] is not None:
            s[0] += r["goals_for"]; s[1] += 1
        if r["xg_finale"] is not None:
            s[2] += r["xg_finale"]; s[3] += 1
    media_lega = {k: (s[0] / s[1] if s[1] >= 20 else None,
                      s[2] / s[3] if s[3] >= 20 else None) for k, s in somme.items()}

    per_lega = {}
    for r in righe:
        per_lega.setdefault(r["league_id"], []).append(r)
    prima_di = {}
    for lista in per_lega.values():
        lista.sort(key=lambda x: x["date"])
        sg = ng = sx = nx = 0.0
        data_prec, buffer = None, []
        for r in lista:
            if data_prec is not None and r["date"] != data_prec:
                for b in buffer:
                    if b["goals_for"] is not None:
                        sg += b["goals_for"]; ng += 1
                    if b["xg_finale"] is not None:
                        sx += b["xg_finale"]; nx += 1
                buffer = []
            prima_di[(r["fixture_id"], r["team_id"])] = (
                sg / ng if ng >= 20 else None, sx / nx if nx >= 20 else None)
            buffer.append(r)
            data_prec = r["date"]
    return media_lega, prima_di


def storici(righe, media_lega, prima_di, conservazione):
    """
    Per ogni squadra in ogni partita: i valori normalizzati delle partite
    precedenti (dalla piu' recente) e se ognuna e' della stagione in corso.
    Non dipendono dai pesi, quindi si calcolano una volta sola.
    """
    per_squadra = {}
    for r in righe:
        per_squadra.setdefault(r["team_id"], []).append(r)
    fuori = {}
    for storia in per_squadra.values():
        storia.sort(key=lambda x: x["date"])
        for i, r in enumerate(storia):
            prec = [p for p in storia[:i] if p["date"] < r["date"]][::-1]
            lega_ora = r["league_id"]
            mg, mx = prima_di.get((r["fixture_id"], r["team_id"]), (None, None))

            def media_di(lega, stagione, quale, lega_ora=lega_ora, mg=mg, mx=mx):
                if lega == lega_ora:
                    return mg if quale == 0 else mx
                return media_lega.get((lega, stagione), (None, None))[quale]

            def norm(campo, quale, tipo):
                return [normalizza(p, campo, quale, tipo, lega_ora, media_di, conservazione)
                        for p in prec]

            stessa = [p["season"] == r["season"] for p in prec]
            fuori[(r["fixture_id"], r["team_id"])] = {
                "n": len(prec), "stessa": stessa, "in_stagione": sum(stessa),
                "ag": norm("goals_for", 0, "attacco"), "dg": norm("goals_against", 0, "difesa"),
                "ax": norm("xg_finale", 1, "attacco"), "dx": norm("xg_against", 1, "difesa")}
    return fuori


def pesata(valori, stessa, potenze, peso_stagione):
    num = den = 0.0
    for k, v in enumerate(valori):
        if v is None:
            continue
        w = potenze[k] * (1.0 if stessa[k] else peso_stagione)
        num += w * v
        den += w
    return num / den if den > 0 else None


# ---------------------------------------------------------------
#  modello
# ---------------------------------------------------------------

def pmf(lam):
    return [math.exp(-lam) * lam ** k / math.factorial(k) for k in range(MAX_GOL + 1)]


def prob_1x2(lc, lf):
    a, b = pmf(lc), pmf(lf)
    p = [0.0, 0.0, 0.0]
    for i in range(MAX_GOL + 1):
        for j in range(MAX_GOL + 1):
            v = a[i] * b[j]
            if i == 0 and j == 0:
                v *= 1 - lc * lf * RHO
            elif i == 0 and j == 1:
                v *= 1 + lc * RHO
            elif i == 1 and j == 0:
                v *= 1 + lf * RHO
            elif i == 1 and j == 1:
                v *= 1 - RHO
            p[0 if i > j else (1 if i == j else 2)] += v
    s = sum(p)
    return [x / s for x in p]


def perdite(partite, st, media_gol, fattore, decad, peso_stag):
    potenze = [decad ** k for k in range(200)]

    def forza(s):
        n = s["n"]
        vals = {c: pesata(s[c], s["stessa"], potenze, peso_stag) for c in ("ag", "dg", "ax", "dx")}
        if None in vals.values():
            return None

        def r(v):
            return 1.0 + (v - 1.0) * n / (n + K)
        return (r(PESO_XG * vals["ax"] + (1 - PESO_XG) * vals["ag"]),
                r(PESO_XG * vals["dx"] + (1 - PESO_XG) * vals["dg"]))

    fuori = []
    radice = math.sqrt(fattore)
    for m in partite:
        c, f = forza(st[m["casa"]]), forza(st[m["fuori"]])
        if c is None or f is None:
            fuori.append(None)
            continue
        lc = max(0.15, min(5.0, media_gol * c[0] * f[1] * radice))
        lf = max(0.15, min(5.0, media_gol * f[0] * c[1] / radice))
        fuori.append(-math.log(max(prob_1x2(lc, lf)[m["esito"]], 1e-15)))
    return fuori


def media(v):
    v = [x for x in v if x is not None]
    return sum(v) / len(v) if v else float("nan")


def confronto(a, b):
    """Media di (a - b) sulle stesse partite, con intervallo al 95%."""
    d = [x - y for x, y in zip(a, b) if x is not None and y is not None]
    if len(d) < 20:
        return None
    random.seed(SEED)
    n = len(d)
    medie = sorted(sum(d[random.randrange(n)] for _ in range(n)) / n
                   for _ in range(N_BOOTSTRAP))
    return len(d), sum(d) / n, medie[int(.025 * N_BOOTSTRAP)], medie[int(.975 * N_BOOTSTRAP)]


def esito_testo(c):
    _, m, lo, hi = c
    if lo > 0:
        return "MIGLIORAMENTO DIMOSTRATO"
    if hi < 0:
        return "PEGGIORAMENTO DIMOSTRATO"
    return "non dimostrato" + (" (tendenza favorevole)" if m > 0 else "")


# ---------------------------------------------------------------

def main():
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} non trovato.")
        return
    conn = sqlite3.connect(DB_PATH)
    print("Carico le partite e ricalcolo gli storici (un paio di minuti)...")
    righe = carica(conn)
    media_lega, prima_di = medie_campionato(righe)
    st = storici(righe, media_lega, prima_di, carica_conservazione())

    # partite complete: entrambe le squadre con abbastanza storico
    per_partita = {}
    for r in righe:
        per_partita.setdefault(r["fixture_id"], []).append(r)
    partite = []
    for fid, coppia in per_partita.items():
        casa = next((x for x in coppia if x["is_home"] == 1), None)
        fuori = next((x for x in coppia if x["is_home"] == 0), None)
        if not casa or not fuori or casa["goals_for"] is None or fuori["goals_for"] is None:
            continue
        kc, kf = (fid, casa["team_id"]), (fid, fuori["team_id"])
        if st[kc]["n"] < MIN_PARTITE or st[kf]["n"] < MIN_PARTITE:
            continue
        gc, gf = casa["goals_for"], fuori["goals_for"]
        partite.append({"fid": fid, "data": casa["date"], "casa": kc, "fuori": kf,
                        "gc": gc, "gf": gf, "esito": 0 if gc > gf else (1 if gc == gf else 2),
                        "inizio": min(st[kc]["in_stagione"], st[kf]["in_stagione"]) < INIZIO_STAGIONE})
    partite.sort(key=lambda m: m["data"])

    def settimana(m):
        return datetime.fromisoformat(m["data"]).isocalendar()[1]
    val = [m for m in partite if settimana(m) % 2 == 0]
    test = [m for m in partite if settimana(m) % 2 == 1]
    media_gol = sum(m["gc"] + m["gf"] for m in val) / (2 * len(val))
    fattore = sum(m["gc"] for m in val) / max(1, sum(m["gf"] for m in val))
    print(f"Partite utilizzabili: {len(partite)}, dal {partite[0]['data'][:10]} "
          f"al {partite[-1]['data'][:10]}")
    print(f"  settimane pari (per scegliere): {len(val)}, di cui "
          f"{sum(m['inizio'] for m in val)} di inizio stagione")
    print(f"  settimane dispari (per misurare): {len(test)}, di cui "
          f"{sum(m['inizio'] for m in test)} di inizio stagione")

    # controllo: con i valori attuali il ricalcolo deve riprodurre il database
    try:
        salvati = {(r[0], r[1]): r[2] for r in conn.execute(
            "SELECT fixture_id, team_id, att_xg FROM features WHERE att_xg IS NOT NULL")}
        potenze = [ATTUALE[0] ** k for k in range(200)]
        diff = []
        for chiave, v in salvati.items():
            if chiave in st:
                mio = pesata(st[chiave]["ax"], st[chiave]["stessa"], potenze, 1.0)
                if mio is not None:
                    diff.append(abs(mio - v))
        diff.sort()
        if diff:
            print(f"  controllo del ricalcolo su {len(diff)} valori: differenza "
                  f"mediana {diff[len(diff) // 2]:.5f}, massima {diff[-1]:.4f}")
    except sqlite3.OperationalError:
        print("  (tabella features assente: controllo del ricalcolo saltato)")

    # ---- scelta sulla validazione ------------------------------------
    print("\n" + "=" * 70)
    print("LOG LOSS SULLE SETTIMANE PARI (qui si sceglie)")
    print("=" * 70)
    print(f"{'decadimento':<13}" + "".join(f"{'stag.' + str(s):>11}" for s in PESI_STAGIONE))
    risultati = {}
    for d in DECADIMENTI:
        riga = f"  {d:<11.2f}"
        for s in PESI_STAGIONE:
            v = media(perdite(val, st, media_gol, fattore, d, s))
            risultati[(d, s)] = v
            marca = "*" if (d, s) == ATTUALE else " "
            riga += f"{v:>10.4f}{marca}"
        print(riga)
    print("  (* = valori attuali)")
    scelta = min(risultati, key=risultati.get)
    if risultati[scelta] > risultati[ATTUALE] - 1e-6:
        scelta = ATTUALE          # a parita' si tiene quello che c'e'
    print(f"\nMigliore sulle settimane pari: decadimento {scelta[0]:.2f}, "
          f"peso stagione scorsa {scelta[1]:.1f}")

    if scelta == ATTUALE:
        print("\nLa combinazione migliore e' quella attuale: non c'e' niente da cambiare.")
        return

    # ---- misura sul test -----------------------------------------------
    print("\n" + "=" * 70)
    print("MISURA SULLE SETTIMANE DISPARI (mai usate per scegliere)")
    print("=" * 70)
    pa = perdite(test, st, media_gol, fattore, *ATTUALE)
    pn = perdite(test, st, media_gol, fattore, *scelta)
    print("Guadagno = log loss attuale meno nuovo: positivo = il nuovo e' meglio\n")
    for nome, filtro in (("tutte", lambda m: True),
                         ("inizio stagione", lambda m: m["inizio"]),
                         ("resto della stagione", lambda m: not m["inizio"])):
        idx = [i for i, m in enumerate(test) if filtro(m)]
        c = confronto([pa[i] for i in idx], [pn[i] for i in idx])
        if not c:
            print(f"  {nome:<22} troppo poche partite")
            continue
        n, m, lo, hi = c
        print(f"  {nome:<22}{n:>6} partite  attuale {media([pa[i] for i in idx]):.4f}"
              f"  nuovo {media([pn[i] for i in idx]):.4f}  guadagno {m:+.4f} "
              f"[{lo:+.4f}, {hi:+.4f}]  {esito_testo(c)}")

    inizio = [m for m in test if m["inizio"]]
    if len(inizio) >= 30:
        print(f"\n  Solo a inizio stagione, decadimento {scelta[0]:.2f}, al variare del peso")
        print("  della stagione scorsa (indicativo, per vedere la tendenza):")
        print("    " + "   ".join(f"{s:.1f}: {media(perdite(inizio, st, media_gol, fattore, scelta[0], s)):.4f}"
                                  for s in PESI_STAGIONE))

    # ---- partite verificate dal vivo -------------------------------------
    print("\n" + "=" * 70)
    print("PARTITE VERIFICATE DAL VIVO: LA DISTANZA DAL MERCATO")
    print("=" * 70)
    try:
        quote = {r[0]: r[1:] for r in conn.execute(
            "SELECT fixture_id, q1, qx, q2 FROM archivio_previsioni WHERE q1 IS NOT NULL")}
    except sqlite3.OperationalError:
        quote = {}
    vive = [m for m in partite if m["fid"] in quote]
    if len(vive) < 30:
        print(f"Solo {len(vive)} partite verificate con quote nel campione: troppo poche.")
    else:
        pa = perdite(vive, st, media_gol, fattore, *ATTUALE)
        pn = perdite(vive, st, media_gol, fattore, *scelta)
        pm = [-math.log(max(quote[m["fid"]][m["esito"]], 1e-15)) for m in vive]
        print(f"{len(vive)} partite. Modello di base, senza i sei indicatori:")
        print(f"  attuale  {media(pa):.4f}   distanza dal mercato {media(pa) - media(pm):+.4f}")
        print(f"  nuovo    {media(pn):.4f}   distanza dal mercato {media(pn) - media(pm):+.4f}")
        print(f"  mercato  {media(pm):.4f}")
        c = confronto(pa, pn)
        if c:
            print(f"  guadagno del nuovo: {c[1]:+.4f} [{c[2]:+.4f}, {c[3]:+.4f}]  {esito_testo(c)}")

    print("\nCOME LEGGERE: conviene applicare il valore nuovo solo se il test mostra")
    print("un miglioramento dimostrato, o almeno una tendenza favorevole chiara che")
    print("si ritrova anche nelle partite verificate dal vivo.")
    conn.close()


if __name__ == "__main__":
    main()
