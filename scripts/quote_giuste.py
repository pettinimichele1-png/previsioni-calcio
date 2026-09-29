#!/usr/bin/env python3
"""
LE QUOTE GIUSTE DI OGNI MERCATO SUI GOL, RICAVATE DA PINNACLE

Pinnacle quota con grande precisione due cose: l'esito finale e
l'Over/Under 2.5. Da quelle due quote si ricavano i gol attesi di casa
e ospite, e dai gol attesi la probabilita' di qualunque mercato sui gol:
multigol, gol di una squadra, combo, handicap, primo e secondo tempo.

Il passaggio non e' perfetto: in alcuni mercati (Gol, Ospite segna,
Casa vince di 2+...) la realta' si discosta in modo sistematico. La
correzione, misurata su 35.000 partite da calibra_mercati.py, sta in
stato/calibrazione_mercati.json e si applica con correggi().

Questo file non si lancia: lo usano calibra_mercati.py e valore.py.
Le regole dei mercati sono quelle del motore (esito_avvenuto in
previsioni.py): calcolo e verifica restano la stessa riga di codice.
"""

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from previsioni import TUTTI_I_MERCATI, NOMI as NOMI_MOTORE, esito_avvenuto

N = 10                      # gol massimi per squadra nella griglia
RHO = -0.05                 # correzione dei punteggi bassi, come nel motore
QUOTA_PRIMO_TEMPO = 0.443   # parte dei gol nel primo tempo; calibra_mercati la misura
FILE_CALIBRAZIONE = os.path.join("stato", "calibrazione_mercati.json")

# Mercati che Pinnacle quota direttamente: la probabilita' e' la sua.
DIRETTI = {"1", "X", "2", "1X", "12", "X2", "over25", "under25"}

# Primo tempo (_pt), secondo tempo (_st) e mercati sui due tempi insieme.
BASE_PT = ["1", "X", "2", "1X", "12", "X2", "over05", "under05",
           "over15", "under15", "over25", "under25",
           "gol_gol", "no_gol", "casa_segna", "fuori_segna"]
BASE_ST = list(BASE_PT)
SPECIALI = ["gol_entrambi_tempi", "casa_segna_entrambi", "fuori_segna_entrambi",
            "pt_piu_gol", "st_piu_gol", "tempi_pari_gol"]
TEMPI = [k + "_pt" for k in BASE_PT] + [k + "_st" for k in BASE_ST] + SPECIALI

MERCATI = list(TUTTI_I_MERCATI) + TEMPI

NOMI_TEMPI = {
    "gol_entrambi_tempi": "Gol in entrambi i tempi",
    "casa_segna_entrambi": "Casa segna in entrambi i tempi",
    "fuori_segna_entrambi": "Ospite segna in entrambi i tempi",
    "pt_piu_gol": "Primo tempo con piu' gol",
    "st_piu_gol": "Secondo tempo con piu' gol",
    "tempi_pari_gol": "Stessi gol nei due tempi",
}
for _k in BASE_PT:
    NOMI_TEMPI[_k + "_pt"] = NOMI_MOTORE[_k] + " primo tempo"
for _k in BASE_ST:
    NOMI_TEMPI[_k + "_st"] = NOMI_MOTORE[_k] + " secondo tempo"


def nome(k):
    return NOMI_MOTORE.get(k) or NOMI_TEMPI.get(k) or k


# ---------------------------------------------------------------
#  dalle quote alle probabilita' giuste
# ---------------------------------------------------------------

def giusto_potenza(quote):
    """
    Toglie il margine del bookmaker. Si cerca l'esponente k per cui le
    probabilita' implicite elevate alla k sommano a uno: cosi' il
    margine pesa di piu' sulle quote alte. E' il metodo che sui nostri
    dati si comporta meglio (test_valore.py).
    """
    grezze = {k: 1.0 / q for k, q in quote.items()}
    lo, hi = 1.0, 4.0
    for _ in range(45):
        e = (lo + hi) / 2
        if sum(v ** e for v in grezze.values()) > 1.0:
            lo = e
        else:
            hi = e
    e = (lo + hi) / 2
    p = {k: v ** e for k, v in grezze.items()}
    s = sum(p.values())
    return {k: v / s for k, v in p.items()}


