#!/usr/bin/env python3
"""
ALL'INTERVALLO: SAPPIAMO PREVEDERE IL SECONDO TEMPO?

All'intervallo le quote stanno ferme quindici minuti e il risultato del
primo tempo e' noto. Se sappiamo calcolare bene la probabilita' di cio'
che succede nel secondo tempo, possiamo confrontarla con le quote live
dei bookmaker, come facciamo prima della partita.

Il punto di partenza sono i gol attesi ricavati dalle quote di Pinnacle
(come in quote_giuste.py): al secondo tempo spetta la sua parte, e in
piu' si impara dai dati come cambia il ritmo secondo il risultato
dell'intervallo (chi e' sotto spinge, chi e' avanti si copre).

Dal 2023/24 in molti campionati si recupera di piu' a fine partita, e il
secondo tempo potrebbe pesare piu' di prima. Per questo si confrontano
due modelli: uno imparato fino al 2022/23 (VECCHIO) e uno imparato sul
2023/24 (RECENTE), provati entrambi sulle stagioni dal 2024/25, che
nessuno dei due ha visto. Per ogni mercato che si gioca all'intervallo
(esito finale, Under/Over, gol nel secondo tempo, Gol/NoGol...) si
confronta la probabilita' prevista con quello che e' successo.

Quello che questo test NON puo' dire: se i bookmaker all'intervallo
pagano piu' del giusto. Le quote live storiche non esistono gratis: per
quello servira' la prova sulla carta, come per valore.py.

Salva il modello imparato su tutte le stagioni dal 2023/24 in
stato/intervallo.json, per usarlo dal vivo.

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


def adatta(gruppo):
    """Quota del primo tempo e cambi di ritmo, imparati su un gruppo di partite."""
    s_c = sum(p["htc"] for p in gruppo) / max(1, sum(p["gc"] for p in gruppo))
    s_f = sum(p["hta"] for p in gruppo) / max(1, sum(p["ga"] for p in gruppo))
    oss = []
    for p in gruppo:
        ritmo = (p["htc"] + p["hta"]) - (p["lc"] * s_c + p["lf"] * s_f)
        oss.append((p["gc"] - p["htc"], math.log(p["lc"] * (1 - s_c)),
                    {stato_di(p["htc"] - p["hta"]): 1.0, 10: ritmo}))
        oss.append((p["ga"] - p["hta"], math.log(p["lf"] * (1 - s_f)),
                    {5 + stato_di(p["hta"] - p["htc"]): 1.0, 10: ritmo}))
    # parametri: 0-4 stato della casa, 5-9 stato dell'ospite, 10 ritmo del primo tempo
    return {"s_c": s_c, "s_f": s_f, "theta": poisson_glm(oss, 11)}


def attesi_st(p, mod):
    """I gol attesi del secondo tempo, dato come si e' arrivati all'intervallo."""
    t, s_c, s_f = mod["theta"], mod["s_c"], mod["s_f"]
    ritmo = (p["htc"] + p["hta"]) - (p["lc"] * s_c + p["lf"] * s_f)
    a = p["lc"] * (1 - s_c) * math.exp(t[stato_di(p["htc"] - p["hta"])] + t[10] * ritmo)
    b = p["lf"] * (1 - s_f) * math.exp(t[5 + stato_di(p["hta"] - p["htc"])] + t[10] * ritmo)
    return a, b


def nome_stagione(s):
    return f"20{s[:2]}/{s[2:]}"


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
    print(f"  partite con quote di Pinnacle e risultato del primo tempo: {len(partite)}")

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

    print("\n  Stagione per stagione: dal 2023/24 in molti campionati si recupera di piu'")
    print("  a fine partita. Se il secondo tempo pesa di piu', qui si vede.\n")
    print(f"    {'stagione':<10}{'partite':>8}{'gol a partita':>15}{'nel 1T':>9}{'nel 2T':>9}")
    for st in sorted({p["stagione"] for p in partite}):
        g = [p for p in partite if p["stagione"] == st]
        tot = sum(p["gc"] + p["ga"] for p in g)
        pt = sum(p["htc"] + p["hta"] for p in g)
        print(f"    {nome_stagione(st):<10}{len(g):>8}{tot / len(g):>15.2f}"
              f"{pt / max(1, tot):>9.1%}{1 - pt / max(1, tot):>9.1%}")

    vecchie = [p for p in partite if p["stagione"] <= "2223"]
    recente = [p for p in partite if p["stagione"] == "2324"]
    prova = [p for p in partite if p["stagione"] >= "2425"]
    print(f"\n  Due modelli, provati entrambi sulle stagioni dal 2024/25 ({len(prova)} partite):")
    print(f"    VECCHIO  imparato fino al 2022/23 ({len(vecchie)} partite)")
    print(f"    RECENTE  imparato sul 2023/24 ({len(recente)} partite)")
    if len(vecchie) < 2000 or len(recente) < 1000 or len(prova) < 1000:
        print("  Troppo poche partite: lancia prima test_valore.py, che scarica i file.")
        return
    mod_v, mod_r = adatta(vecchie), adatta(recente)

    # ---------------------------------------------------------------
    print("\n" + "=" * 88)
    print("2. COME CAMBIA IL RITMO NEL SECONDO TEMPO")
    print("=" * 88)
    print("  Gol del secondo tempo rispetto alla parte di gol attesi dalle quote,")
    print("  per come si arriva all'intervallo:\n")
    print(f"    {'':<16}{'casa':>18}{'ospite':>18}")
    print(f"    {'':<16}{'vecchio  recente':>18}{'vecchio  recente':>18}")
    tv, tr = mod_v["theta"], mod_r["theta"]
    for i, nome in enumerate(STATI):
        print(f"    {nome:<16}{math.exp(tv[i]) - 1:>+10.0%}{math.exp(tr[i]) - 1:>+8.0%}"
              f"{math.exp(tv[5 + i]) - 1:>+10.0%}{math.exp(tr[5 + i]) - 1:>+8.0%}")
    print(f"\n  Gol nel primo tempo: vecchio {mod_v['s_c']:.1%} (casa) {mod_v['s_f']:.1%} (ospite),"
          f" recente {mod_r['s_c']:.1%} {mod_r['s_f']:.1%}.")
    print(f"  Ogni gol in piu' del previsto nel primo tempo cambia il ritmo del secondo di "
          f"{math.exp(tv[10]) - 1:+.0%} (vecchio), {math.exp(tr[10]) - 1:+.0%} (recente).")

    # ---------------------------------------------------------------
    print("\n" + "=" * 88)
    print("3. PREVISTO CONTRO REALE, DAL 2024/25 (MAI VISTO DA NESSUNO DEI DUE)")
    print("=" * 88)
    print("  Per ogni mercato giocabile all'intervallo: se col modello RECENTE il previsto")
    print("  torna col reale in ogni fascia (il dettaglio compare solo dove non torna), e")
    print("  come va rispetto al VECCHIO. Solo casi ancora aperti (fra 2% e 98%).\n")
    righe = {nome: {"v": [], "r": []} for nome, _ in MERCATI}
    per_pt = {}
    for p in prova:
        sc, sf = p["gc"] - p["htc"], p["ga"] - p["hta"]
        reale = {nome: regola(p["gc"], p["ga"], sc, sf) for nome, regola in MERCATI}
        pr_v = probabilita(p["htc"], p["hta"], *attesi_st(p, mod_v))
        pr_r = probabilita(p["htc"], p["hta"], *attesi_st(p, mod_r))
        for nome, _ in MERCATI:
            # gli stessi casi per i due modelli, cosi' il confronto e' alla pari
            if 0.02 <= pr_r[nome] <= 0.98:
                righe[nome]["v"].append((pr_v[nome], reale[nome]))
                righe[nome]["r"].append((pr_r[nome], reale[nome]))
        per_pt.setdefault(f"{p['htc']}-{p['hta']}", []).append((pr_r, reale))

    affidabili, da_correggere = [], []
    for nome, _ in MERCATI:
        t_v, ok_v = tabella(righe[nome]["v"])
        t_r, ok_r = tabella(righe[nome]["r"])
        d = [a - b for a, b in zip(perdita(righe[nome]["v"]), perdita(righe[nome]["r"]))]
        m = sum(d) / len(d) if d else 0.0
        es = math.sqrt(sum((x - m) ** 2 for x in d) / max(1, len(d) - 1) / max(1, len(d))) if d else 0.0
        (affidabili if ok_r else da_correggere).append(nome)
        confronto = "meglio" if m > 2 * es else ("peggio" if m < -2 * es else "uguale")
        print(f"  {nome:<32} {'AFFIDABILE' if ok_r else 'DA CORREGGERE':<14} recente {confronto} "
              f"del vecchio ({m * 1000:+.1f} millesimi), che era "
              f"{'affidabile' if ok_v else 'da correggere'}")
        if not ok_r:
            for lo, hi, n, prev, reale, ok in t_r:
                v_prev = next((x[3] for x in t_v if x[0] == lo), None)
                print(f"      {lo:>4.0%}-{min(hi, 1):>4.0%} {n:>6}   previsto {prev:>6.1%}"
                      + (f" (vecchio {v_prev:>5.1%})" if v_prev is not None else " " * 18)
                      + f"   reale {reale:>6.1%}  {'ok' if ok else '!! ' + format(reale - prev, '+.1%')}")

    # ---------------------------------------------------------------
    print("\n" + "=" * 88)
    print("4. I RISULTATI DEL PRIMO TEMPO PIU' FREQUENTI (modello recente)")
    print("=" * 88)
    print("  Probabilita' prevista (media) contro quello che e' successo.\n")
    print(f"    {'intervallo':<11}{'partite':>8}   {'gol nel 2T':>18}   {'1 finale':>18}   {'X finale':>18}")
    for pt in ["0-0", "1-0", "0-1", "1-1", "2-0", "0-2", "2-1", "1-2"]:
        g = per_pt.get(pt, [])
        if len(g) < 150:
            continue

        def coppia(nome):
            prev = sum(x[0][nome] for x in g) / len(g)
            reale = sum(1 for x in g if x[1][nome]) / len(g)
            return f"{prev:>6.1%} / {reale:>6.1%}"
        print(f"    {pt:<11}{len(g):>8}   {coppia('Over 0.5 secondo tempo'):>18}   "
              f"{coppia('1 finale'):>18}   {coppia('X finale'):>18}")
    print("    (previsto / reale)")

    # ---------------------------------------------------------------
    # per l'uso dal vivo: il modello imparato su tutte le stagioni recenti
    finale = adatta([p for p in partite if p["stagione"] >= "2324"])
    os.makedirs("stato", exist_ok=True)
    with open(USCITA, "w", encoding="utf-8") as f:
        json.dump({"stagioni": "dal 2023/24",
                   "quota_pt_casa": round(finale["s_c"], 4), "quota_pt_ospite": round(finale["s_f"], 4),
                   "stato_casa": [round(x, 5) for x in finale["theta"][:5]],
                   "stato_ospite": [round(x, 5) for x in finale["theta"][5:10]],
                   "ritmo": round(finale["theta"][10], 5), "stati": STATI,
                   "affidabili": affidabili, "da_correggere": da_correggere}, f,
                  ensure_ascii=False, indent=1)

    print("\n" + "=" * 88)
    print("5. VERDETTO")
    print("=" * 88)
    print(f"  Mercati dell'intervallo che il modello recente prezza bene: {len(affidabili)} su {len(MERCATI)}")
    for n in affidabili:
        print(f"    + {n}")
    for n in da_correggere:
        print(f"    - {n}")
    print(f"\n  Salvato {USCITA} (imparato su tutte le stagioni dal 2023/24). Resta da vedere,")
    print("  sulla carta, se i bookmaker all'intervallo pagano piu' di questi prezzi.")
    print("=" * 88)


if __name__ == "__main__":
    main()
