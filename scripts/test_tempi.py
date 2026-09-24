#!/usr/bin/env python3
"""
SAPPIAMO PREVEDERE I DUE TEMPI?

Sui bookmaker ci sono molti mercati sul primo e sul secondo tempo:
Over/Under di tempo, 1X2 di tempo, Primo tempo/Finale, segna in
entrambi i tempi. Prima di metterli nell'app bisogna sapere se li
sappiamo prevedere, e questo test lo verifica senza toccare il motore.

IL DATO C'E' GIA'. Quando archiviamo una partita salviamo la risposta
intera dell'API, e li' dentro c'e' il punteggio al 45'. Il secondo
tempo e' la differenza fra finale e parziale. Quindi non serve
nemmeno una chiamata in piu': lo storico e' tutto in casa.

COSA CONFRONTA. Quattro modi di stimare i gol attesi di un tempo,
dal piu' stupido al piu' elaborato:

  A  la media del campionato in quel tempo, e basta;
  B  i gol attesi della partita intera, moltiplicati per la quota di
     gol che in quel campionato cade in quel tempo;
  C  come B, ma con la quota di ogni singola squadra invece che
     quella del campionato;
  D  un modello dedicato, con attacco e difesa ricalcolati sui soli
     gol di quel tempo.

Se A regge il confronto con gli altri tre, vuol dire che sui tempi
non abbiamo niente da dire e i mercati non vanno messi. E' lo stesso
esito che abbiamo avuto sui corner.

TUTTO FUORI CAMPIONE. Ogni partita viene prevista usando solo le
partite giocate PRIMA: medie del campionato, forze delle squadre e
quote dei tempi si calcolano sempre guardando indietro. Una partita
non partecipa mai alla propria previsione.

Uso:
    python3 scripts/test_tempi.py
"""

import json
import math
import sqlite3
from collections import defaultdict

DB_PATH = "calcio_dati.db"

MIN_PARTITE = 6          # storia minima di una squadra per entrare nel test
SHRINK = 8               # quanto si tira la stima verso la media (come il motore)
DECADIMENTO = 0.95       # peso delle partite vecchie (come il motore)
PESO_STAGIONE_PRECEDENTE = 0.3
MIN_LEGA = 40            # partite minime di un campionato prima di fidarsi
SHRINK_QUOTA = 20        # la quota di tempo di una squadra e' rumorosa: si tira forte


# ---------------------------------------------------------------
#  dati
# ---------------------------------------------------------------

def carica(conn):
    """Una riga per squadra-partita, con i gol dei due tempi."""
    righe = conn.execute("""
        SELECT id, league_id, season, date,
               home_team_id, away_team_id,
               goals_home, goals_away, raw_json
        FROM fixtures
        WHERE goals_home IS NOT NULL AND goals_away IS NOT NULL
        ORDER BY date
    """).fetchall()

    partite, senza_parziale = [], 0
    for fid, lega, stagione, data, cid, oid, gc, ga, grezzo in righe:
        try:
            ht = (json.loads(grezzo).get("score") or {}).get("halftime") or {}
        except Exception:
            ht = {}
        hc, ha = ht.get("home"), ht.get("away")
        if hc is None or ha is None:
            senza_parziale += 1
            continue
        # un parziale piu' alto del finale sarebbe un dato sbagliato
        if hc > gc or ha > ga:
            senza_parziale += 1
            continue
        partite.append({
            "fid": fid, "lega": lega, "stagione": stagione, "data": data,
            "casa_id": cid, "ospite_id": oid,
            # [primo tempo, secondo tempo] per ciascuna squadra
            "casa": [hc, gc - hc], "ospite": [ha, ga - ha],
            "casa_ft": gc, "ospite_ft": ga,
        })
    return partite, senza_parziale


def medie_espansive(partite, tempo):
    """
    Media gol del campionato in quel tempo, casa e ospite separate,
    calcolata solo sulle giornate precedenti. Serve da riferimento:
    tutte le forze sono rapporti rispetto a questa.
    """
    per_lega = defaultdict(list)
    for p in partite:
        per_lega[p["lega"]].append(p)
    fuori = {}
    for lista in per_lega.values():
        sc = so = conta = 0.0
        buffer, data_prec = [], None
        for p in lista:
            if data_prec is not None and p["data"] != data_prec:
                for b in buffer:
                    sc += b["casa"][tempo]
                    so += b["ospite"][tempo]
                    conta += 1
                buffer = []
            fuori[p["fid"]] = ((sc / conta, so / conta)
                               if conta >= MIN_LEGA else None)
            buffer.append(p)
            data_prec = p["data"]
    return fuori


