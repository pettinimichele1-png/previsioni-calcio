#!/usr/bin/env python3
"""
LA CORREZIONE DI OGNI MERCATO

Per ognuno dei mercati che quote_giuste.py calcola (gli 89 del motore
piu' quelli del primo e del secondo tempo) confronta la probabilita'
ricavata dalle quote di Pinnacle con quello che e' successo davvero, e
ne ricava una correzione.

La correzione si impara sulle stagioni fino al 2022/23 e si prova sulle
stagioni successive, che non ha mai visto. Un mercato si usa (in
valore.py) solo se, su quelle stagioni, previsto e reale coincidono in
ogni fascia di probabilita'.

Salva:
    stato/calibrazione_mercati.json   la correzione, per valore.py
    stato/calibrazione_mercati.txt    il dettaglio mercato per mercato

Uso, dalla cartella del progetto (usa i file scaricati da test_valore.py):
    python3 scripts/calibra_mercati.py

La prima volta ci mette qualche minuto; poi i gol attesi di ogni partita
restano salvati (dati_valore/gol_attesi.json) e ci mette meno.
"""

import json
import math
import os
import sys
from array import array
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quote_giuste as Q
import test_derivati as D      # legge i file di football-data
import test_valore as V

ULTIMA_STAGIONE_STUDIO = "2223"
FASCE = D.FASCE
MIN_FASCIA = 150
USCITA_JSON = os.path.join("stato", "calibrazione_mercati.json")
USCITA_TXT = os.path.join("stato", "calibrazione_mercati.txt")


# ---------------------------------------------------------------
#  la correzione: una retta sulla scala dei logit (metodo di Platt)
# ---------------------------------------------------------------

def platt(xs, ys):
    """
    Trova a e b tali che la probabilita' corretta sia
    sigmoide(a + b * logit(p)). a sposta tutto in su o in giu', b
    allarga o stringe. Con a = 0 e b = 1 non cambia niente.
    """
    a, b = 0.0, 1.0
    for _ in range(30):
        ga = gb = haa = hab = hbb = 0.0
        for x, y in zip(xs, ys):
            p = Q.sigmoide(a + b * x)
            r = p - y
            w = p * (1 - p)
            ga += r
            gb += r * x
            haa += w
            hab += w * x
            hbb += w * x * x
        det = haa * hbb - hab * hab
        if det <= 1e-12:
            break
        da = (hbb * ga - hab * gb) / det
        db = (haa * gb - hab * ga) / det
        a, b = a - da, b - db
        if abs(da) < 1e-10 and abs(db) < 1e-10:
            break
    return a, b


def perdite(ps, ys):
    return [-(math.log(max(p, 1e-12)) if y else math.log(max(1 - p, 1e-12)))
            for p, y in zip(ps, ys)]


def fasce(ps, ys):
    """Per fascia di probabilita': quante, previsto, reale, e se lo scarto sta
    dentro il margine del caso."""
    out = []
    for lo, hi in FASCE:
        g = [(p, y) for p, y in zip(ps, ys) if lo <= p < hi]
        if len(g) < MIN_FASCIA:
            continue
        prev = sum(p for p, _ in g) / len(g)
        reale = sum(y for _, y in g) / len(g)
        tol = 2.5 * math.sqrt(max(prev * (1 - prev), 1e-9) / len(g)) + 0.005
        out.append((lo, hi, len(g), prev, reale, abs(reale - prev) <= tol))
    return out


def peggior_scarto(tab):
    return max((abs(r[4] - r[3]) for r in tab), default=0.0)


# ---------------------------------------------------------------

