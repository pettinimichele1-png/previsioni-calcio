"""
DOVE PERDIAMO CONTRO IL MERCATO
===============================
Il mercato e' piu' preciso di noi sull'esito 1X2. Questo script guarda
se lo svantaggio e' distribuito ovunque o si concentra da qualche parte:

  A. per gruppo di campionati
       Europa originali   i campionati europei con cui e' nato il sistema
       Sudamerica         i sei aggiunti per primi
       Nuovi              i sedici aggiunti a meta' settembre 2026, con
                          poco storico per squadra
  B. per singolo campionato, dove ci sono abbastanza partite
  C. settimana per settimana, con la composizione dei campionati:
     se il peggioramento coincide con l'arrivo dei nuovi, lo si vede qui

Scarto = log loss del mercato meno il nostro, sulle stesse partite.
Negativo: il mercato e' piu' preciso. Vicino a zero: siamo alla pari.

Il gruppo si riconosce dal paese del campionato.

USO:
    python scripts/test_campionati.py
"""

import math
import random
import sqlite3
import sys
from datetime import datetime

DB_PATH = "calcio_dati.db"
RICAMPIONAMENTI = 2000
MIN_CAMPIONATO = 15
random.seed(7)

NUOVI = {"belgium", "czech republic", "czechia", "croatia", "china",
         "saudi arabia", "serbia", "romania", "scotland", "bulgaria",
         "israel", "finland", "ireland", "japan", "south korea",
         "korea republic", "qatar", "united arab emirates"}
SUDAMERICA = {"brazil", "argentina", "colombia", "chile", "peru", "ecuador",
              "uruguay", "paraguay", "bolivia", "venezuela"}
GRUPPI = ("Europa originali", "Sudamerica", "Nuovi")


def gruppo(campionato):
    paese = str(campionato or "").split(" - ")[0].lower().replace("-", " ").strip()
    if paese in NUOVI:
        return "Nuovi"
    if paese in SUDAMERICA:
        return "Sudamerica"
    return "Europa originali"


def carica():
    conn = sqlite3.connect(DB_PATH)
    try:
        righe = conn.execute("""
            SELECT a.campionato, a.data, a.p1, a.px, a.p2, a.q1, a.qx, a.q2,
                   f.goals_home, f.goals_away
            FROM archivio_previsioni a
            JOIN fixtures f ON f.id = a.fixture_id
            WHERE f.goals_home IS NOT NULL AND f.goals_away IS NOT NULL
              AND f.status IN ('FT','AET','PEN') AND a.q1 IS NOT NULL
            ORDER BY a.data
        """).fetchall()
    except sqlite3.OperationalError as e:
        print(f"Archivio non disponibile: {e}")
        sys.exit(1)
    conn.close()
    fuori = []
    for camp, data, p1, px, p2, q1, qx, q2, gc, ga in righe:
        e = 0 if gc > ga else (1 if gc == ga else 2)
        noi, merc = (p1, px, p2), (q1, qx, q2)
        fuori.append({
            "campionato": camp, "gruppo": gruppo(camp), "data": data,
            "noi": -math.log(max(noi[e], 1e-15)),
            "mercato": -math.log(max(merc[e], 1e-15)),
            "ok_noi": max(range(3), key=lambda i: noi[i]) == e,
            "ok_mercato": max(range(3), key=lambda i: merc[i]) == e})
    return fuori


def media(v):
    return sum(v) / len(v)


def scarto(g):
    """Media di (mercato - noi) con intervallo al 95%."""
    d = [r["mercato"] - r["noi"] for r in g]
    n = len(d)
    medie = sorted(sum(d[random.randrange(n)] for _ in range(n)) / n
                   for _ in range(RICAMPIONAMENTI))
    return media(d), medie[int(.025 * RICAMPIONAMENTI)], medie[int(.975 * RICAMPIONAMENTI)]


def giudizio(lo, hi):
    if lo > 0:
        return "meglio noi"
    if hi < 0:
        return "meglio il mercato"
    return "alla pari"