def storie(partite):
    """Le partite di ogni squadra, in ordine di data."""
    fuori = defaultdict(list)
    for p in partite:
        fuori[p["casa_id"]].append((p, "casa", "ospite"))
        fuori[p["ospite_id"]].append((p, "ospite", "casa"))
    return fuori


def _pesi(precedenti, stagione_ora):
    for k, (p, mio, suo) in enumerate(precedenti):
        yield k, p, mio, suo, ((DECADIMENTO ** k) *
                               (1.0 if p["stagione"] == stagione_ora
                                else PESO_STAGIONE_PRECEDENTE))


def forze(partite, medie, tempo):
    """
    Attacco e difesa di ogni squadra in quel tempo: quanto segna e
    quanto subisce rispetto alla media del campionato, pesando di piu'
    le partite recenti e tirando le stime verso 1 quando la storia e'
    corta. E' la stessa logica del motore, applicata a un solo tempo.
    """
    fuori = {}
    for storia in storie(partite).values():
        for i, (p, mio, suo) in enumerate(storia):
            prec = [x for x in storia[:i] if x[0]["data"] < p["data"]][::-1]
            if len(prec) < MIN_PARTITE:
                continue
            na = da = nd = dd = 0.0
            for k, q, qmio, qsuo, peso in _pesi(prec, p["stagione"]):
                mq = medie.get(q["fid"])
                if not mq:
                    continue
                rif_mio = mq[0] if qmio == "casa" else mq[1]
                rif_suo = mq[1] if qmio == "casa" else mq[0]
                if rif_mio <= 0 or rif_suo <= 0:
                    continue
                na += peso * (q[qmio][tempo] / rif_mio); da += peso
                nd += peso * (q[qsuo][tempo] / rif_suo); dd += peso
            if da <= 0 or dd <= 0:
                continue
            n = len(prec)
            att = 1.0 + (na / da - 1.0) * n / (n + SHRINK)
            dif = 1.0 + (nd / dd - 1.0) * n / (n + SHRINK)
            fuori[(p["fid"], mio)] = (max(att, 0.05), max(dif, 0.05), n)
    return fuori


def quote_tempo(partite):
    """
    Per ogni squadra, la quota dei suoi gol che cade nel primo tempo,
    tirata verso la quota del campionato. E' il numero che serve al
    metodo C. Con pochi gol a disposizione e' molto rumorosa, percio'
    lo shrink e' forte.
    """
    quota_lega = {}
    per_lega = defaultdict(list)
    for p in partite:
        per_lega[p["lega"]].append(p)
    for lista in per_lega.values():
        primo = tot = conta = 0.0
        buffer, data_prec = [], None
        for p in lista:
            if data_prec is not None and p["data"] != data_prec:
                for b in buffer:
                    primo += b["casa"][0] + b["ospite"][0]
                    tot += b["casa_ft"] + b["ospite_ft"]
                    conta += 1
                buffer = []
            quota_lega[p["fid"]] = (primo / tot if conta >= MIN_LEGA and tot > 0
                                    else None)
            buffer.append(p)
            data_prec = p["data"]

    per_squadra = {}
    for storia in storie(partite).values():
        for i, (p, mio, suo) in enumerate(storia):
            ql = quota_lega.get(p["fid"])
            if ql is None:
                continue
            prec = [x for x in storia[:i] if x[0]["data"] < p["data"]][::-1]
            if len(prec) < MIN_PARTITE:
                continue
            primo = tot = 0.0
            for k, q, qmio, qsuo, peso in _pesi(prec, p["stagione"]):
                # contano i gol della partita, non solo i propri: la
                # quota di tempo e' una caratteristica della gara
                primo += peso * (q[qmio][0] + q[qsuo][0])
                tot += peso * (q[qmio][0] + q[qmio][1] +
                               q[qsuo][0] + q[qsuo][1])
            if tot <= 0:
                per_squadra[(p["fid"], mio)] = ql
                continue
            grezza = primo / tot
            n = len(prec)
            per_squadra[(p["fid"], mio)] = ql + (grezza - ql) * n / (n + SHRINK_QUOTA)
    return quota_lega, per_squadra


# ---------------------------------------------------------------
#  probabilita'
# ---------------------------------------------------------------

def poisson(k, lam):
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * lam ** k / math.factorial(k)