def poisson_lista(lam, n=N):
    p = [math.exp(-lam)]
    for k in range(1, n):
        p.append(p[-1] * lam / k)
    return p


def matrice(lc, lf, rho=RHO):
    pc, pf = poisson_lista(lc), poisson_lista(lf)
    M = [[a * b for b in pf] for a in pc]
    if rho:
        M[0][0] *= max(1 - lc * lf * rho, 0.01)
        M[0][1] *= max(1 + lc * rho, 0.01)
        M[1][0] *= max(1 + lf * rho, 0.01)
        M[1][1] *= max(1 - rho, 0.01)
    s = sum(map(sum, M))
    return [[v / s for v in r] for r in M]


_IDX_1 = [i * N + j for i in range(N) for j in range(N) if i > j]
_IDX_2 = [i * N + j for i in range(N) for j in range(N) if i < j]
# caselle dell'Over: almeno 2 gol per l'Over 1.5, 3 per il 2.5, 4 per il 3.5
_IDX_O = {g: [i * N + j for i in range(N) for j in range(N) if i + j >= g]
          for g in (1, 2, 3, 4, 5)}


def _principali(M, gol_over=3):
    v = [x for r in M for x in r]
    return (sum(v[i] for i in _IDX_1), sum(v[i] for i in _IDX_2),
            sum(v[i] for i in _IDX_O[gol_over]))


def gol_attesi(p1, p2, po, linea=2.5):
    """
    I gol attesi di casa e ospite che riproducono le probabilita' giuste
    di 1, 2 e Over (di solito il 2.5; se Pinnacle non lo quota, un'altra
    linea). Si parte da una stima ragionevole e si aggiusta a passi
    sempre piu' piccoli. Ritorna anche lo scarto rimasto.
    """
    g_over = int(linea) + 1
    lo, hi = 0.3, 6.0
    for _ in range(30):
        t = (lo + hi) / 2
        sotto = sum(poisson_lista(t, g_over))
        if 1 - sotto < po:
            lo = t
        else:
            hi = t
    tot = (lo + hi) / 2
    sup = (p1 - p2) * 2.4
    lc, lf = max((tot + sup) / 2, 0.1), max((tot - sup) / 2, 0.1)

    def errore(a, b):
        q1, q2, qo = _principali(matrice(a, b), g_over)
        return (q1 - p1) ** 2 + (q2 - p2) ** 2 + (qo - po) ** 2

    best = errore(lc, lf)
    passo = 0.2
    while passo > 0.002:
        migliorato = False
        for dc, df in ((passo, 0), (-passo, 0), (0, passo), (0, -passo),
                       (passo, -passo), (-passo, passo)):
            a, b = max(lc + dc, 0.05), max(lf + df, 0.05)
            e = errore(a, b)
            if e < best:
                lc, lf, best, migliorato = a, b, e, True
                break
        if not migliorato:
            passo /= 2
    return lc, lf, math.sqrt(best)


# ---------------------------------------------------------------
#  da gol attesi a tutti i mercati
# ---------------------------------------------------------------

def _caselle(chiavi):
    return {k: [x * N + y for x in range(N) for y in range(N)
                if esito_avvenuto(k, x, y)] for k in chiavi}


CASELLE = _caselle(TUTTI_I_MERCATI)
CASELLE_TEMPO = _caselle(sorted(set(BASE_PT) | set(BASE_ST)))


def _griglia(a, b):
    pa, pb = poisson_lista(a), poisson_lista(b)
    v = [x * y for x in pa for y in pb]
    s = sum(v)
    return [x / s for x in v]


