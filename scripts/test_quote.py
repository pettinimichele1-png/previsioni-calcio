#!/usr/bin/env python3
"""
COSA DICONO LE QUOTE, PRESE DA SOLE

Le quote sono la miglior previsione dei risultati che esista: quella
di chiusura di Pinnacle sbaglia meno di qualunque modello, compreso il
nostro. Ma prevedere bene non basta: il bookmaker ti paga in base alla
sua previsione, meno il margine. Si guadagna solo dove il mercato
sbaglia in modo sistematico, o dove sappiamo qualcosa che la quota non
dice ancora.

Questo test fa tre domande alle quote, su decine di migliaia di
partite gia' giocate.

  1. IL MERCATO SBAGLIA SEMPRE NELLO STESSO VERSO?
     E' noto che i bookmaker sottovalutano le favorite nette e
     sopravvalutano le sfavorite (effetto favorita-sfavorita). Se
     l'effetto e' abbastanza forte, giocare sempre le favorite nette
     potrebbe rendere, anche con il margine. Riguarda proprio le quote
     basse.

  2. I MOVIMENTI DELLE QUOTE DICONO QUALCOSA?
     Fra il venerdi' e il fischio d'inizio le quote si muovono. Se una
     squadra la cui quota e' scesa vince piu' spesso di quanto dica la
     quota finale, il movimento contiene un'informazione in piu'.

  3. IL NOSTRO MODELLO AGGIUNGE QUALCOSA ALLE QUOTE?
     Da solo sbaglia piu' del mercato. Ma mescolato alle quote potrebbe
     migliorarle, se contiene anche solo un pezzo di informazione che
     il mercato non ha. E' il test esatto di "prevedere meglio".

Si guarda il ritorno reale, fascia per fascia e stagione per stagione.
Con tante righe, qualcuna risultera' positiva per puro caso: conta solo
cio' che regge in quasi tutte le stagioni.

Uso, dalla cartella del progetto, dopo test_valore.py (usa i suoi file):
    python3 scripts/test_quote.py
"""

import math
import os
import random
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_valore as V

DB_PATH = "calcio_dati.db"
QUOTA_MAX = 15.0

FASCE_1X2 = [(1.00, 1.30), (1.30, 1.50), (1.50, 1.80), (1.80, 2.20),
             (2.20, 3.00), (3.00, 5.00), (5.00, 10.0), (10.0, 99.0)]
FASCE_OU = [(1.00, 1.60), (1.60, 1.85), (1.85, 2.10), (2.10, 2.50), (2.50, 99.0)]

# con quali quote si gioca. "prima" = il venerdi' (o due giorni prima),
# "chiusura" = poco prima del fischio d'inizio.
LIBRI_1X2 = [("PS_C", "Pinn. chius."), ("B365", "bet365"),
             ("B365_C", "b365 chius."), ("WH", "W. Hill"), ("BW", "bwin"),
             ("Max", "migliore"), ("Max_C", "migl. chius.")]
LIBRI_OU = [("PS_OUC", "Pinn. chius."), ("B365_OU", "bet365"),
            ("B365_OUC", "b365 chius."), ("WH_OU", "W. Hill"),
            ("Max_OU", "migliore"), ("Max_OUC", "migl. chius.")]
ACCESSIBILI = {"B365", "B365_C", "WH", "BW", "B365_OU", "B365_OUC", "WH_OU"}


def esiti(p, mercato):
    if mercato == "1X2":
        return {k: p["esito"] == k for k in "HDA"}
    if p["gol"] is None:
        return None
    return {"O": p["gol"] > 2.5, "U": p["gol"] < 2.5}


def etichetta_fascia(lo, hi):
    return f"{lo:.2f}-{hi:.2f}" if hi < 99 else f"oltre {lo:.2f}"


def raccogli(partite, mercato, chiave, fascia, stagione=None):
    """Ritorni di tutte le giocate di quella fascia, a quelle quote."""
    rif = "PS_C" if mercato == "1X2" else "PS_OUC"
    lo, hi = fascia
    out = []
    for p in partite:
        if stagione and p["stagione"] != stagione:
            continue
        if rif not in p or chiave not in p:
            continue
        ok = esiti(p, mercato)
        if ok is None:
            continue
        for k, vinta in ok.items():
            if not lo <= p[rif][k] < hi:
                continue
            q = p[chiave][k]
            if q > QUOTA_MAX:
                continue
            out.append((q - 1.0) if vinta else -1.0)
    return out


