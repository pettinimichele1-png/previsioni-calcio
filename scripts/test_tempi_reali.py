#!/usr/bin/env python3
"""
I TEMPI, CON I GOL ATTESI VERI

Il test precedente (test_tempi.py) ricostruiva i gol attesi con una
versione semplificata del motore: niente xG, niente formazioni,
niente indicatori dell'allenatore. L'obiezione legittima era che il
motore vero e' migliore, e che quindi il metodo B - gol attesi per la
quota di primo tempo del campionato - partirebbe da una base piu'
solida di quella che gli abbiamo dato.

Qui si usa il motore vero. Nell'archivio delle verifiche ci sono i
gol attesi che il motore ha effettivamente prodotto in produzione,
partita per partita: quelli, non una ricostruzione.

Le partite sono meno, perche' l'archivio parte da quando abbiamo
iniziato a verificare. In cambio i numeri sono quelli giusti.

IL CONTROLLO CHE VALIDA IL CONFRONTO. Prima di guardare i tempi si
verifica che i gol attesi veri siano davvero migliori di quelli
ricostruiti sul risultato FINALE, dove sappiamo di funzionare. Se non
lo fossero nemmeno li', l'obiezione cadrebbe da sola e il confronto
sui tempi non direbbe niente di nuovo.

Uso:
    python3 scripts/test_tempi_reali.py
"""

import math
import sqlite3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_tempi as T

DB_PATH = "calcio_dati.db"


def gol_attesi_veri(conn):
    """I gol attesi prodotti dal motore in produzione, per partita."""
    esiste = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='archivio_previsioni'").fetchone()
    if not esiste:
        return {}
    righe = conn.execute("""
        SELECT fixture_id, gol_attesi_casa, gol_attesi_fuori
        FROM archivio_previsioni
        WHERE gol_attesi_casa IS NOT NULL AND gol_attesi_fuori IS NOT NULL
    """).fetchall()
    return {r[0]: (r[1], r[2]) for r in righe}


def riga(nome, perdite, riferimento, etichetta_rif="A"):
    c = T.confronto(perdite[riferimento], perdite[nome])
    if c is None:
        return f"  {nome:<34} dati insufficienti"
    m, lo, hi = c
    esito = (f"MEGLIO di {etichetta_rif}" if lo > 0 else
             f"peggio di {etichetta_rif}" if hi < 0 else "non distinguibile")
    return (f"  {nome:<34} {T.media(perdite[nome]):.4f}  "
            f"guadagno {m:+.4f} [{lo:+.4f}, {hi:+.4f}]  {esito}")


