#!/usr/bin/env python3
"""
LE QUOTE GIUSTE DI TUTTI I MERCATI SUI GOL: SONO ONESTE?

Per dare una quota minima su ogni mercato - multigol, gol di una
squadra, combo, risultato esatto, primo tempo - non uso il nostro
modello, che sbaglia piu' del mercato. Parto dalle quote di Pinnacle su
esito finale e Over/Under 2.5, le piu' precise che esistano, e ricavo i
gol attesi di casa e ospite che le riproducono. Da quei gol attesi si
calcola qualunque mercato sui gol.

Il rischio e' che il passaggio dai mercati principali a quelli derivati
introduca errori: il modello di Poisson e' una semplificazione. Questo
test lo controlla su tutte le partite gia' scaricate: per ogni mercato,
quanto dicevamo che succedeva e quanto e' successo davvero.

Un mercato e' affidabile se, in ogni fascia di probabilita', lo scarto
fra previsto e reale sta dentro il margine del caso. Solo su quelli la
quota minima si puo' usare.

Uso, dalla cartella del progetto (usa i file di test_valore.py):
    python3 scripts/test_derivati.py
"""

import csv
import io
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_valore as V

RHO = -0.05             # correzione dei punteggi bassi, come nel motore
N = 10                  # gol massimi per squadra nella griglia
QUOTA_PRIMO_TEMPO = 0.443   # parte dei gol che cade nel primo tempo (misurata)

FASCE = [(0.0, 0.10), (0.10, 0.20), (0.20, 0.35), (0.35, 0.50),
         (0.50, 0.65), (0.65, 0.80), (0.80, 0.90), (0.90, 1.0001)]

# ogni mercato e' una regola sul risultato finale (gc, ga)
MERCATI = [
    ("Over 1.5", lambda x, y: x + y >= 2),
    ("Over 3.5", lambda x, y: x + y >= 4),
    ("Gol (segnano entrambe)", lambda x, y: x > 0 and y > 0),
    ("Multigol 1-3", lambda x, y: 1 <= x + y <= 3),
    ("Multigol 2-4", lambda x, y: 2 <= x + y <= 4),
    ("Casa segna", lambda x, y: x > 0),
    ("Ospite segna", lambda x, y: y > 0),
    ("Casa Over 1.5", lambda x, y: x >= 2),
    ("Ospite Over 1.5", lambda x, y: y >= 2),
    ("Casa vince di 2+", lambda x, y: x - y >= 2),
    ("1X + Over 1.5", lambda x, y: x >= y and x + y >= 2),
    ("X2 + Under 3.5", lambda x, y: x <= y and x + y <= 3),
    ("Risultato 1-1", lambda x, y: x == 1 and y == 1),
    ("Risultato 0-0", lambda x, y: x == 0 and y == 0),
    ("Risultato 1-0", lambda x, y: x == 1 and y == 0),
    ("Risultato 2-1", lambda x, y: x == 2 and y == 1),
]
MERCATI_PT = [
    ("Over 0.5 primo tempo", lambda x, y: x + y >= 1),
    ("Over 1.5 primo tempo", lambda x, y: x + y >= 2),
]


# ---------------------------------------------------------------
#  dalla quota ai gol attesi
# ---------------------------------------------------------------