def matrice(lc, lo, n=7):
    """Griglia dei punteggi di un tempo, con i due lati indipendenti."""
    M = [[poisson(x, lc) * poisson(y, lo) for y in range(n)] for x in range(n)]
    tot = sum(sum(r) for r in M)
    return [[v / tot for v in r] for r in M]


def over(M, soglia):
    n = len(M)
    return sum(M[x][y] for x in range(n) for y in range(n) if x + y > soglia)


def esiti(M):
    n = len(M)
    p1 = sum(M[x][y] for x in range(n) for y in range(n) if x > y)
    px = sum(M[x][x] for x in range(n))
    return p1, px, 1 - p1 - px


def perdita(p, avvenuto):
    return -math.log(max(p if avvenuto else 1 - p, 1e-15))


def media(v):
    return sum(v) / len(v) if v else float("nan")


def confronto(a, b):
    """
    Quanto il metodo A e' migliore del metodo B, con la fascia di
    incertezza. Se la fascia attraversa lo zero non sappiamo dire
    chi e' meglio.
    """
    d = [x - y for x, y in zip(a, b)]
    if len(d) < 30:
        return None
    m = media(d)
    var = sum((x - m) ** 2 for x in d) / (len(d) - 1)
    err = 1.96 * math.sqrt(var / len(d))
    return m, m - err, m + err


def riga_confronto(nome, perdite, riferimento):
    c = confronto(perdite[riferimento], perdite[nome])
    if c is None:
        return f"  {nome:<38} dati insufficienti"
    m, lo, hi = c
    esito = ("MEGLIO della media" if lo > 0 else
             "peggio della media" if hi < 0 else "non distinguibile")
    return (f"  {nome:<38} {media(perdite[nome]):.4f}  "
            f"guadagno {m:+.4f} [{lo:+.4f}, {hi:+.4f}]  {esito}")


# ---------------------------------------------------------------