def main():
    conn = sqlite3.connect(DB_PATH)
    partite, _ = T.carica(conn)
    veri = gol_attesi_veri(conn)

    print("=" * 78)
    print("1. QUANTE PARTITE HANNO I GOL ATTESI VERI")
    print("=" * 78)
    print(f"  partite con parziale al 45':        {len(partite)}")
    print(f"  partite nell'archivio previsioni:   {len(veri)}")
    if not veri:
        print("\n  L'archivio delle previsioni e' vuoto: niente da confrontare.")
        return

    # medie e quote si calcolano su TUTTO lo storico, sempre guardando
    # indietro: e' il riferimento, non una previsione
    medie1 = T.medie_espansive(partite, 0)
    medie2 = T.medie_espansive(partite, 1)
    medie_ft = {}
    for p in partite:
        a, b = medie1.get(p["fid"]), medie2.get(p["fid"])
        medie_ft[p["fid"]] = (a[0] + b[0], a[1] + b[1]) if a and b else None
    forze_ft = T.forze([dict(p, casa=[p["casa_ft"], 0], ospite=[p["ospite_ft"], 0])
                        for p in partite], medie_ft, 0)
    quota_lega, _ = T.quote_tempo(partite)

    # si tengono solo le partite dove TUTTI i metodi sono calcolabili,
    # altrimenti il confronto non sarebbe alla pari
    usabili = []
    for p in partite:
        if p["fid"] not in veri:
            continue
        kc, ko = (p["fid"], "casa"), (p["fid"], "ospite")
        if kc not in forze_ft or ko not in forze_ft:
            continue
        if not medie1.get(p["fid"]) or not medie2.get(p["fid"]):
            continue
        if quota_lega.get(p["fid"]) is None:
            continue
        usabili.append(p)

    print(f"  partite utilizzabili nel confronto: {len(usabili)}")
    if len(usabili) < 150:
        print("\n  Troppo poche: le fasce di incertezza sarebbero cosi' larghe")
        print("  da non distinguere nulla. Conviene rifare il test fra qualche")
        print("  settimana, quando l'archivio sara' piu' grande.")
        if len(usabili) < 60:
            return

    perdite = {k: {} for k in (
        "Over 0.5 primo tempo", "Over 1.5 primo tempo", "Gol/NoGol primo tempo",
        "1X2 primo tempo", "Over 0.5 secondo tempo", "Over 1.5 secondo tempo",
        "CONTROLLO Over 2.5 finale", "CONTROLLO Gol/NoGol finale")}
    previste = {k: {} for k in perdite}
    reali = {k: [] for k in perdite}
    nomi = ["A  media del campionato",
            "B  gol attesi ricostruiti",
            "B* gol attesi VERI del motore"]
    for m in perdite:
        for n in nomi:
            perdite[m][n] = []
            previste[m][n] = []

    for p in usabili:
        fid = p["fid"]
        m1, m2, mft = medie1[fid], medie2[fid], medie_ft[fid]
        ql = quota_lega[fid]
        acf, dcf, _ = forze_ft[(fid, "casa")]
        aof, dof, _ = forze_ft[(fid, "ospite")]

        lam_ric = (acf * dof * mft[0], aof * dcf * mft[1])
        lam_vero = veri[fid]

        modelli = {
            nomi[0]: ((m1[0], m1[1]), (m2[0], m2[1]), (mft[0], mft[1])),
            nomi[1]: ((lam_ric[0] * ql, lam_ric[1] * ql),
                      (lam_ric[0] * (1 - ql), lam_ric[1] * (1 - ql)), lam_ric),
            nomi[2]: ((lam_vero[0] * ql, lam_vero[1] * ql),
                      (lam_vero[0] * (1 - ql), lam_vero[1] * (1 - ql)), lam_vero),
        }

        gc1, go1 = p["casa"][0], p["ospite"][0]
        gc2, go2 = p["casa"][1], p["ospite"][1]
        gcf, gof = p["casa_ft"], p["ospite_ft"]
        esito1 = "1" if gc1 > go1 else ("X" if gc1 == go1 else "2")

        for nome, ((l1c, l1o), (l2c, l2o), (lfc, lfo)) in modelli.items():
            M1, M2, MF = T.matrice(l1c, l1o), T.matrice(l2c, l2o), T.matrice(lfc, lfo, 9)
            gg1 = sum(M1[x][y] for x in range(1, len(M1)) for y in range(1, len(M1)))
            ggf = sum(MF[x][y] for x in range(1, len(MF)) for y in range(1, len(MF)))
            binari = {
                "Over 0.5 primo tempo": (T.over(M1, 0.5), gc1 + go1 >= 1),
                "Over 1.5 primo tempo": (T.over(M1, 1.5), gc1 + go1 >= 2),
                "Gol/NoGol primo tempo": (gg1, gc1 > 0 and go1 > 0),
                "Over 0.5 secondo tempo": (T.over(M2, 0.5), gc2 + go2 >= 1),
                "Over 1.5 secondo tempo": (T.over(M2, 1.5), gc2 + go2 >= 2),
                # il controllo: qui i gol attesi veri DEVONO vincere
                "CONTROLLO Over 2.5 finale": (T.over(MF, 2.5), gcf + gof >= 3),
                "CONTROLLO Gol/NoGol finale": (ggf, gcf > 0 and gof > 0),
            }
            for mercato, (prob, avvenuto) in binari.items():
                perdite[mercato][nome].append(T.perdita(prob, avvenuto))
                previste[mercato][nome].append(prob)
            p1, px, p2 = T.esiti(M1)
            perdite["1X2 primo tempo"][nome].append(
                -math.log(max({"1": p1, "X": px, "2": p2}[esito1], 1e-15)))

        for mercato, avvenuto in (
                ("Over 0.5 primo tempo", gc1 + go1 >= 1),
                ("Over 1.5 primo tempo", gc1 + go1 >= 2),
                ("Gol/NoGol primo tempo", gc1 > 0 and go1 > 0),
                ("Over 0.5 secondo tempo", gc2 + go2 >= 1),
                ("Over 1.5 secondo tempo", gc2 + go2 >= 2),
                ("CONTROLLO Over 2.5 finale", gcf + gof >= 3),
                ("CONTROLLO Gol/NoGol finale", gcf > 0 and gof > 0)):
            reali[mercato].append(avvenuto)

    # ---- il controllo, prima di tutto ----------------------------
    print("\n" + "=" * 78)
    print("2. CONTROLLO: I GOL ATTESI VERI SONO DAVVERO MIGLIORI?")
    print("=" * 78)
    print("  Sul risultato FINALE, dove sappiamo di funzionare. Se qui i gol")
    print("  attesi veri non battono quelli ricostruiti, l'obiezione cade da")
    print("  sola e il resto del test non aggiunge niente.\n")
    controllo_ok = False
    for mercato in ("CONTROLLO Over 2.5 finale", "CONTROLLO Gol/NoGol finale"):
        print(f"  {mercato[10:].upper()}")
        print(f"  {nomi[1]:<34} {T.media(perdite[mercato][nomi[1]]):.4f}  (riferimento)")
        print(riga(nomi[2], perdite[mercato], nomi[1], "i ricostruiti"))
        c = T.confronto(perdite[mercato][nomi[1]], perdite[mercato][nomi[2]])
        if c and c[1] > 0:
            controllo_ok = True
        print()
    print("  " + ("I gol attesi veri sono migliori: il confronto sui tempi ha senso."
                  if controllo_ok else
                  "I gol attesi veri NON risultano migliori su questo campione:\n"
                  "  probabilmente e' troppo piccolo. Leggere il resto con prudenza."))

    # ---- i tempi -------------------------------------------------
    print("\n" + "=" * 78)
    print("3. I TEMPI, CON I GOL ATTESI VERI")
    print("=" * 78)
    print("  Il riferimento e' sempre la media del campionato. Perche' valga")
    print("  la pena costruire i mercati sui tempi, B* deve batterla.\n")
    for mercato in ("Over 0.5 primo tempo", "Over 1.5 primo tempo",
                    "Gol/NoGol primo tempo", "1X2 primo tempo",
                    "Over 0.5 secondo tempo", "Over 1.5 secondo tempo"):
        print(f"  {mercato.upper()}")
        print(f"  {nomi[0]:<34} {T.media(perdite[mercato][nomi[0]]):.4f}  (riferimento)")
        for n in nomi[1:]:
            print(riga(n, perdite[mercato], nomi[0]))
        print()

    # ---- taratura ------------------------------------------------
    print("=" * 78)
    print("4. LE PERCENTUALI SONO ONESTE?")
    print("=" * 78)
    print(f"  {'mercato':<30} {'realta':>7}  " +
          "  ".join(f"{n.split()[0]:>7}" for n in nomi))
    for mercato in ("Over 0.5 primo tempo", "Over 1.5 primo tempo",
                    "Gol/NoGol primo tempo", "Over 0.5 secondo tempo",
                    "Over 1.5 secondo tempo", "CONTROLLO Over 2.5 finale"):
        v = reali[mercato]
        if not v:
            continue
        reale = sum(v) / len(v)
        colonne = []
        for n in nomi:
            m = T.media(previste[mercato][n])
            tol = 2 * math.sqrt(max(m * (1 - m), 1e-9) / len(v))
            colonne.append(f"{m:>6.1%}" + (" " if abs(m - reale) <= tol else "*"))
        print(f"  {mercato:<30} {reale:>6.1%}   " + "   ".join(colonne))
    print("\n  L'asterisco segnala uno scarto troppo grande per essere caso.")

    print("\n" + "=" * 78)
    print("COME LEGGERE")
    print("  - Se B* batte A in modo dimostrato su Over 0.5 e Over 1.5 di")
    print("    primo tempo, i mercati sui tempi si possono fare: basta")
    print("    moltiplicare i gol attesi per la quota del campionato.")
    print("  - Se B* resta dietro ad A come i ricostruiti, la questione e'")
    print("    chiusa: non e' colpa della base di partenza, e' che sui tempi")
    print("    non abbiamo niente da dire.")
    print("  - Se il campione e' piccolo e tutto risulta non distinguibile,")
    print("    non e' un no: e' un 'troppo presto'. Si rifa' fra un mese.")
    print("=" * 78)


if __name__ == "__main__":
    main()
