"""
CORREZIONE PER CAMBIO DI DIVISIONE
===================================
IL PROBLEMA
-----------
Il confronto con le quote ha mostrato errori grossi sulle neopromosse:
    Venezia - Fiorentina    noi 65% al Venezia, il mercato 32%
    Chaves - Feirense       noi 68%,            il mercato 43%

La causa: le loro 10 partite vengono in buona parte dalla serie
inferiore, dove dominavano. Noi normalizziamo sulla media di QUEL
campionato, quindi un attacco 1,5 rispetto alla Serie B diventa un
attacco 1,5 applicato alla Serie A. Ma non e' la stessa cosa.

LA STIMA
--------
Quanto si comprime la forza cambiando categoria? Lo misuriamo dalle
squadre che nell'archivio hanno partite in due divisioni diverse:

    forza_nuova - 1 = c * (forza_vecchia - 1) + d

    c  quanto si conserva del vantaggio (atteso sotto 1: ci si
       avvicina alla media salendo di categoria)
    d  spostamento medio (atteso negativo per chi sale, positivo
       per chi scende)

I coefficienti si stimano separatamente per promozioni e retrocessioni,
perche' non sono simmetriche.

NON e' un'ipotesi imposta: se dai dati uscisse c vicino a 1 e d vicino
a 0, vorrebbe dire che il cambio di categoria non comprime nulla, e lo
sapremmo.

USO:
    python correzione_divisione.py
"""

import sqlite3
import os
import json
import math
import random

DB_PATH = "calcio_dati.db"
USCITA = "correzione_divisione.json"
MIN_PARTITE_GRUPPO = 3
SEED = 55
N_BOOTSTRAP = 4000


def minimi_quadrati(x, y):
    """Retta y = a + b*x."""
    n = len(x)
    if n < 3:
        return None, None
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((v - mx) ** 2 for v in x)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    if sxx < 1e-12:
        return None, None
    b = sxy / sxx
    a = my - b * mx
    return a, b


def intervallo(v, livello=0.95):
    v = sorted(v)
    return v[int(len(v) * (1 - livello) / 2)], v[int(len(v) * (1 - (1 - livello) / 2))]


