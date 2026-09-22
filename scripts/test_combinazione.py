"""
NOI, IL MERCATO, E LE GIOCATE DI VALORE
=======================================
La verifica dal vivo dice che il mercato e' piu' preciso di noi, in modo
dimostrato. Questo script risponde a tre domande su quelle stesse partite.

1. COMBINARE NOI E IL MERCATO
   La probabilita' combinata e' una media pesata, fatta sui logaritmi:
       p  proporzionale a  p_noi^w * p_mercato^(1-w)
   Con w = 0 e' il mercato, con w = 1 siamo noi. La combinazione batte il
   mercato solo se le nostre previsioni contengono qualcosa che il mercato
   non ha.

   Il peso si sceglie in modo onesto (walk-forward): le partite sono in
   ordine di data e divise in blocchi; il peso per ogni blocco si sceglie
   SOLO sui blocchi precedenti, e si misura su quello. Il primo blocco
   serve solo a partire e non viene valutato.

2. DOVE SIAMO PIU' VICINI AL MERCATO
   Per livello di affidabilita' e per tipo di formazioni. Il confronto si
   fa con il mercato sulle stesse partite, perche' il log loss da solo
   dipende anche da quanto erano difficili.
   Se l'indice di affidabilita' funziona, dove e' "alta" dovremmo stare
   piu' vicini al mercato che dove e' "bassa".

3. LE GIOCATE DI VALORE AVREBBERO GUADAGNATO?
   Si simula di aver puntato 1 su ogni esito con vantaggio stimato fra il
   5% e il 50%, probabilita' almeno 20% e affidabilita' almeno 55: gli
   stessi filtri delle Singole consigliate.
   La quota si ricostruisce dalla probabilita' del mercato rimettendo il
   margine medio dei bookmaker: e' un'approssimazione della quota media,
   non della migliore disponibile.

USO:
    python scripts/test_combinazione.py
"""

import math
import random
import sqlite3
import sys

DB_PATH = "calcio_dati.db"
BLOCCHI = 5
GRIGLIA = [i / 20 for i in range(21)]
RICAMPIONAMENTI = 2000
random.seed(42)