def main():
    conn = sqlite3.connect(DB_PATH)
    partite, senza = carica(conn)

    print("=" * 78)
    print("1. QUANTI PARZIALI ABBIAMO")
    print("=" * 78)
    print(f"  partite con parziale al 45': {len(partite)}")
    print(f"  partite scartate (parziale assente o incoerente): {senza}")
    if len(partite) < 400:
        print("\n  Troppo poche per dire qualcosa. Test interrotto.")
        return

    # ---- i fatti di base -----------------------------------------
    print("\n" + "=" * 78)
    print("2. DOVE CADONO I GOL")
    print("=" * 78)
    g1 = sum(p["casa"][0] + p["ospite"][0] for p in partite)
    g2 = sum(p["casa"][1] + p["ospite"][1] for p in partite)
    print(f"  primo tempo:   {g1:>6} gol  ({g1/(g1+g2):.1%})")
    print(f"  secondo tempo: {g2:>6} gol  ({g2/(g1+g2):.1%})")
    print(f"  media per partita: {(g1+g2)/len(partite):.2f} "
          f"({g1/len(partite):.2f} nel primo, {g2/len(partite):.2f} nel secondo)")

    per_lega = defaultdict(lambda: [0, 0])
    for p in partite:
        per_lega[p["lega"]][0] += p["casa"][0] + p["ospite"][0]
        per_lega[p["lega"]][1] += p["casa"][1] + p["ospite"][1]
    quote = [(a / (a + b), lega) for lega, (a, b) in per_lega.items() if a + b >= 100]
    quote.sort()
    if quote:
        print(f"\n  La quota di primo tempo cambia poco da campionato a campionato:")
        print(f"    minima {quote[0][0]:.1%} (lega {quote[0][1]}), "
              f"massima {quote[-1][0]:.1%} (lega {quote[-1][1]})")
        print("  Se la forbice e' stretta, la quota della singola squadra "
              "ha poco da aggiungere.")

    # ---- i due tempi sono indipendenti? --------------------------
    print("\n" + "=" * 78)
    print("3. I DUE TEMPI SI INFLUENZANO?")
    print("=" * 78)
    a = [p["casa"][0] + p["ospite"][0] for p in partite]
    b = [p["casa"][1] + p["ospite"][1] for p in partite]
    ma, mb = media(a), media(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / len(a)
    sa = math.sqrt(sum((x - ma) ** 2 for x in a) / len(a))
    sb = math.sqrt(sum((y - mb) ** 2 for y in b) / len(b))
    r = cov / (sa * sb) if sa and sb else 0.0
    print(f"  correlazione fra gol del primo e del secondo tempo: {r:+.3f}")
    print("  Vicina a zero: si possono trattare come indipendenti, e allora")
    print("  Primo tempo/Finale e 'segna in entrambi i tempi' si calcolano")
    print("  moltiplicando. Lontana da zero: servirebbe una correzione.")

    # ---- i quattro metodi ----------------------------------------
    medie1 = medie_espansive(partite, 0)
    medie2 = medie_espansive(partite, 1)
    forze1 = forze(partite, medie1, 0)
    forze2 = forze(partite, medie2, 1)
    medie_ft = {p["fid"]: (
        (medie1[p["fid"]][0] + medie2[p["fid"]][0],
         medie1[p["fid"]][1] + medie2[p["fid"]][1])
        if medie1.get(p["fid"]) and medie2.get(p["fid"]) else None)
        for p in partite}
    forze_ft = forze([dict(p, casa=[p["casa_ft"], 0], ospite=[p["ospite_ft"], 0])
                      for p in partite], medie_ft, 0)
    quota_lega, quota_squadra = quote_tempo(partite)

    perdite = defaultdict(lambda: defaultdict(list))
    previste = defaultdict(lambda: defaultdict(list))
    reali = defaultdict(list)
    usate = 0
    for p in partite:
        chiavi = [(p["fid"], "casa"), (p["fid"], "ospite")]
        if not all(k in forze1 and k in forze2 and k in forze_ft for k in chiavi):
            continue
        m1, m2, mft = medie1.get(p["fid"]), medie2.get(p["fid"]), medie_ft.get(p["fid"])
        ql = quota_lega.get(p["fid"])
        if not m1 or not m2 or not mft or ql is None:
            continue
        usate += 1

        ac, dc, _ = forze1[chiavi[0]]
        ao, do, _ = forze1[chiavi[1]]
        ac2, dc2, _ = forze2[chiavi[0]]
        ao2, do2, _ = forze2[chiavi[1]]
        acf, dcf, _ = forze_ft[chiavi[0]]
        aof, dof, _ = forze_ft[chiavi[1]]
        qs = quota_squadra.get(chiavi[0], ql)

        lam_ft = (acf * dof * mft[0], aof * dcf * mft[1])
        modelli = {
            "A  media del campionato": ((m1[0], m1[1]), (m2[0], m2[1])),
            "B  gol attesi x quota del campionato":
                ((lam_ft[0] * ql, lam_ft[1] * ql),
                 (lam_ft[0] * (1 - ql), lam_ft[1] * (1 - ql))),
            "C  gol attesi x quota della squadra":
                ((lam_ft[0] * qs, lam_ft[1] * qs),
                 (lam_ft[0] * (1 - qs), lam_ft[1] * (1 - qs))),
            "D  modello dedicato al tempo":
                ((ac * do * m1[0], ao * dc * m1[1]),
                 (ac2 * do2 * m2[0], ao2 * dc2 * m2[1])),
        }

        gc1, go1 = p["casa"][0], p["ospite"][0]
        gc2, go2 = p["casa"][1], p["ospite"][1]
        vero = {
            "Over 0.5 primo tempo": gc1 + go1 >= 1,
            "Over 1.5 primo tempo": gc1 + go1 >= 2,
            "Gol/NoGol primo tempo": gc1 > 0 and go1 > 0,
            "Over 0.5 secondo tempo": gc2 + go2 >= 1,
            "Over 1.5 secondo tempo": gc2 + go2 >= 2,
            "segna in entrambi i tempi (casa)": p["casa"][0] > 0 and p["casa"][1] > 0,
        }
        for k, v in vero.items():
            reali[k].append(v)
        esito1 = "1" if gc1 > go1 else ("X" if gc1 == go1 else "2")
        reali["1X2 primo tempo"].append(esito1)

        for nome, ((l1c, l1o), (l2c, l2o)) in modelli.items():
            M1, M2 = matrice(l1c, l1o), matrice(l2c, l2o)
            p1, px, p2 = esiti(M1)
            gg1 = sum(M1[x][y] for x in range(1, len(M1)) for y in range(1, len(M1)))
            # segna in entrambi i tempi: qui i due tempi si moltiplicano,
            # ed e' lecito solo se la sezione 3 dice che non si influenzano
            sc1 = 1 - sum(M1[0][y] for y in range(len(M1)))
            sc2 = 1 - sum(M2[0][y] for y in range(len(M2)))

            binari = {
                "Over 0.5 primo tempo": (over(M1, 0.5), gc1 + go1 >= 1),
                "Over 1.5 primo tempo": (over(M1, 1.5), gc1 + go1 >= 2),
                "Gol/NoGol primo tempo": (gg1, gc1 > 0 and go1 > 0),
                "Over 0.5 secondo tempo": (over(M2, 0.5), gc2 + go2 >= 1),
                "Over 1.5 secondo tempo": (over(M2, 1.5), gc2 + go2 >= 2),
                "segna in entrambi i tempi (casa)":
                    (sc1 * sc2, p["casa"][0] > 0 and p["casa"][1] > 0),
            }
            for mercato, (prob, avvenuto) in binari.items():
                perdite[mercato][nome].append(perdita(prob, avvenuto))
                previste[mercato][nome].append(prob)
            perdite["1X2 primo tempo"][nome].append(
                -math.log(max({"1": p1, "X": px, "2": p2}[esito1], 1e-15)))

    print("\n" + "=" * 78)
    print("4. I QUATTRO METODI A CONFRONTO")
    print("=" * 78)
    print(f"  partite utilizzabili: {usate}")
    print("  Numeri piu' bassi sono meglio. Il guadagno e' rispetto al metodo A,")
    print("  la media del campionato: se nessuno lo batte, sui tempi non")
    print("  abbiamo niente da dire.\n")

    nomi = ["A  media del campionato",
            "B  gol attesi x quota del campionato",
            "C  gol attesi x quota della squadra",
            "D  modello dedicato al tempo"]
    for mercato in ["Over 0.5 primo tempo", "Over 1.5 primo tempo",
                    "Gol/NoGol primo tempo", "1X2 primo tempo",
                    "Over 0.5 secondo tempo", "Over 1.5 secondo tempo",
                    "segna in entrambi i tempi (casa)"]:
        if not perdite[mercato][nomi[0]]:
            continue
        print(f"  {mercato.upper()}")
        print(f"  {'metodo':<38} {'perdita':>8}")
        print(f"  {nomi[0]:<38} {media(perdite[mercato][nomi[0]]):.4f}  "
              "(riferimento)")
        for n in nomi[1:]:
            print(riga_confronto(n, perdite[mercato], nomi[0]))
        print()

    # ---- taratura ------------------------------------------------
    print("=" * 78)
    print("5. LE PERCENTUALI SONO ONESTE?")
    print("=" * 78)
    print("  Quanto diciamo che succede, contro quanto succede davvero.")
    print("  Un metodo puo' perdere il confronto e restare onesto: in quel")
    print("  caso le percentuali si possono mostrare, ma non danno vantaggio.")
    print("  Se invece e' storto, il mercato non va messo affatto.\n")
    print(f"  {'mercato':<34} {'realta':>7}  " +
          "  ".join(f"{n.split()[0]:>7}" for n in nomi))
    for mercato in ["Over 0.5 primo tempo", "Over 1.5 primo tempo",
                    "Gol/NoGol primo tempo", "Over 0.5 secondo tempo",
                    "Over 1.5 secondo tempo", "segna in entrambi i tempi (casa)"]:
        if not previste[mercato][nomi[0]]:
            continue
        veri = reali[mercato]
        reale = sum(veri) / len(veri)
        colonne = []
        for n in nomi:
            m = media(previste[mercato][n])
            # tolleranza: l'errore che ci si aspetta per puro caso
            tol = 2 * math.sqrt(max(m * (1 - m), 1e-9) / len(veri))
            segno = " " if abs(m - reale) <= tol else "*"
            colonne.append(f"{m:>6.1%}{segno}")
        print(f"  {mercato:<34} {reale:>6.1%}   " + "   ".join(colonne))
    print("\n  L'asterisco segnala uno scarto troppo grande per essere caso.")

    print("\n" + "=" * 78)
    print("COME LEGGERE")
    print("  - Se B, C o D battono A in modo dimostrato (fascia tutta sopra")
    print("    lo zero) su Over 0.5 e Over 1.5 di primo tempo, i mercati sui")
    print("    tempi si possono costruire e il metodo vincente ci dice come.")
    print("  - Se vince B, basta moltiplicare i gol attesi che gia' calcoliamo")
    print("    per la quota del campionato: lavoro di mezz'ora, nessun")
    print("    modello nuovo da mantenere.")
    print("  - Se nessuno batte A, sui tempi siamo alla pari con chi guarda")
    print("    solo la media, e i mercati non vanno messi. Stesso esito dei")
    print("    corner.")
    print("=" * 78)


if __name__ == "__main__":
    main()