def cella(valori):
    if len(valori) < 30:
        return "–"
    m, ic = V.media_ic(valori)
    segno = ""
    if ic and ic[0] > 0:
        segno = "*"
    elif ic and ic[1] < 0:
        segno = "-"
    return f"{m:+.1%}{segno}"


def tabella(partite, mercato, fasce, libri):
    print(f"  {'quota':<13}" + "".join(f"{n:>13}" for _, n in libri))
    for f in fasce:
        riga = f"  {etichetta_fascia(*f):<13}"
        for chiave, _ in libri:
            riga += f"{cella(raccogli(partite, mercato, chiave, f)):>13}"
        print(riga)
    print("\n  * = guadagno dimostrato (fascia tutta sopra zero)")
    print("  - = perdita dimostrata    senza segno = non distinguibile dal caso")


def cerca_vincenti(partite, mercato, fasce, libri, stagioni):
    """Le combinazioni con guadagno dimostrato, e se reggono nel tempo."""
    trovate = []
    for chiave, nome in libri:
        for f in fasce:
            v = raccogli(partite, mercato, chiave, f)
            if len(v) < 100:
                continue
            m, ic = V.media_ic(v)
            if not ic or ic[0] <= 0:
                continue
            positive = tot = 0
            for s in stagioni:
                vs = raccogli(partite, mercato, chiave, f, s)
                if len(vs) >= 20:
                    tot += 1
                    positive += sum(vs) > 0
            trovate.append((mercato, nome, chiave, f, len(v), m, ic, positive, tot))
    return trovate


# ---------------------------------------------------------------
#  3. il nostro modello aggiunge qualcosa?
# ---------------------------------------------------------------

def mescola(q, p, w):
    """Media geometrica pesata: w=0 solo mercato, w=1 solo modello."""
    g = {k: max(q[k], 1e-9) ** (1 - w) * max(p[k], 1e-9) ** w for k in q}
    s = sum(g.values())
    return {k: v / s for k, v in g.items()}


def nostro_modello():
    if not os.path.exists(DB_PATH):
        print(f"  {DB_PATH} non trovato: lancia dalla cartella del progetto.")
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        righe = conn.execute("""
            SELECT a.p1, a.px, a.p2, a.q1, a.qx, a.q2, a.formazioni,
                   f.goals_home, f.goals_away
            FROM archivio_previsioni a JOIN fixtures f ON f.id = a.fixture_id
            WHERE a.q1 IS NOT NULL AND f.goals_home IS NOT NULL
              AND f.status IN ('FT','AET','PEN')
        """).fetchall()
    except sqlite3.OperationalError as e:
        print(f"  archivio non leggibile: {e}")
        return
    dati = []
    for p1, px, p2, q1, qx, q2, form, gc, ga in righe:
        if None in (p1, px, p2, q1, qx, q2):
            continue
        es = "1" if gc > ga else ("X" if gc == ga else "2")
        dati.append(({"1": q1, "X": qx, "2": q2}, {"1": p1, "X": px, "2": p2},
                     es, form or ""))
    print(f"  partite archiviate con quote e risultato: {len(dati)}")
    if len(dati) < 100:
        print("  Troppo poche: si rifa' fra qualche settimana.")
        return

    pesi = [i / 20 for i in range(21)]

    def perdita(riga, w):
        q, p, es, _ = riga
        return -math.log(max(mescola(q, p, w)[es], 1e-12))

    # il peso si sceglie su una parte delle partite e si prova sulle
    # altre, a turno: cosi' non si imbroglia scegliendolo col senno di poi
    random.seed(7)
    ordine = list(range(len(dati)))
    random.shuffle(ordine)
    gruppi = [ordine[i::5] for i in range(5)]
    mix, mercato, modello, scelti = [], [], [], []
    for i in range(5):
        prova = set(gruppi[i])
        allena = [dati[j] for j in ordine if j not in prova]
        w_best = min(pesi, key=lambda w: sum(perdita(r, w) for r in allena))
        scelti.append(w_best)
        for j in gruppi[i]:
            mix.append(perdita(dati[j], w_best))
            mercato.append(perdita(dati[j], 0.0))
            modello.append(perdita(dati[j], 1.0))

    w_tutti = min(pesi, key=lambda w: sum(perdita(r, w) for r in dati))
    diff = [a - b for a, b in zip(mercato, mix)]
    m, ic = V.media_ic(diff)
    print(f"\n  log loss, provata su partite non usate per scegliere il peso:")
    print(f"    solo quote di mercato     {sum(mercato)/len(mercato):.4f}")
    print(f"    solo nostro modello       {sum(modello)/len(modello):.4f}")
    print(f"    quote + modello mescolati {sum(mix)/len(mix):.4f}")
    print(f"\n  peso del nostro modello nella miscela migliore: {w_tutti:.0%}")
    print(f"  (scelto a turno: {', '.join(f'{w:.0%}' for w in scelti)})")
    print(f"  guadagno della miscela sul mercato: {m:+.4f} "
          f"[{ic[0]:+.4f}, {ic[1]:+.4f}]" if ic else "")
    if ic and ic[0] > 0:
        print("\n  IL NOSTRO MODELLO AGGIUNGE INFORMAZIONE AL MERCATO. Poca, ma")
        print("  vera: mescolato alle quote le migliora. Vale la pena usarlo cosi'")
        print("  nell'app, e cercare dove questa informazione si concentra.")
    elif ic and ic[1] < 0:
        print("\n  La miscela peggiora il mercato: il modello non aggiunge nulla.")
    else:
        print("\n  Non distinguibile: se il modello aggiunge qualcosa, e' troppo")
        print("  poco per vederlo con queste partite.")
        if len(dati) < 1500:
            print(f"  Attenzione: con {len(dati)} partite si vedrebbe solo un'informazione")
            print("  forte. Provato su dati costruiti apposta, un modello che sapeva")
            print("  davvero qualcosa in piu' del mercato non si distingueva ancora con")
            print("  700 partite. L'archivio cresce di circa 40 partite al giorno:")
            print("  la risposta vera arriva fra qualche mese.")