def carica():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT a.p1, a.px, a.p2, a.q1, a.qx, a.q2, a.margine,
                   a.affidabilita, a.affidabilita_etichetta, a.formazioni,
                   a.data, f.goals_home, f.goals_away
            FROM archivio_previsioni a
            JOIN fixtures f ON f.id = a.fixture_id
            WHERE f.goals_home IS NOT NULL AND f.goals_away IS NOT NULL
              AND f.status IN ('FT','AET','PEN')
              AND a.q1 IS NOT NULL
            ORDER BY a.data
        """)
    except sqlite3.OperationalError as e:
        print(f"Archivio non disponibile: {e}")
        sys.exit(1)
    righe = []
    for p1, px, p2, q1, qx, q2, marg, aff, etich, form, data, gc, ga in cur.fetchall():
        esito = 0 if gc > ga else (1 if gc == ga else 2)
        righe.append({"noi": (p1, px, p2), "mercato": (q1, qx, q2),
                      "margine": marg or 0.05, "aff": aff or 0,
                      "etichetta": etich or "?", "formazioni": form or "?",
                      "esito": esito})
    conn.close()
    return righe


def combina(r, w):
    v = [max(a, 1e-9) ** w * max(b, 1e-9) ** (1 - w)
         for a, b in zip(r["noi"], r["mercato"])]
    s = sum(v)
    return [x / s for x in v]


def perdita(p, esito):
    return -math.log(max(p[esito], 1e-15))


def media(v):
    return sum(v) / len(v) if v else float("nan")


def peso_migliore(righe):
    return min(GRIGLIA, key=lambda w: media([perdita(combina(r, w), r["esito"])
                                              for r in righe]))


def intervallo_differenza(a, b):
    """Media di (a - b) con intervallo al 95%, ricampionando le partite."""
    d = [x - y for x, y in zip(a, b)]
    n = len(d)
    medie = sorted(sum(d[random.randrange(n)] for _ in range(n)) / n
                   for _ in range(RICAMPIONAMENTI))
    return media(d), medie[int(0.025 * RICAMPIONAMENTI)], medie[int(0.975 * RICAMPIONAMENTI)]


def verdetto(lo, hi, meglio, peggio):
    if lo > 0:
        return f"{meglio}, in modo dimostrato"
    if hi < 0:
        return f"{peggio}, in modo dimostrato"
    return "non distinguibili: servono piu' partite"


def parte1(righe):
    print("=" * 70)
    print("1. COMBINARE NOI E IL MERCATO")
    print("=" * 70)
    n = len(righe)
    dim = n // BLOCCHI
    if dim < 30:
        print(f"Solo {n} partite con quote: troppo poche per dividerle in blocchi.")
        return

    noi, mercato, combinata, pesi = [], [], [], []
    for b in range(1, BLOCCHI):
        passato = righe[:b * dim]
        blocco = righe[b * dim:(b + 1) * dim if b < BLOCCHI - 1 else n]
        w = peso_migliore(passato)
        pesi.append(w)
        for r in blocco:
            noi.append(perdita(r["noi"], r["esito"]))
            mercato.append(perdita(r["mercato"], r["esito"]))
            combinata.append(perdita(combina(r, w), r["esito"]))

    print(f"Partite con quote: {n}, valutate fuori campione: {len(noi)}")
    print(f"(le prime {dim} servono solo a scegliere il primo peso)\n")
    print(f"  {'':<22}{'log loss':>10}")
    print(f"  {'solo noi':<22}{media(noi):>10.4f}")
    print(f"  {'solo mercato':<22}{media(mercato):>10.4f}")
    print(f"  {'combinazione':<22}{media(combinata):>10.4f}")
    print(f"\n  peso dato a noi, blocco per blocco: "
          + ", ".join(f"{w:.2f}" for w in pesi))

    m, lo, hi = intervallo_differenza(mercato, combinata)
    print(f"\n  Combinazione contro mercato: {m:+.4f}  [{lo:+.4f}, {hi:+.4f}]")
    print(f"  (positivo = la combinazione e' piu' precisa)")
    print(f"  ESITO: {verdetto(lo, hi, 'la combinazione batte il mercato', 'il mercato batte la combinazione')}")

    w_tutto = peso_migliore(righe)
    print(f"\n  Peso migliore guardando tutte le partite: {w_tutto:.2f}")
    print("  (indicativo: e' scelto sulle stesse partite su cui si misura)")
    curva = [(w, media([perdita(combina(r, w), r["esito"]) for r in righe]))
             for w in (0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0)]
    print("  Log loss al variare del peso:  "
          + "  ".join(f"{w:.1f}:{v:.4f}" for w, v in curva))


def parte2(righe):
    print("\n" + "=" * 70)
    print("2. DOVE SIAMO PIU' VICINI AL MERCATO")
    print("=" * 70)
    print("Scarto = log loss del mercato meno il nostro.")
    print("Negativo = il mercato e' piu' preciso; piu' e' vicino a zero, meglio e'.\n")

    def tabella(titolo, chiave, ordine):
        print(f"{titolo:<18}{'partite':>9}{'noi':>9}{'mercato':>10}{'scarto':>10}   intervallo")
        for valore in ordine:
            g = [r for r in righe if r[chiave] == valore]
            if len(g) < 20:
                continue
            a = [perdita(r["noi"], r["esito"]) for r in g]
            b = [perdita(r["mercato"], r["esito"]) for r in g]
            m, lo, hi = intervallo_differenza(b, a)
            print(f"  {valore:<16}{len(g):>9}{media(a):>9.4f}{media(b):>10.4f}"
                  f"{m:>+10.4f}   [{lo:+.4f}, {hi:+.4f}]")
        print()

    tabella("Affidabilita'", "etichetta", ["alta", "media", "bassa"])
    tabella("Formazioni", "formazioni", ["ufficiale", "probabile", "nessuna"])
    print("Se l'indice di affidabilita' funziona, lo scarto dovrebbe avvicinarsi")
    print("a zero passando da 'bassa' ad 'alta'.")


def parte3(righe):
    print("\n" + "=" * 70)
    print("3. LE GIOCATE DI VALORE AVREBBERO GUADAGNATO?")
    print("=" * 70)
    giocate = []
    for r in righe:
        if r["aff"] < 55:
            continue
        for i in range(3):
            p, q = r["noi"][i], r["mercato"][i]
            if p < 0.20 or q <= 0:
                continue
            vantaggio = p / q - 1
            if not 0.05 <= vantaggio <= 0.50:
                continue
            quota = 1 / (q * (1 + r["margine"]))
            giocate.append({"vinta": r["esito"] == i, "quota": quota,
                            "vantaggio": vantaggio, "p": p})

    if len(giocate) < 20:
        print(f"Solo {len(giocate)} giocate: troppo poche per un giudizio.")
        return

    rese = [(g["quota"] - 1) if g["vinta"] else -1 for g in giocate]
    vinte = sum(1 for g in giocate if g["vinta"])
    attese = sum(g["p"] for g in giocate)
    implicite = sum(1 / g["quota"] for g in giocate)
    n = len(rese)
    medie = sorted(sum(rese[random.randrange(n)] for _ in range(n)) / n
                   for _ in range(RICAMPIONAMENTI))
    lo, hi = medie[int(0.025 * RICAMPIONAMENTI)], medie[int(0.975 * RICAMPIONAMENTI)]

    print(f"Giocate simulate: {n}, puntando 1 su ciascuna")
    print(f"  vinte:                   {vinte} ({vinte / n:.1%})")
    print(f"  secondo noi dovevano:    {attese:.0f} ({attese / n:.1%})")
    print(f"  secondo le quote:        {implicite:.0f} ({implicite / n:.1%})")
    print(f"  resa per giocata:        {media(rese):+.1%}   [{lo:+.1%}, {hi:+.1%}]")
    print(f"  saldo:                   {sum(rese):+.1f} unita' su {n} puntate")
    if lo > 0:
        print("  ESITO: guadagno dimostrato.")
    elif hi < 0:
        print("  ESITO: perdita dimostrata. Il vantaggio stimato era in gran parte errore nostro.")
    else:
        print("  ESITO: non dimostrato ne' in un senso ne' nell'altro.")
    print("\nConfronto utile: se le vinte stanno vicino alla stima delle quote e non")
    print("alla nostra, il vantaggio che vedevamo non c'era.")


def main():
    righe = carica()
    print(f"Partite verificate con quote: {len(righe)}\n")
    if len(righe) < 50:
        print("Troppo poche partite per un'analisi affidabile.")
        return
    parte1(righe)
    parte2(righe)
    parte3(righe)


if __name__ == "__main__":
    main()
