#!/usr/bin/env python3
"""
ALL'INTERVALLO: SAPPIAMO PREVEDERE IL SECONDO TEMPO?

All'intervallo le quote stanno ferme quindici minuti e il risultato del
primo tempo e' noto. Se sappiamo calcolare bene la probabilita' di cio'
che succede nel secondo tempo, possiamo confrontarla con le quote live
dei bookmaker, come facciamo prima della partita.

Il punto di partenza sono i gol attesi ricavati dalle quote di Pinnacle
(come in quote_giuste.py). Due modi di prevedere il secondo tempo:

  SEMPLICE    il secondo tempo vale la sua parte dei gol attesi della
              partita, qualunque sia il risultato del primo tempo.
  CON IL      in piu' si impara dai dati come cambia il ritmo: chi e'
  RISULTATO   sotto spinge, chi e' avanti si copre, e un primo tempo con
              piu' gol del previsto dice che la partita e' piu' aperta.

I due modi si imparano sulle stagioni fino al 2022/23 e si provano su
quelle dopo, mai viste. Per ogni mercato che si gioca all'intervallo
(esito finale, Under/Over, gol nel secondo tempo, Gol/NoGol...) si
confronta la probabilita' prevista con quello che e' successo.

Quello che questo test NON puo' dire: se i bookmaker all'intervallo
pagano piu' del giusto. Le quote live storiche non esistono gratis: per
quello servira' la prova sulla carta, come per valore.py.

Salva la correzione in stato/intervallo.json, per usarla dal vivo.

Uso, dalla cartella del progetto (usa i file di test_valore.py):
    python3 scripts/test_intervallo.py
"""

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quote_giuste as Q
import test_derivati as D
import test_valore as V

ULTIMA_STAGIONE_STUDIO = "2223"
N = 10
FASCE = [(0.0, 0.10), (0.10, 0.20), (0.20, 0.35), (0.35, 0.50),
         (0.50, 0.65), (0.65, 0.80), (0.80, 0.90), (0.90, 1.0001)]
STATI = ["sotto di 2+", "sotto di 1", "in parita'", "avanti di 1", "avanti di 2+"]
USCITA = os.path.join("stato", "intervallo.json")


def stato_di(diff):
    """Da -2 in giu' a +2 in su: la posizione di una squadra all'intervallo."""
    return 0 if diff <= -2 else 1 if diff == -1 else 2 if diff == 0 else 3 if diff == 1 else 4


# ---------------------------------------------------------------
#  regressione di Poisson, scritta a mano (niente librerie esterne)
# ---------------------------------------------------------------

def risolvi(A, b):
    """Sistema lineare A x = b, eliminazione di Gauss con pivot."""
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        if abs(M[c][c]) < 1e-12:
            return None
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                for k in range(c, n + 1):
                    M[r][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] for i in range(n)]


def poisson_glm(osservazioni, n_par):
    """
    osservazioni: (gol, log dell'atteso di partenza, {indice: valore})
    Trova i parametri per cui gol ~ Poisson(atteso * exp(somma parametri)).
    """
    theta = [0.0] * n_par
    for _ in range(25):
        g = [0.0] * n_par
        H = [[0.0] * n_par for _ in range(n_par)]
        for y, off, x in osservazioni:
            mu = math.exp(off + sum(theta[i] * v for i, v in x.items()))
            for i, vi in x.items():
                g[i] += (y - mu) * vi
                for j, vj in x.items():
                    H[i][j] += mu * vi * vj
        passo = risolvi(H, g)
        if passo is None:
            break
        theta = [t + d for t, d in zip(theta, passo)]
        if max(abs(d) for d in passo) < 1e-8:
            break
    return theta


# ---------------------------------------------------------------

def griglia(a, b):
    pa, pb = Q.poisson_lista(a, N), Q.poisson_lista(b, N)
    return [[x * y for y in pb] for x in pa]