def tempi(lc, lf, s):
    """I mercati del primo e secondo tempo: i due tempi come due partite
    brevi e indipendenti, con la parte s dei gol nel primo."""
    a1, b1, a2, b2 = lc * s, lf * s, lc * (1 - s), lf * (1 - s)
    h, t = _griglia(a1, b1), _griglia(a2, b2)
    out = {k + "_pt": sum(h[i] for i in CASELLE_TEMPO[k]) for k in BASE_PT}
    out.update({k + "_st": sum(t[i] for i in CASELLE_TEMPO[k]) for k in BASE_ST})
    t1, t2 = poisson_lista(a1 + b1), poisson_lista(a2 + b2)
    out["gol_entrambi_tempi"] = (1 - t1[0]) * (1 - t2[0])
    out["casa_segna_entrambi"] = (1 - math.exp(-a1)) * (1 - math.exp(-a2))
    out["fuori_segna_entrambi"] = (1 - math.exp(-b1)) * (1 - math.exp(-b2))
    primo = sum(t1[i] * t2[j] for i in range(N) for j in range(i))
    pari = sum(t1[i] * t2[i] for i in range(N))
    tot = sum(t1) * sum(t2)
    out["pt_piu_gol"] = primo / tot
    out["tempi_pari_gol"] = pari / tot
    out["st_piu_gol"] = 1 - (primo + pari) / tot
    return out


def tutte(p1, px, p2, po, gol=None, quota_pt=None, linea=2.5):
    """
    Dalle probabilita' giuste di Pinnacle (1, X, 2, Over della linea
    indicata, di solito 2.5) a quelle di tutti i mercati, non ancora
    corrette. Ritorna anche i gol attesi.
    """
    if gol is None:
        lc, lf, _ = gol_attesi(p1, p2, po, linea)
    else:
        lc, lf = gol
    v = [x for r in matrice(lc, lf) for x in r]
    prob = {k: sum(v[i] for i in idx) for k, idx in CASELLE.items()}
    # dove Pinnacle quota il mercato, vale la sua probabilita'
    prob.update({"1": p1, "X": px, "2": p2,
                 "1X": p1 + px, "12": p1 + p2, "X2": px + p2,
                 f"over{int(linea)}5": po, f"under{int(linea)}5": 1 - po})
    prob.update(tempi(lc, lf, QUOTA_PRIMO_TEMPO if quota_pt is None else quota_pt))
    return prob, (lc, lf)


# ---------------------------------------------------------------
#  verifica a partita finita
# ---------------------------------------------------------------

def esito_tempi(k, gc, ga, htc, hta):
    if htc is None or hta is None:
        return None
    sc, sa = gc - htc, ga - hta
    if sc < 0 or sa < 0:
        return None
    speciali = {
        "gol_entrambi_tempi": htc + hta > 0 and sc + sa > 0,
        "casa_segna_entrambi": htc > 0 and sc > 0,
        "fuori_segna_entrambi": hta > 0 and sa > 0,
        "pt_piu_gol": htc + hta > sc + sa,
        "st_piu_gol": sc + sa > htc + hta,
        "tempi_pari_gol": htc + hta == sc + sa,
    }
    if k in speciali:
        return speciali[k]
    if k.endswith("_pt"):
        return esito_avvenuto(k[:-3], htc, hta)
    if k.endswith("_st"):
        return esito_avvenuto(k[:-3], sc, sa)
    return None


def esito(k, gc, ga, htc=None, hta=None):
    """Se il mercato k si e' avverato. None se non si puo' dire."""
    if k in NOMI_TEMPI:
        return esito_tempi(k, gc, ga, htc, hta)
    return esito_avvenuto(k, gc, ga)


# ---------------------------------------------------------------
#  la correzione
# ---------------------------------------------------------------

def logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def sigmoide(x):
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    e = math.exp(x)
    return e / (1 + e)


def carica_calibrazione(percorso=FILE_CALIBRAZIONE):
    try:
        with open(percorso, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def correggi(prob, cal):
    """Applica la correzione misurata da calibra_mercati.py."""
    if not cal:
        return dict(prob)
    tab = cal.get("mercati", {})
    out = {}
    for k, p in prob.items():
        c = tab.get(k)
        out[k] = sigmoide(c["a"] + c["b"] * logit(p)) if c else p
    return out


def quota_minima(p, margine):
    """La quota sotto cui non si gioca: quella giusta piu' il margine."""
    return math.ceil((1 + margine) / p * 100 - 1e-9) / 100