def poisson(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def matrice(lc, lf, rho=RHO):
    pc = [poisson(i, lc) for i in range(N)]
    pf = [poisson(j, lf) for j in range(N)]
    M = [[pc[i] * pf[j] for j in range(N)] for i in range(N)]
    if rho:
        M[0][0] *= max(1 - lc * lf * rho, 0.01)
        M[0][1] *= max(1 + lc * rho, 0.01)
        M[1][0] *= max(1 + lf * rho, 0.01)
        M[1][1] *= max(1 - rho, 0.01)
    s = sum(map(sum, M))
    return [[v / s for v in r] for r in M]


def principali(M):
    p1 = sum(M[i][j] for i in range(N) for j in range(N) if i > j)
    p2 = sum(M[i][j] for i in range(N) for j in range(N) if i < j)
    po = sum(M[i][j] for i in range(N) for j in range(N) if i + j >= 3)
    return p1, p2, po


def gol_attesi(t1, t2, to=None):
    """
    I gol attesi che riproducono le probabilita' del mercato. Si parte
    da una stima ragionevole e si aggiusta a passi sempre piu' piccoli.
    """
    tot = 2.6
    if to is not None:
        lo, hi = 0.3, 6.0
        for _ in range(30):
            t = (lo + hi) / 2
            sotto = sum(poisson(k, t) for k in range(3))
            if 1 - sotto < to:
                lo = t
            else:
                hi = t
        tot = (lo + hi) / 2
    sup = (t1 - t2) * 2.4
    lc, lf = max((tot + sup) / 2, 0.1), max((tot - sup) / 2, 0.1)

    def errore(a, b):
        p1, p2, po = principali(matrice(a, b))
        e = (p1 - t1) ** 2 + (p2 - t2) ** 2
        if to is not None:
            e += (po - to) ** 2
        return e

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
#  lettura
# ---------------------------------------------------------------

def num(riga, k):
    try:
        v = float((riga.get(k) or "").strip())
        return v
    except ValueError:
        return None


def leggi():
    partite = []
    for s in V.STAGIONI:
        for l in V.LEGHE:
            percorso = os.path.join(V.CARTELLA, f"{s}_{l}.csv")
            if not os.path.exists(percorso):
                continue
            grezzo = open(percorso, "rb").read()
            testo = None
            for cod in ("utf-8-sig", "latin-1"):
                try:
                    testo = grezzo.decode(cod)
                    break
                except UnicodeDecodeError:
                    pass
            if not testo:
                continue
            for r in csv.DictReader(io.StringIO(testo)):
                r = {(k or "").strip().lstrip("﻿"): v for k, v in r.items()}
                gc, ga = num(r, "FTHG"), num(r, "FTAG")
                if gc is None or ga is None:
                    continue
                # prima la chiusura, se manca la quota del venerdi'
                q = [num(r, f"PSC{k}") for k in "HDA"]
                if not all(x and x > 1 for x in q):
                    q = [num(r, f"PS{k}") for k in "HDA"]
                if not all(x and x > 1 for x in q):
                    continue
                o, u = num(r, "PC>2.5"), num(r, "PC<2.5")
                if not (o and u and o > 1 and u > 1):
                    o, u = num(r, "P>2.5"), num(r, "P<2.5")
                ou = {"O": o, "U": u} if (o and u and o > 1 and u > 1) else None
                partite.append({
                    "stagione": s, "gc": int(gc), "ga": int(ga),
                    "htc": num(r, "HTHG"), "hta": num(r, "HTAG"),
                    "q": dict(zip("HDA", q)), "ou": ou})
    return partite


# ---------------------------------------------------------------

def tabella(nome, righe):
    """righe: (probabilita' prevista, avvenuto). Ritorna True se onesto."""
    onesto = True
    dettagli = []
    for lo, hi in FASCE:
        g = [r for r in righe if lo <= r[0] < hi]
        if len(g) < 200:
            continue
        prev = sum(r[0] for r in g) / len(g)
        reale = sum(1 for r in g if r[1]) / len(g)
        tol = 2.5 * math.sqrt(max(prev * (1 - prev), 1e-9) / len(g)) + 0.005
        ok = abs(prev - reale) <= tol
        onesto &= ok
        dettagli.append(f"      {lo:>4.0%}-{min(hi, 1):>4.0%}  {len(g):>6}  "
                        f"previsto {prev:>6.1%}  reale {reale:>6.1%}  "
                        f"{'ok' if ok else '!! scarto ' + format(reale - prev, '+.1%')}")
    stato = "AFFIDABILE" if onesto else "DA CORREGGERE"
    print(f"  {nome:<26} {stato}")
    for d in dettagli:
        print(d)
    return onesto


def main():
    print("=" * 84)
    print("0. DATI")
    print("=" * 84)
    partite = leggi()
    print(f"  partite con quote di Pinnacle e risultato: {len(partite)}")
    con_ou = sum(1 for p in partite if p["ou"])
    print(f"  di cui anche con l'Over/Under di Pinnacle: {con_ou}")
    if not partite:
        print("  Nessun dato: lancia prima test_valore.py, che scarica i file.")
        return
    print("  Calcolo i gol attesi di ogni partita dalle quote (un paio di minuti)...",
          flush=True)

    ft = {nome: [] for nome, _ in MERCATI}
    pt = {nome: [] for nome, _ in MERCATI_PT}
    scarti = []
    for i, p in enumerate(partite):
        f1 = V.giusto_potenza(p["q"])
        fo = V.giusto_potenza(p["ou"])["O"] if p["ou"] else None
        lc, lf, sc = gol_attesi(f1["H"], f1["A"], fo)
        scarti.append(sc)
        M = matrice(lc, lf)
        for nome, regola in MERCATI:
            prob = sum(M[x][y] for x in range(N) for y in range(N) if regola(x, y))
            ft[nome].append((prob, regola(p["gc"], p["ga"])))
        if p["htc"] is not None and p["hta"] is not None:
            Mh = matrice(lc * QUOTA_PRIMO_TEMPO, lf * QUOTA_PRIMO_TEMPO, 0.0)
            for nome, regola in MERCATI_PT:
                prob = sum(Mh[x][y] for x in range(N) for y in range(N) if regola(x, y))
                pt[nome].append((prob, regola(int(p["htc"]), int(p["hta"]))))
        if (i + 1) % 5000 == 0:
            print(f"    {i + 1} partite...", flush=True)

    scarti.sort()
    print(f"\n  quanto i gol attesi riproducono le quote di Pinnacle: scarto tipico "
          f"{scarti[len(scarti) // 2]:.2%}, nel 95% dei casi sotto "
          f"{scarti[int(len(scarti) * 0.95)]:.2%}")

    print("\n" + "=" * 84)
    print("1. MERCATI SUL RISULTATO FINALE: PREVISTO CONTRO REALE")
    print("=" * 84)
    print("  Per ogni fascia di probabilita': quanto dicevamo e quanto e' successo.")
    print("  'ok' = scarto dentro il margine del caso.\n")
    buoni, cattivi = [], []
    for nome, _ in MERCATI:
        (buoni if tabella(nome, ft[nome]) else cattivi).append(nome)
        print()

    print("=" * 84)
    print("2. PRIMO TEMPO")
    print("=" * 84)
    print(f"  I gol attesi del primo tempo sono il {QUOTA_PRIMO_TEMPO:.0%} di quelli")
    print("  della partita, come misurato sulle nostre 5.500 partite.\n")
    for nome, _ in MERCATI_PT:
        if len(pt[nome]) < 500:
            print(f"  {nome}: dati del primo tempo non disponibili")
            continue
        (buoni if tabella(nome, pt[nome]) else cattivi).append(nome)
        print()

    print("=" * 84)
    print("3. VERDETTO")
    print("=" * 84)
    print("  Mercati dove la quota giusta ricavata da Pinnacle e' onesta, e dove")
    print("  quindi la quota minima si puo' usare:")
    for n in buoni:
        print(f"    + {n}")
    if cattivi:
        print("\n  Mercati dove sbaglia in modo sistematico: la quota minima andrebbe")
        print("  corretta prima di usarla, oppure non usata:")
        for n in cattivi:
            print(f"    - {n}")
    print("=" * 84)


if __name__ == "__main__":
    main()