def main():
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} non trovato.")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # squadra -> campionato attuale
    cur.execute("SELECT id, league_id, name FROM teams")
    attuale = {r[0]: r[1] for r in cur.fetchall()}
    cur.execute("SELECT id, name FROM teams")
    nomi = dict(cur.fetchall())
    cur.execute("SELECT id, country || ' - ' || name FROM leagues")
    nomi_lega = dict(cur.fetchall())

    # medie di ogni campionato-stagione
    cur.execute("""
        SELECT league_id, season, AVG(goals_for), COUNT(*)
        FROM team_match WHERE goals_for IS NOT NULL
        GROUP BY league_id, season
    """)
    medie = {(l, s): m for l, s, m, n in cur.fetchall() if n >= 20}

    # rendimento di ogni squadra in ogni campionato-stagione
    cur.execute("""
        SELECT team_id, league_id, season, AVG(goals_for), AVG(goals_against), COUNT(*)
        FROM team_match WHERE goals_for IS NOT NULL
        GROUP BY team_id, league_id, season
    """)
    gruppi = {}
    for tid, lid, sea, gf, ga, n in cur.fetchall():
        if n < MIN_PARTITE_GRUPPO or (lid, sea) not in medie:
            continue
        m = medie[(lid, sea)]
        if m <= 0:
            continue
        gruppi.setdefault(tid, []).append({
            "lega": lid, "stagione": sea, "n": n,
            "att": gf / m, "dif": ga / m})

    # accoppio: gruppo nel campionato attuale <-> gruppo in un altro
    coppie = []
    for tid, lista in gruppi.items():
        lega_ora = attuale.get(tid)
        nuovo = next((g for g in lista if g["lega"] == lega_ora), None)
        if not nuovo:
            continue
        for vecchio in lista:
            if vecchio["lega"] == lega_ora:
                continue
            coppie.append({
                "team_id": tid, "nome": nomi.get(tid, str(tid)),
                "lega_vecchia": vecchio["lega"], "lega_nuova": lega_ora,
                "att_v": vecchio["att"], "att_n": nuovo["att"],
                "dif_v": vecchio["dif"], "dif_n": nuovo["dif"],
                "n_v": vecchio["n"], "n_n": nuovo["n"],
                # se il vecchio campionato non e' fra i nostri, e' quasi
                # sempre una categoria inferiore
                "ignoto": vecchio["lega"] not in nomi_lega,
            })

    print(f"Squadre con partite in due divisioni: {len(coppie)}")
    if len(coppie) < 15:
        print("Troppo poche per stimare la correzione in modo affidabile.")
        conn.close()
        return

    # promozione o retrocessione? lo deduco dal rendimento:
    # chi arriva da una categoria inferiore vi era piu' forte della media
    print("\n" + "=" * 68)
    print("SQUADRE CHE HANNO CAMBIATO DIVISIONE")
    print("=" * 68)
    print(f"{'squadra':<24} {'att prima':>10} {'att dopo':>10} {'campionato precedente':>22}")
    print("-" * 68)
    for c in sorted(coppie, key=lambda x: -x["att_v"])[:14]:
        lega = nomi_lega.get(c["lega_vecchia"], f"id={c['lega_vecchia']} (fuori scope)")
        print(f"{c['nome'][:23]:<24} {c['att_v']:>10.2f} {c['att_n']:>10.2f} {lega[:21]:>22}")

    # ---- stima -----------------------------------------------------
    print("\n" + "=" * 68)
    print("COMPRESSIONE DELLA FORZA")
    print("=" * 68)

    risultati = {}
    for etichetta, campo_v, campo_n in [("attacco", "att_v", "att_n"),
                                        ("difesa", "dif_v", "dif_n")]:
        x = [c[campo_v] - 1 for c in coppie]
        y = [c[campo_n] - 1 for c in coppie]
        d, c_ = minimi_quadrati(x, y)
        if c_ is None:
            continue

        random.seed(SEED)
        idx = list(range(len(x)))
        cs, ds = [], []
        for _ in range(N_BOOTSTRAP):
            campione = [random.choice(idx) for _ in idx]
            dd, cc = minimi_quadrati([x[i] for i in campione], [y[i] for i in campione])
            if cc is not None:
                cs.append(cc); ds.append(dd)
        lo_c, hi_c = intervallo(cs)
        lo_d, hi_d = intervallo(ds)

        risultati[etichetta] = {"c": c_, "d": d, "ic_c": [lo_c, hi_c], "ic_d": [lo_d, hi_d]}
        print(f"\n{etichetta.upper()}")
        print(f"  conservazione c : {c_:+.3f}   intervallo 95% [{lo_c:+.3f}, {hi_c:+.3f}]")
        print(f"  spostamento  d : {d:+.3f}   intervallo 95% [{lo_d:+.3f}, {hi_d:+.3f}]")
        if hi_c < 1.0:
            print(f"  -> la forza SI COMPRIME: se ne conserva circa il {c_*100:.0f}%")
        elif lo_c > 1.0:
            print("  -> la forza si amplifica (risultato inatteso, da guardare)")
        else:
            print("  -> compressione non dimostrata: l'intervallo include 1")

    # ---- esempio pratico -------------------------------------------
    if "attacco" in risultati:
        c_ = risultati["attacco"]["c"]
        d = risultati["attacco"]["d"]
        print("\n" + "=" * 68)
        print("EFFETTO PRATICO SULL'ATTACCO")
        print("=" * 68)
        print(f"{'forza nella vecchia categoria':>30} {'-> stimata nella nuova':>26}")
        print("-" * 60)
        for v in (1.6, 1.4, 1.2, 1.0, 0.8):
            print(f"{v:>30.2f} {1 + c_*(v-1) + d:>26.2f}")

    with open(USCITA, "w", encoding="utf-8") as f:
        json.dump({"coppie_usate": len(coppie), "coefficienti": risultati},
                  f, ensure_ascii=False, indent=1)
    print(f"\nCoefficienti salvati in {USCITA}")
    print("Verranno usati da indicatori.py per correggere le partite")
    print("giocate in una divisione diversa da quella attuale.")

    conn.close()


if __name__ == "__main__":
    main()