MERCATI = [
    # (nome, regola su: gol casa/ospite a fine partita, gol casa/ospite nel secondo tempo)
    ("1 finale", lambda fc, ff, sc, sf: fc > ff),
    ("X finale", lambda fc, ff, sc, sf: fc == ff),
    ("2 finale", lambda fc, ff, sc, sf: fc < ff),
    ("Over 1.5 finale", lambda fc, ff, sc, sf: fc + ff >= 2),
    ("Over 2.5 finale", lambda fc, ff, sc, sf: fc + ff >= 3),
    ("Over 3.5 finale", lambda fc, ff, sc, sf: fc + ff >= 4),
    ("Gol finale", lambda fc, ff, sc, sf: fc > 0 and ff > 0),
    ("Over 0.5 secondo tempo", lambda fc, ff, sc, sf: sc + sf >= 1),
    ("Over 1.5 secondo tempo", lambda fc, ff, sc, sf: sc + sf >= 2),
    ("1 secondo tempo", lambda fc, ff, sc, sf: sc > sf),
    ("X secondo tempo", lambda fc, ff, sc, sf: sc == sf),
    ("2 secondo tempo", lambda fc, ff, sc, sf: sc < sf),
    ("Casa segna nel secondo tempo", lambda fc, ff, sc, sf: sc > 0),
    ("Ospite segna nel secondo tempo", lambda fc, ff, sc, sf: sf > 0),
]


def probabilita(htc, hta, a, b):
    """Tutti i mercati, dato il primo tempo e i gol attesi del secondo."""
    G = griglia(a, b)
    out = {}
    for nome, regola in MERCATI:
        out[nome] = sum(G[i][j] for i in range(N) for j in range(N)
                        if regola(htc + i, hta + j, i, j))
    return out


def tabella(righe):
    """righe: (prob, avvenuto). Ritorna le fasce e se tutte stanno nel margine."""
    esito, onesto = [], True
    for lo, hi in FASCE:
        g = [r for r in righe if lo <= r[0] < hi]
        if len(g) < 150:
            continue
        prev = sum(r[0] for r in g) / len(g)
        reale = sum(1 for r in g if r[1]) / len(g)
        tol = 2.5 * math.sqrt(max(prev * (1 - prev), 1e-9) / len(g)) + 0.005
        ok = abs(prev - reale) <= tol
        onesto &= ok
        esito.append((lo, hi, len(g), prev, reale, ok))
    return esito, onesto


def perdita(righe):
    return [-(math.log(max(p, 1e-12)) if y else math.log(max(1 - p, 1e-12))) for p, y in righe]