# ---------------------------------------------------------------

def main():
    print("=" * 110)
    print("0. DATI")
    print("=" * 110)
    partite = V.leggi()
    if not partite:
        print("  Nessun dato: lancia prima test_valore.py, che scarica i file.")
        return
    stagioni = sorted({p["stagione"] for p in partite})
    print(f"  partite: {len(partite)} in {len(stagioni)} stagioni")
    print("  Calcolo i prezzi giusti...", flush=True)
    V.prepara(partite)

    # ---- 1. effetto favorita-sfavorita -----------------------------
    print("\n" + "=" * 110)
    print("1. GIOCANDO SEMPRE TUTTO QUELLO CHE STA IN UNA FASCIA DI QUOTA: ESITO FINALE")
    print("=" * 110)
    print("  Ritorno per euro puntato, giocando OGNI esito la cui quota di chiusura di")
    print("  Pinnacle cade in quella fascia, alle quote di ciascun bookmaker.")
    print("  Se il mercato sbaglia sempre nello stesso verso, qui si vede.\n")
    tabella(partite, "1X2", FASCE_1X2, LIBRI_1X2)

    print("\n" + "=" * 110)
    print("1b. STESSA COSA SULL'OVER/UNDER 2.5")
    print("=" * 110)
    tabella(partite, "OU", FASCE_OU, LIBRI_OU)

    # ---- 2. i movimenti delle quote --------------------------------
    print("\n" + "=" * 110)
    print("2. I MOVIMENTI DELLE QUOTE DICONO QUALCOSA IN PIU'?")
    print("=" * 110)
    print("  Di quanto e' cambiata la probabilita' di Pinnacle fra prima e chiusura,")
    print("  e se l'esito e' uscito piu' o meno di quanto diceva la chiusura.")
    print("  Se 'realta' e 'chiusura' coincidono, il movimento non aggiunge niente:")
    print("  la quota finale sa gia' tutto.\n")
    movimenti = []
    for p in partite:
        pre, chi = V.giusto(p, "PS"), V.giusto(p, "PS_C")
        if not pre or not chi:
            continue
        for k in "HDA":
            movimenti.append((chi[k] - pre[k], chi[k], p["esito"] == k,
                              p.get("Max_C", {}).get(k), p.get("B365_C", {}).get(k),
                              p["PS"][k]))
    print(f"  {'movimento':<18} {'esiti':>7} {'chiusura':>9} {'realta':>8}  "
          f"{'giocando in chiusura':>22}  {'se lo avessi saputo prima':>26}")
    print(f"  {'':<18} {'':>7} {'':>9} {'':>8}  {'b365':>10} {'migliore':>11}  "
          f"{'(Pinnacle prima)':>26}")
    for lo, hi, et in ((-1, -0.04, "scesa oltre 4 punti"), (-0.04, -0.02, "scesa 2-4"),
                       (-0.02, -0.005, "scesa 0,5-2"), (-0.005, 0.005, "ferma"),
                       (0.005, 0.02, "salita 0,5-2"), (0.02, 0.04, "salita 2-4"),
                       (0.04, 1, "salita oltre 4 punti")):
        g = [x for x in movimenti if lo <= x[0] < hi]
        if len(g) < 50:
            continue
        chi = sum(x[1] for x in g) / len(g)
        reale = sum(1 for x in g if x[2]) / len(g)
        b365 = [(x[4] - 1) if x[2] else -1.0 for x in g if x[4] and x[4] <= QUOTA_MAX]
        mx = [(x[3] - 1) if x[2] else -1.0 for x in g if x[3] and x[3] <= QUOTA_MAX]
        prima = [(x[5] - 1) if x[2] else -1.0 for x in g if x[5] <= QUOTA_MAX]
        print(f"  {et:<18} {len(g):>7} {chi:>9.2%} {reale:>8.2%}  "
              f"{cella(b365):>10} {cella(mx):>11}  {cella(prima):>26}")
    print("\n  'salita' = la probabilita' e' cresciuta, cioe' la quota e' scesa.")
    print("  L'ultima colonna non si puo' giocare: dice quanto vale ANTICIPARE il")
    print("  mercato. E' esattamente il lavoro che dovrebbe fare un modello.")

    # ---- 3. il nostro modello --------------------------------------
    print("\n" + "=" * 110)
    print("3. IL NOSTRO MODELLO AGGIUNGE QUALCOSA ALLE QUOTE?")
    print("=" * 110)
    nostro_modello()

    # ---- 4. verdetto -----------------------------------------------
    print("\n" + "=" * 110)
    print("4. VERDETTO: C'E' UNA REGOLA CHE GUADAGNA, E REGGE NEL TEMPO?")
    print("=" * 110)
    trovate = (cerca_vincenti(partite, "1X2", FASCE_1X2, LIBRI_1X2, stagioni) +
               cerca_vincenti(partite, "OU", FASCE_OU, LIBRI_OU, stagioni))
    if not trovate:
        print("  Nessuna fascia, con nessun bookmaker, ha un guadagno dimostrato.")
        print("  Le quote da sole non contengono una regola che batte il margine.")
    else:
        print(f"  {'mercato':<8} {'bookmaker':<18} {'fascia':<13} {'giocate':>8}  "
              f"{'ritorno':<24} {'stagioni in attivo':>18}")
        for merc, nome, chiave, f, n, m, ic, pos, tot in sorted(trovate, key=lambda t: -t[5]):
            acc = " (IT)" if chiave in ACCESSIBILI else ""
            print(f"  {merc:<8} {nome + acc:<18} {etichetta_fascia(*f):<13} {n:>8}  "
                  f"{V.fmt_ic(m, ic):<24} {pos:>9} su {tot}")
        solide = [t for t in trovate if t[8] and t[7] / t[8] >= 0.75]
        print()
        if solide:
            print("  Regge in almeno tre stagioni su quattro:")
            for merc, nome, chiave, f, n, m, ic, pos, tot in solide:
                print(f"    - {merc} {nome}, quote {etichetta_fascia(*f)}: "
                      f"{m:+.1%} per euro, {n / max(len(stagioni), 1):.0f} giocate a stagione")
            print("\n  Prima di crederci: guarda se il bookmaker e' uno che puoi usare")
            print("  (IT), e ricorda che con tante righe qualcuna esce positiva per")
            print("  caso. Quello che regge in quasi tutte le stagioni e' piu' credibile.")
        else:
            print("  Nessuna di queste regge nel tempo: i guadagni vengono da poche")
            print("  stagioni fortunate. Non e' una regola, e' rumore.")
    print("=" * 110)


if __name__ == "__main__":
    main()