def main():
    print("=" * 84)
    print("1. DATI")
    print("=" * 84)
    partite = [p for p in D.leggi() if p["ou"]]
    if not partite:
        print("  Nessun dato: lancia prima test_valore.py, che scarica i file.")
        return
    studio = [i for i, p in enumerate(partite) if p["stagione"] <= ULTIMA_STAGIONE_STUDIO]
    prova = [i for i, p in enumerate(partite) if p["stagione"] > ULTIMA_STAGIONE_STUDIO]
    print(f"  partite con quote di Pinnacle (esito e Over/Under 2.5): {len(partite)}")
    print(f"  per imparare la correzione (fino al 2022/23): {len(studio)}")
    print(f"  per provarla (dal 2023/24, mai viste):         {len(prova)}")

    # quanta parte dei gol cade nel primo tempo
    gol_pt = gol_tot = 0
    for i in studio:
        p = partite[i]
        if p["htc"] is not None and p["hta"] is not None:
            gol_pt += p["htc"] + p["hta"]
            gol_tot += p["gc"] + p["ga"]
    quota_pt = gol_pt / gol_tot if gol_tot else Q.QUOTA_PRIMO_TEMPO
    print(f"  gol segnati nel primo tempo: {quota_pt:.1%} del totale")

    # gol attesi di ogni partita, con memoria per le volte successive
    percorso_memoria = os.path.join(V.CARTELLA, "gol_attesi.json")
    try:
        with open(percorso_memoria, encoding="utf-8") as f:
            memoria = json.load(f)
    except (OSError, ValueError):
        memoria = {}
    nuove = 0
    print("  Calcolo le probabilita' di ogni mercato in ogni partita...", flush=True)

    prob = {k: array("d") for k in Q.MERCATI}
    esiti = {k: array("b") for k in Q.MERCATI}
    vince = {k: set(idx) for k, idx in Q.CASELLE.items()}
    for n, p in enumerate(partite):
        f = Q.giusto_potenza(p["q"])
        fo = Q.giusto_potenza(p["ou"])["O"]
        chiave = "|".join(str(p["q"][k]) for k in "HDA") + f"|{p['ou']['O']}|{p['ou']['U']}"
        g = memoria.get(chiave)
        if g is None:
            lc, lf, _ = Q.gol_attesi(f["H"], f["A"], fo)
            g = memoria[chiave] = [round(lc, 4), round(lf, 4)]
            nuove += 1
        pr, _ = Q.tutte(f["H"], f["D"], f["A"], fo, gol=g, quota_pt=quota_pt)
        gc, ga = p["gc"], p["ga"]
        htc = int(p["htc"]) if p["htc"] is not None else None
        hta = int(p["hta"]) if p["hta"] is not None else None
        cella = gc * Q.N + ga if gc < Q.N and ga < Q.N else None
        for k in Q.MERCATI:
            prob[k].append(pr[k])
            if k in vince:
                e = (cella in vince[k]) if cella is not None else Q.esito(k, gc, ga)
            else:
                e = Q.esito(k, gc, ga, htc, hta)
            esiti[k].append(-1 if e is None else int(bool(e)))
        if (n + 1) % 5000 == 0:
            print(f"    {n + 1} partite...", flush=True)
    if nuove:
        try:
            with open(percorso_memoria, "w", encoding="utf-8") as f:
                json.dump(memoria, f)
        except OSError:
            pass

    print("\n" + "=" * 84)
    print("2. LA CORREZIONE, MERCATO PER MERCATO")
    print("=" * 84)
    print("  Imparata fino al 2022/23, provata dal 2023/24. Dettaglio in", USCITA_TXT)

    righe_txt = []
    risultato = {}
    cosi, corretti, esclusi = [], [], []
    for k in Q.MERCATI:
        ps, ys = prob[k], esiti[k]
        s_x = [Q.logit(ps[i]) for i in studio if ys[i] >= 0]
        s_y = [ys[i] for i in studio if ys[i] >= 0]
        t_p = [ps[i] for i in prova if ys[i] >= 0]
        t_y = [ys[i] for i in prova if ys[i] >= 0]
        if len(s_y) < 2000 or len(t_y) < 1000 or not 0 < sum(s_y) < len(s_y):
            esclusi.append((k, "dati insufficienti"))
            continue
        a, b = platt(s_x, s_y)
        t_c = [Q.sigmoide(a + b * Q.logit(p)) for p in t_p]
        d = [x - y for x, y in zip(perdite(t_p, t_y), perdite(t_c, t_y))]
        guadagno = sum(d) / len(d)
        media = guadagno
        es = math.sqrt(sum((x - media) ** 2 for x in d) / (len(d) - 1) / len(d))
        usa = guadagno > 0
        tab_prima, tab_dopo = fasce(t_p, t_y), fasce(t_c, t_y)
        ok_prima = all(r[5] for r in tab_prima)
        ok_dopo = all(r[5] for r in (tab_dopo if usa else tab_prima))
        affidabile = ok_dopo

        # la correzione definitiva si impara su tutte le stagioni
        if usa:
            tutti = [i for i in range(len(partite)) if ys[i] >= 0]
            a_f, b_f = platt([Q.logit(ps[i]) for i in tutti], [ys[i] for i in tutti])
        else:
            a_f, b_f = 0.0, 1.0
        risultato[k] = {"a": round(a_f, 5), "b": round(b_f, 5), "nome": Q.nome(k),
                        "affidabile": affidabile}

        tipica = sorted(t_p)[len(t_p) // 2]
        dopo_tipica = Q.sigmoide(a_f + b_f * Q.logit(tipica))
        if not affidabile:
            esclusi.append((k, f"scarto fino a {peggior_scarto(tab_dopo if usa else tab_prima):.1%}"
                               " anche dopo la correzione"))
        elif ok_prima:
            cosi.append(k)
        else:
            corretti.append((k, tipica, dopo_tipica))

        righe_txt.append(f"{Q.nome(k)}   [{k}]")
        righe_txt.append(f"  correzione: a={a_f:+.4f} b={b_f:.4f}  "
                         f"(al valore tipico {tipica:.1%} -> {dopo_tipica:.1%})"
                         f"   guadagno sulla prova {guadagno * 1000:+.2f} +/- {1.96 * es * 1000:.2f} millesimi"
                         f"   {'APPLICATA' if usa else 'non serve'}")
        for titolo, tab in (("prima", tab_prima), ("dopo ", tab_dopo)):
            for lo, hi, nn, prev, reale, ok in tab:
                righe_txt.append(f"    {titolo} {lo:>4.0%}-{min(hi, 1):>4.0%} {nn:>6}  previsto {prev:>6.1%}"
                                 f"  reale {reale:>6.1%}  {'ok' if ok else '!! ' + format(reale - prev, '+.1%')}")
        righe_txt.append(f"  -> {'AFFIDABILE' if affidabile else 'NON AFFIDABILE'}\n")

    os.makedirs("stato", exist_ok=True)
    with open(USCITA_JSON, "w", encoding="utf-8") as f:
        json.dump({"creato": datetime.now().isoformat(timespec="minutes"),
                   "partite": len(partite), "quota_primo_tempo": round(quota_pt, 4),
                   "rho": Q.RHO, "mercati": risultato}, f, ensure_ascii=False, indent=1)
    with open(USCITA_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(righe_txt) + "\n")

    tempi = set(Q.TEMPI)
    corr = {k: (a, b) for k, a, b in corretti}
    escl = dict(esclusi)
    print("\n  PRIMO E SECONDO TEMPO")
    print("  (al valore tipico: quanto dicevano le quote di Pinnacle -> quanto dopo la correzione)\n")
    for k in Q.TEMPI:
        if k in cosi:
            stato = "giusto cosi' com'e'"
        elif k in corr:
            stato = f"giusto dopo la correzione   {corr[k][0]:>6.1%} -> {corr[k][1]:>6.1%}"
        else:
            stato = "ESCLUSO: " + escl.get(k, "")
        print(f"    {Q.nome(k):<40} {stato}")

    finali = [k for k in Q.MERCATI if k not in tempi]
    print("\n  RISULTATO FINALE")
    print(f"    giusti cosi' come sono: {sum(1 for k in cosi if k not in tempi)}"
          f"   giusti dopo la correzione: {sum(1 for k in corr if k not in tempi)}"
          f"   esclusi: {sum(1 for k in escl if k not in tempi)}   su {len(finali)}")
    for k, perche in esclusi:
        if k not in tempi:
            print(f"    escluso {Q.nome(k):<34} {perche}")

    print("\n" + "=" * 84)
    print(f"  Salvato {USCITA_JSON}: {len(cosi) + len(corretti)} mercati utilizzabili")
    print("  su " + str(len(Q.MERCATI)) + ". Da adesso valore.py la usa per i mercati dei tempi.")
    print("=" * 84)


if __name__ == "__main__":
    main()