def parte_a(righe):
    print("=" * 76)
    print("A. PER GRUPPO DI CAMPIONATI")
    print("=" * 76)
    print(f"{'':<18}{'partite':>8}{'azzecc. noi':>13}{'mercato':>9}"
          f"{'scarto':>10}   {'intervallo':<20}giudizio")
    for nome in GRUPPI:
        g = [r for r in righe if r["gruppo"] == nome]
        if len(g) < 10:
            print(f"  {nome:<16}{len(g):>8}   troppo poche")
            continue
        m, lo, hi = scarto(g)
        print(f"  {nome:<16}{len(g):>8}"
              f"{sum(r['ok_noi'] for r in g) / len(g):>13.1%}"
              f"{sum(r['ok_mercato'] for r in g) / len(g):>9.1%}"
              f"{m:>+10.4f}   [{lo:+.4f}, {hi:+.4f}]  {giudizio(lo, hi)}")
    vecchi = [r for r in righe if r["gruppo"] != "Nuovi"]
    if len(vecchi) >= 30:
        m, lo, hi = scarto(vecchi)
        print(f"\n  Senza i campionati nuovi: {len(vecchi)} partite, scarto {m:+.4f} "
              f"[{lo:+.4f}, {hi:+.4f}] -> {giudizio(lo, hi)}")


def parte_b(righe):
    print("\n" + "=" * 76)
    print(f"B. PER CAMPIONATO (almeno {MIN_CAMPIONATO} partite), dal peggiore")
    print("=" * 76)
    per = {}
    for r in righe:
        per.setdefault(r["campionato"], []).append(r)
    tabella = []
    for camp, g in per.items():
        if len(g) >= MIN_CAMPIONATO:
            m, lo, hi = scarto(g)
            tabella.append((m, camp, len(g), lo, hi, g[0]["gruppo"]))
    if not tabella:
        print("  Nessun campionato ha ancora abbastanza partite.")
        return
    for m, camp, n, lo, hi, gr in sorted(tabella):
        print(f"  {camp[:34]:<35}{gr[:10]:<11}{n:>4}  {m:>+8.4f}  [{lo:+.3f}, {hi:+.3f}]")
    sotto = sum(1 for t in tabella if t[0] < 0)
    print(f"\n  Campionati dove il mercato fa meglio: {sotto} su {len(tabella)}")
    print("  (su singoli campionati gli intervalli sono larghi: conta il quadro")
    print("  d'insieme, non la posizione di uno in classifica)")


def parte_c(righe):
    print("\n" + "=" * 76)
    print("C. SETTIMANA PER SETTIMANA")
    print("=" * 76)
    print(f"{'settimana':<14}{'partite':>8}{'scarto':>10}   composizione (Eur / Sud / Nuovi)")
    sett = {}
    for r in righe:
        try:
            d = datetime.fromisoformat(r["data"])
        except ValueError:
            continue
        anno, num, _ = d.isocalendar()
        sett.setdefault((anno, num), []).append(r)
    for (anno, num), g in sorted(sett.items()):
        if len(g) < 10:
            continue
        m = media([r["mercato"] - r["noi"] for r in g])
        comp = [sum(1 for r in g if r["gruppo"] == x) for x in GRUPPI]
        inizio = datetime.fromisocalendar(anno, num, 1)
        print(f"  dal {inizio:%d/%m}    {len(g):>8}{m:>+10.4f}   "
              f"{comp[0]:>3} / {comp[1]:>3} / {comp[2]:>3}")
    print("\n  Se lo scarto peggiora quando cresce la colonna dei Nuovi, il problema")
    print("  sono loro. Se peggiora anche con solo campionati europei, e' altro.")


def main():
    righe = carica()
    print(f"Partite verificate con quote: {len(righe)}\n")
    if len(righe) < 30:
        print("Troppo poche per un'analisi.")
        return
    parte_a(righe)
    parte_b(righe)
    parte_c(righe)


if __name__ == "__main__":
    main()