def main():
    print("=" * 88)
    print("1. DATI")
    print("=" * 88)
    tutte = [p for p in D.leggi() if p["ou"] and p["htc"] is not None and p["hta"] is not None]
    partite = []
    for p in tutte:
        htc, hta = int(p["htc"]), int(p["hta"])
        if htc > p["gc"] or hta > p["ga"]:
            continue
        p["htc"], p["hta"] = htc, hta
        partite.append(p)
    studio = [p for p in partite if p["stagione"] <= ULTIMA_STAGIONE_STUDIO]
    prova = [p for p in partite if p["stagione"] > ULTIMA_STAGIONE_STUDIO]
    print(f"  partite con quote di Pinnacle e risultato del primo tempo: {len(partite)}")
    print(f"  per imparare (fino al 2022/23): {len(studio)}   per provare (dal 2023/24): {len(prova)}")
    if len(studio) < 2000 or len(prova) < 1000:
        print("  Troppo poche partite: lancia prima test_valore.py, che scarica i file.")
        return

    # gol attesi dalle quote, con la stessa memoria di calibra_mercati.py
    percorso = os.path.join(V.CARTELLA, "gol_attesi.json")
    try:
        with open(percorso, encoding="utf-8") as f:
            memoria = json.load(f)
    except (OSError, ValueError):
        memoria = {}
    nuove = 0
    print("  Gol attesi di ogni partita dalle quote di Pinnacle...", flush=True)
    for p in partite:
        chiave = "|".join(str(p["q"][k]) for k in "HDA") + f"|{p['ou']['O']}|{p['ou']['U']}"
        g = memoria.get(chiave)
        if g is None:
            f1 = Q.giusto_potenza(p["q"])
            fo = Q.giusto_potenza(p["ou"])["O"]
            lc, lf, _ = Q.gol_attesi(f1["H"], f1["A"], fo)
            g = memoria[chiave] = [round(lc, 4), round(lf, 4)]
            nuove += 1
        p["lc"], p["lf"] = g
    if nuove:
        try:
            with open(percorso, "w", encoding="utf-8") as f:
                json.dump(memoria, f)
        except OSError:
            pass

    # quanta parte dei gol cade nel primo tempo, per la casa e per l'ospite
    s_c = sum(p["htc"] for p in studio) / max(1, sum(p["gc"] for p in studio))
    s_f = sum(p["hta"] for p in studio) / max(1, sum(p["ga"] for p in studio))
    print(f"  gol nel primo tempo: casa {s_c:.1%}, ospite {s_f:.1%} del totale")

    # ---------------------------------------------------------------
    print("\n" + "=" * 88)
    print("2. COME CAMBIA IL RITMO NEL SECONDO TEMPO")
    print("=" * 88)
    # parametri: 0-4 stato della casa, 5-9 stato dell'ospite, 10 ritmo del primo tempo
    def osservazioni(gruppo):
        oss = []
        for p in gruppo:
            atteso_pt = p["lc"] * s_c + p["lf"] * s_f
            ritmo = (p["htc"] + p["hta"]) - atteso_pt
            oss.append((p["gc"] - p["htc"], math.log(p["lc"] * (1 - s_c)),
                        {stato_di(p["htc"] - p["hta"]): 1.0, 10: ritmo}))
            oss.append((p["ga"] - p["hta"], math.log(p["lf"] * (1 - s_f)),
                        {5 + stato_di(p["hta"] - p["htc"]): 1.0, 10: ritmo}))
        return oss

    theta = poisson_glm(osservazioni(studio), 11)
    print("  Gol del secondo tempo rispetto a quelli attesi dalle quote, per")
    print("  come si arriva all'intervallo:\n")
    print(f"    {'':<16}{'casa':>10}{'ospite':>10}")
    for i, nome in enumerate(STATI):
        print(f"    {nome:<16}{math.exp(theta[i]) - 1:>+10.0%}{math.exp(theta[5 + i]) - 1:>+10.0%}")
    print(f"\n  Ogni gol in piu' del previsto nel primo tempo cambia il ritmo del secondo "
          f"di {math.exp(theta[10]) - 1:+.0%}.")

    def attesi_st(p, con_risultato):
        a, b = p["lc"] * (1 - s_c), p["lf"] * (1 - s_f)
        if not con_risultato:
            return a, b
        ritmo = (p["htc"] + p["hta"]) - (p["lc"] * s_c + p["lf"] * s_f)
        a *= math.exp(theta[stato_di(p["htc"] - p["hta"])] + theta[10] * ritmo)
        b *= math.exp(theta[5 + stato_di(p["hta"] - p["htc"])] + theta[10] * ritmo)
        return a, b

    # ---------------------------------------------------------------
    print("\n" + "=" * 88)
    print("3. PREVISTO CONTRO REALE, SULLE STAGIONI MAI VISTE")
    print("=" * 88)
    print("  Per ogni mercato giocabile all'intervallo: se il previsto torna col reale in")
    print("  ogni fascia di probabilita' (il dettaglio compare solo dove non torna), e se")
    print("  tenere conto del risultato migliora la previsione. Contano solo i casi ancora")
    print("  aperti (probabilita' fra 2% e 98%).\n")
    righe = {nome: {"semplice": [], "risultato": []} for nome, _ in MERCATI}
    per_pt = {}
    for p in prova:
        sc, sf = p["gc"] - p["htc"], p["ga"] - p["hta"]
        reale = {nome: regola(p["gc"], p["ga"], sc, sf) for nome, regola in MERCATI}
        pr_s = probabilita(p["htc"], p["hta"], *attesi_st(p, False))
        pr_r = probabilita(p["htc"], p["hta"], *attesi_st(p, True))
        for nome, _ in MERCATI:
            # gli stessi casi per i due modi, cosi' il confronto e' alla pari
            if 0.02 <= pr_r[nome] <= 0.98:
                righe[nome]["semplice"].append((pr_s[nome], reale[nome]))
                righe[nome]["risultato"].append((pr_r[nome], reale[nome]))
        per_pt.setdefault(f"{p['htc']}-{p['hta']}", []).append((pr_r, reale))

    affidabili, da_correggere = [], []
    for nome, _ in MERCATI:
        t_s, ok_s = tabella(righe[nome]["semplice"])
        t_r, ok_r = tabella(righe[nome]["risultato"])
        d = [a - b for a, b in zip(perdita(righe[nome]["semplice"]), perdita(righe[nome]["risultato"]))]
        m = sum(d) / len(d) if d else 0.0
        es = math.sqrt(sum((x - m) ** 2 for x in d) / max(1, len(d) - 1) / max(1, len(d))) if d else 0.0
        verdetto = "AFFIDABILE" if ok_r else "DA CORREGGERE"
        (affidabili if ok_r else da_correggere).append(nome)
        print(f"  {nome:<32} {verdetto}   col risultato "
              f"{'meglio' if m > 2 * es else ('peggio' if m < -2 * es else 'uguale')} "
              f"del semplice ({m * 1000:+.1f} millesimi)")
        # il dettaglio per fasce solo dove qualcosa non torna
        if not ok_r:
            for lo, hi, n, prev, reale, ok in t_r:
                s_prev = next((x[3] for x in t_s if x[0] == lo), None)
                print(f"      {lo:>4.0%}-{min(hi, 1):>4.0%} {n:>6}   previsto {prev:>6.1%}"
                      + (f" (semplice {s_prev:>5.1%})" if s_prev is not None else " " * 19)
                      + f"   reale {reale:>6.1%}  {'ok' if ok else '!! ' + format(reale - prev, '+.1%')}")

    # ---------------------------------------------------------------
    print("=" * 88)
    print("4. I RISULTATI DEL PRIMO TEMPO PIU' FREQUENTI")
    print("=" * 88)
    print("  Probabilita' prevista col risultato (media) contro quello che e' successo.\n")
    print(f"    {'intervallo':<11}{'partite':>8}   {'gol nel 2T':>18}   {'1 finale':>18}   {'X finale':>18}")
    for pt in ["0-0", "1-0", "0-1", "1-1", "2-0", "0-2", "2-1", "1-2"]:
        g = per_pt.get(pt, [])
        if len(g) < 200:
            continue
        def coppia(nome):
            prev = sum(x[0][nome] for x in g) / len(g)
            reale = sum(1 for x in g if x[1][nome]) / len(g)
            return f"{prev:>6.1%} / {reale:>6.1%}"
        print(f"    {pt:<11}{len(g):>8}   {coppia('Over 0.5 secondo tempo'):>18}   "
              f"{coppia('1 finale'):>18}   {coppia('X finale'):>18}")
    print("    (previsto / reale)")

    # ---------------------------------------------------------------
    os.makedirs("stato", exist_ok=True)
    with open(USCITA, "w", encoding="utf-8") as f:
        json.dump({"quota_pt_casa": round(s_c, 4), "quota_pt_ospite": round(s_f, 4),
                   "stato_casa": [round(x, 5) for x in theta[:5]],
                   "stato_ospite": [round(x, 5) for x in theta[5:10]],
                   "ritmo": round(theta[10], 5), "stati": STATI,
                   "affidabili": affidabili, "da_correggere": da_correggere}, f,
                  ensure_ascii=False, indent=1)

    print("\n" + "=" * 88)
    print("5. VERDETTO")
    print("=" * 88)
    print(f"  Mercati dell'intervallo che sappiamo prezzare bene: {len(affidabili)} su {len(MERCATI)}")
    for n in affidabili:
        print(f"    + {n}")
    for n in da_correggere:
        print(f"    - {n}")
    print(f"\n  Salvato {USCITA}. Resta da vedere, sulla carta, se i bookmaker all'intervallo")
    print("  pagano piu' di questi prezzi: questo test non lo puo' dire.")
    print("=" * 88)


if __name__ == "__main__":
    main()
