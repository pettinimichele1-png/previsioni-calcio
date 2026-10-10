#!/usr/bin/env python3
"""
GIOCATORI, SOLO LA STAGIONE IN CORSO: BATTIAMO LA MEDIA DI RUOLO?
=================================================================
Fase 0 della prova sui mercati dei giocatori (tiri, tiri in porta, falli
commessi) nei cinque grandi campionati. Legge soltanto il database, non
scrive niente e non chiama l'API.

Come test_giocatori.py, ma:
  - solo la stagione 2026/27 (decisione dell'utente: chi ha cambiato
    squadra o ruolo falserebbe la storia);
  - un modello in piu' che tiene conto dell'AVVERSARIO;
  - i minuti come si conoscono prima della partita: si giudicano solo i
    TITOLARI, e si usano i minuti che quel giocatore gioca di solito da
    titolare, non quelli che ha giocato davvero.

I modelli, tutti "eventi ogni 90 minuti" per i minuti attesi, con la
distribuzione di Poisson sulle soglie che Bet365 quota (almeno 1, 2, 3):
  ruolo        media del suo ruolo nel suo campionato
  giocatore    la sua media, tirata verso quella del ruolo (smorzata)
  + avversario la media smorzata, per quanto l'avversario concede
               (tiri subiti, o falli subiti per i falli commessi)

REGOLA DECISA PRIMA: si passa alla fase 1 solo se "giocatore" o
"+ avversario" batte "ruolo" in modo dimostrato (forbice al 95% tutta
a favore) su almeno uno dei tre mercati.

USO, dalla cartella del progetto:
    python3 scripts/test_giocatori_stagione.py
"""

import math
import random
import sqlite3
from collections import defaultdict

DB_PATH = "calcio_dati.db"
STAGIONE = 2026
LEGHE = (39, 140, 135, 78, 61)
MERCATI = (("tiri", "TIRI", "tiri"), ("tiri_porta", "TIRI IN PORTA", "tiri"),
           ("falli_fatti", "FALLI COMMESSI", "falli_subiti"))
MIN_PRECEDENTI = 3          # partite precedenti del giocatore
MIN_DA_TITOLARE = 2         # per stimare i minuti da titolare
QUOTA_TEST = 0.33
SOGLIE = (0.5, 1.5, 2.5)
SMORZAMENTI = (1.0, 2.0, 3.0, 5.0, 8.0)
SMORZA_AVV = (2.0, 4.0, 8.0, 16.0)
RICAMPIONAMENTI = 2000
random.seed(23)


def carica(conn):
    righe = conn.execute(f"""
        SELECT pm.fixture_id, pm.player_id, pm.team_id, pm.league_id, pm.date,
               pm.minuti, pm.ruolo, pm.titolare, pm.tiri, pm.tiri_porta,
               pm.falli_fatti, pm.falli_subiti, f.home_team_id, f.away_team_id
        FROM player_match pm JOIN fixtures f ON f.id = pm.fixture_id
        WHERE pm.season = ? AND pm.league_id IN ({",".join("?" * len(LEGHE))})
          AND pm.minuti IS NOT NULL AND pm.minuti > 0
        ORDER BY pm.date, pm.fixture_id
    """, (STAGIONE,) + LEGHE).fetchall()
    out = []
    for r in righe:
        avversario = r[13] if r[2] == r[12] else r[12]
        out.append({"fid": r[0], "pid": r[1], "squadra": r[2], "lega": r[3], "data": r[4],
                    "minuti": r[5], "ruolo": r[6] or "?", "titolare": bool(r[7]),
                    "tiri": r[8] or 0, "tiri_porta": r[9] or 0, "falli_fatti": r[10] or 0,
                    "falli_subiti": r[11] or 0, "avversario": avversario})
    return out


def poisson_oltre(lam, soglia):
    termine = somma = math.exp(-lam)
    for k in range(1, int(soglia) + 1):
        termine *= lam / k
        somma += termine
    return max(1e-9, min(1 - 1e-9, 1 - somma))


def log_perdita(p, avvenuto):
    return -math.log(p if avvenuto else 1 - p)


def media(v):
    return sum(v) / len(v) if v else float("nan")


def confronto(a, b):
    """Differenza media di log loss (positivo = vince il secondo), forbice al 95%."""
    d = [x - y for x, y in zip(a, b)]
    n = len(d)
    if n < 30:
        return None
    medie = sorted(sum(d[random.randrange(n)] for _ in range(n)) / n for _ in range(RICAMPIONAMENTI))
    return media(d), medie[int(.025 * RICAMPIONAMENTI)], medie[int(.975 * RICAMPIONAMENTI)]


def prepara(dati, campo, campo_avv):
    """
    Per ogni giocatore titolare: medie di ruolo, del giocatore e
    dell'avversario calcolate SOLO sulle partite precedenti.
    """
    storico = defaultdict(list)                     # pid -> [(eventi, novanta, titolare)]
    ruolo_ev, ruolo_90 = defaultdict(float), defaultdict(float)
    # quanto concede ogni squadra: eventi degli avversari (campo) per partita,
    # o per i falli i falli subiti dagli avversari... cioe' quanti falli
    # "provoca" la squadra che si affronta: per i falli commessi conta quanti
    # falli subisce di solito l'avversario
    conc_ev, conc_partite = defaultdict(float), defaultdict(int)
    lega_ev, lega_partite = defaultdict(float), defaultdict(int)
    fuori = []
    per_partita = defaultdict(list)
    for r in dati:
        per_partita[r["fid"]].append(r)
    for fid in sorted(per_partita, key=lambda f: per_partita[f][0]["data"]):
        righe = per_partita[fid]
        for r in righe:
            prec = storico[r["pid"]]
            kr = (r["lega"], r["ruolo"])
            da_tit = [x[1] for x in prec if x[2]]
            if (r["titolare"] and len(prec) >= MIN_PRECEDENTI and len(da_tit) >= MIN_DA_TITOLARE
                    and ruolo_90[kr] >= 20 and lega_partite[r["lega"]] >= 10):
                mu_lega = lega_ev[r["lega"]] / lega_partite[r["lega"]]
                fuori.append({**r, "n": r[campo], "mu_ruolo": ruolo_ev[kr] / ruolo_90[kr],
                              "eventi": sum(x[0] for x in prec), "novanta_prima": sum(x[1] for x in prec),
                              "novanta_attesi": sum(da_tit) / len(da_tit),
                              "conc": conc_ev[r["avversario"]], "conc_n": conc_partite[r["avversario"]],
                              "mu_lega": mu_lega})
        # dopo la partita: si aggiornano gli storici
        squadre = defaultdict(float)
        for r in righe:
            novanta = r["minuti"] / 90.0
            storico[r["pid"]].append((r[campo], novanta, r["titolare"]))
            ruolo_ev[(r["lega"], r["ruolo"])] += r[campo]
            ruolo_90[(r["lega"], r["ruolo"])] += novanta
            squadre[r["squadra"]] += r[campo_avv]
        ids = list(squadre)
        if len(ids) == 2:
            for a, b in ((ids[0], ids[1]), (ids[1], ids[0])):
                # quanto "concede" b: eventi della squadra a contro b (per i
                # falli commessi: i falli subiti da b, cioe' quanti ne provoca)
                if campo_avv == campo:
                    conc_ev[b] += squadre[a]
                else:
                    conc_ev[b] += squadre[b]
                conc_partite[b] += 1
            lega = righe[0]["lega"]
            lega_ev[lega] += sum(squadre.values()) / 2
            lega_partite[lega] += 1
    return fuori


def lam(r, modello, k, k_avv):
    novanta = max(0.3, r["novanta_attesi"])
    ruolo = r["mu_ruolo"]
    if modello == "ruolo":
        return max(0.02, ruolo * novanta)
    smorzata = (r["eventi"] + k * ruolo) / (r["novanta_prima"] + k)
    if modello == "giocatore":
        return max(0.02, smorzata * novanta)
    # avversario: rapporto fra quanto concede e la media del campionato, smorzato
    fattore = (r["conc"] + k_avv * r["mu_lega"]) / (r["conc_n"] * r["mu_lega"] + k_avv * r["mu_lega"])
    return max(0.02, smorzata * fattore * novanta)


def perdite(righe, modello, k, k_avv):
    out = []
    for r in righe:
        l = lam(r, modello, k, k_avv)
        out.append(media([log_perdita(poisson_oltre(l, s), r["n"] > s) for s in SOGLIE]))
    return out


def soglia_perdite(righe, modello, k, k_avv, s):
    return [log_perdita(poisson_oltre(lam(r, modello, k, k_avv), s), r["n"] > s) for r in righe]


def verdetto(c):
    if not c:
        return "troppo pochi casi"
    _, lo, hi = c
    return "VINCE IL MODELLO, dimostrato" if lo > 0 else ("vince il ruolo, dimostrato" if hi < 0 else "non distinguibili")


def main():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    dati = carica(conn)
    conn.close()
    partite = len({r["fid"] for r in dati})
    print("=" * 78)
    print(f"GIOCATORI, STAGIONE {STAGIONE}/{STAGIONE % 100 + 1}, CINQUE GRANDI CAMPIONATI")
    print("=" * 78)
    print(f"partite: {partite}   righe giocatore-partita: {len(dati)}")
    passa = False
    for campo, etichetta, campo_avv in MERCATI:
        righe = prepara(dati, campo, campo_avv)
        if len(righe) < 300:
            print(f"\n{etichetta}: solo {len(righe)} casi utilizzabili, troppo pochi.")
            continue
        taglio = int(len(righe) * (1 - QUOTA_TEST))
        allen, test = righe[:taglio], righe[taglio:]
        k = min(SMORZAMENTI, key=lambda x: media(perdite(allen, "giocatore", x, 4.0)))
        k_avv = min(SMORZA_AVV, key=lambda x: media(perdite(allen, "avversario", k, x)))
        base = perdite(test, "ruolo", k, k_avv)
        print(f"\n{etichetta}  (titolari; casi per scegliere {len(allen)}, per giudicare {len(test)}, "
              f"dal {test[0]['data'][:10]})")
        print(f"  media vera {media([r['n'] for r in test]):.2f} a partita   smorzamento {k}, avversario {k_avv}")
        print(f"  log loss   ruolo {media(base):.4f}   giocatore {media(perdite(test, 'giocatore', k, k_avv)):.4f}"
              f"   + avversario {media(perdite(test, 'avversario', k, k_avv)):.4f}")
        for modello in ("giocatore", "avversario"):
            c = confronto(base, perdite(test, modello, k, k_avv))
            testo = verdetto(c)
            passa = passa or testo.startswith("VINCE")
            print(f"  {modello:<11} contro ruolo: {c[0]:+.4f} [{c[1]:+.4f}, {c[2]:+.4f}] -> {testo}"
                  if c else f"  {modello:<11} contro ruolo: {testo}")
        print("  per soglia (guadagno di '+ avversario' sul ruolo):")
        for s in SOGLIE:
            succ = media([1.0 if r["n"] > s else 0.0 for r in test])
            c = confronto(soglia_perdite(test, "ruolo", k, k_avv, s), soglia_perdite(test, "avversario", k, k_avv, s))
            print(f"    almeno {int(s + .5)}: succede {succ:.0%}   "
                  + (f"{c[0]:+.4f} [{c[1]:+.4f}, {c[2]:+.4f}] -> {verdetto(c)}" if c else "troppo pochi casi"))
    print("\n" + "=" * 78)
    print("VERDETTO DELLA FASE 0: " + ("si passa alla fase 1." if passa else
          "il giocatore non batte il ruolo: per ora ci si ferma (da ripetere con piu' giornate)."))


if __name__ == "__main__":
    main()
