#!/usr/bin/env python3
"""
QUANDO CI SBILANCIAMO SULLA SFAVORITA, ABBIAMO RAGIONE?

L'osservazione da verificare: nelle partite dove il nostro modello si
allontana molto dal mercato a favore della squadra sfavorita, quella
squadra spesso pareggia o vince.

Puo' essere vero per due motivi seri. Il nostro modello pesa gli xG al
75%, e gli xG dicono quanto una squadra ha MERITATO, non quanto ha
ottenuto: una squadra che perde giocando bene resta forte per noi.
E i bookmaker prezzano anche i soldi del pubblico, che vanno sulle
favorite. Se la nostra divergenza cade proprio li', potremmo avere
ragione su quel sottoinsieme anche perdendo nel complesso.

Puo' anche essere un'illusione: una sfavorita pareggia o vince quattro
volte su dieci comunque, e si ricordano i casi che confermano.

Questo test non decide a occhio. Per ogni fascia di divergenza
confronta tre cose: quanto dicevamo noi, quanto diceva il mercato, e
quanto e' successo davvero. Poi simula la giocata: quanto avresti
guadagnato puntando sulla sfavorita ogni volta.

LE QUOTE VERE si ricostruiscono: archiviamo la probabilita' con il
margine gia' tolto, piu' il margine stesso, quindi la quota che il
bookmaker offriva si ricava esattamente.

Uso, dalla cartella del progetto:
    python3 scripts/test_divergenza.py
"""

import math
import os
import random
import sqlite3
import sys

DB_PATH = "calcio_dati.db"
FASCE = [(-1.00, 0.00), (0.00, 0.05), (0.05, 0.10),
         (0.10, 0.15), (0.15, 1.00)]
PUNTATA = 10.0            # euro per giocata, nella simulazione


def carica(conn):
    """Partite archiviate, con le quote e il risultato finale."""
    try:
        righe = conn.execute("""
            SELECT a.fixture_id, a.campionato, a.casa, a.fuori,
                   a.p1, a.px, a.p2, a.q1, a.qx, a.q2, a.margine,
                   a.affidabilita, f.goals_home, f.goals_away
            FROM archivio_previsioni a
            JOIN fixtures f ON f.id = a.fixture_id
            WHERE a.q1 IS NOT NULL
              AND f.goals_home IS NOT NULL
              AND f.status IN ('FT','AET','PEN')
        """).fetchall()
    except sqlite3.OperationalError as e:
        print(f"  Non riesco a leggere l'archivio: {e}")
        return []

    partite = []
    for (fid, lega, casa, fuori, p1, px, p2, q1, qx, q2, margine,
         affid, gc, ga) in righe:
        if None in (p1, px, p2, q1, qx, q2):
            continue
        esito = "1" if gc > ga else ("X" if gc == ga else "2")
        # per il mercato, chi e' la sfavorita fra le due squadre
        sfavorita = "1" if q1 < q2 else "2"
        nostra = {"1": p1, "X": px, "2": p2}[sfavorita]
        loro = {"1": q1, "X": qx, "2": q2}[sfavorita]
        m = margine if margine is not None else 0.0
        # la quota offerta: la probabilita' col margine rimesso dentro
        quota = 1.0 / max(loro * (1.0 + m), 1e-9)
        partite.append({
            "fid": fid, "lega": lega, "casa": casa, "fuori": fuori,
            "p": {"1": p1, "X": px, "2": p2},
            "q": {"1": q1, "X": qx, "2": q2},
            "esito": esito, "sfavorita": sfavorita,
            "nostra": nostra, "loro": loro, "scarto": nostra - loro,
            "quota": quota, "affidabilita": affid or 0,
            "vinta": esito == sfavorita,
            "non_persa": esito in (sfavorita, "X"),
        })
    return partite


def perdita(p, avvenuto):
    return -math.log(max(p if avvenuto else 1 - p, 1e-15))


def perdita_1x2(d, chiave):
    return -math.log(max(d[chiave][d["esito"]], 1e-15))


def media(v):
    return sum(v) / len(v) if v else float("nan")


def intervallo(valori, giri=2000):
    """Fascia di incertezza col bootstrap: si ricampiona e si guarda."""
    if len(valori) < 10:
        return None
    random.seed(1)
    medie = []
    n = len(valori)
    for _ in range(giri):
        medie.append(sum(random.choice(valori) for _ in range(n)) / n)
    medie.sort()
    return medie[int(giri * 0.025)], medie[int(giri * 0.975)]


def main():
    if not os.path.exists(DB_PATH):
        print(f"  {DB_PATH} non trovato: lancia dalla cartella del progetto.")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    partite = carica(conn)

    print("=" * 76)
    print("1. QUANTE PARTITE ABBIAMO")
    print("=" * 76)
    print(f"  partite archiviate, con quote e gia' giocate: {len(partite)}")
    if len(partite) < 60:
        print("\n  Troppo poche per dire qualcosa. Si rifa' fra qualche")
        print("  settimana: da oggi l'archivio cresce molto piu' in fretta.")
        if not partite:
            return

    sbil = [d for d in partite if d["scarto"] > 0]
    print(f"  di cui ci sbilanciamo verso la sfavorita: {len(sbil)}")
    forti = [d for d in partite if d["scarto"] >= 0.10]
    print(f"  di cui con scarto di 10 punti o piu':     {len(forti)}")
    print("\n  E' il numero da confrontare con i casi che hai notato: se i")
    print("  tuoi venti-trenta stanno dentro un totale simile, sono tanti;")
    print("  se il totale e' molto piu' grande, sono la normalita'.")

    # ---- 2. previsto contro avvenuto -----------------------------
    print("\n" + "=" * 76)
    print("2. PER OGNI FASCIA: CHI AVEVA RAGIONE")
    print("=" * 76)
    print("  Lo scarto e' quanto diamo in piu' alla sfavorita rispetto al")
    print("  mercato. Sotto, quanto dicevamo noi, quanto diceva il mercato,")
    print("  e quante volte la sfavorita ha davvero vinto.\n")
    print(f"  {'scarto':<14} {'n':>5} {'noi':>8} {'mercato':>9} {'reale':>8} "
          f"{'chi sbaglia meno':>18}")
    for lo, hi in FASCE:
        gruppo = [d for d in partite if lo <= d["scarto"] < hi]
        if not gruppo:
            continue
        nostra = media([d["nostra"] for d in gruppo])
        loro = media([d["loro"] for d in gruppo])
        reale = media([1.0 if d["vinta"] else 0.0 for d in gruppo])
        vicino = "noi" if abs(nostra - reale) < abs(loro - reale) else "il mercato"
        etichetta = (f"{lo:+.0%}..{hi:+.0%}" if hi < 1 else f"oltre {lo:+.0%}")
        print(f"  {etichetta:<14} {len(gruppo):>5} {nostra:>8.1%} {loro:>9.1%} "
              f"{reale:>8.1%} {vicino:>18}")

    print("\n  Lo stesso, contando anche i pareggi come esito buono:\n")
    print(f"  {'scarto':<14} {'n':>5} {'noi':>8} {'mercato':>9} {'reale':>8} "
          f"{'chi sbaglia meno':>18}")
    for lo, hi in FASCE:
        gruppo = [d for d in partite if lo <= d["scarto"] < hi]
        if not gruppo:
            continue
        nostra = media([d["p"][d["sfavorita"]] + d["p"]["X"] for d in gruppo])
        loro = media([d["q"][d["sfavorita"]] + d["q"]["X"] for d in gruppo])
        reale = media([1.0 if d["non_persa"] else 0.0 for d in gruppo])
        vicino = "noi" if abs(nostra - reale) < abs(loro - reale) else "il mercato"
        etichetta = (f"{lo:+.0%}..{hi:+.0%}" if hi < 1 else f"oltre {lo:+.0%}")
        print(f"  {etichetta:<14} {len(gruppo):>5} {nostra:>8.1%} {loro:>9.1%} "
              f"{reale:>8.1%} {vicino:>18}")

    # ---- 3. log loss per fascia ----------------------------------
    print("\n" + "=" * 76)
    print("3. QUANTO SBAGLIAMO, IN NUMERI")
    print("=" * 76)
    print("  Log loss sull'esito 1X2. Piu' basso e' meglio. Il guadagno")
    print("  positivo vuol dire che in quella fascia siamo migliori noi.\n")
    print(f"  {'scarto':<14} {'n':>5} {'noi':>9} {'mercato':>9} "
          f"{'guadagno':>10}  fascia")
    for lo, hi in FASCE:
        gruppo = [d for d in partite if lo <= d["scarto"] < hi]
        if len(gruppo) < 10:
            continue
        nostre = [perdita_1x2(d, "p") for d in gruppo]
        loro = [perdita_1x2(d, "q") for d in gruppo]
        diff = [b - a for a, b in zip(nostre, loro)]
        fascia = intervallo(diff)
        etichetta = (f"{lo:+.0%}..{hi:+.0%}" if hi < 1 else f"oltre {lo:+.0%}")
        testo = (f"[{fascia[0]:+.4f}, {fascia[1]:+.4f}]" if fascia else "–")
        print(f"  {etichetta:<14} {len(gruppo):>5} {media(nostre):>9.4f} "
              f"{media(loro):>9.4f} {media(diff):>+10.4f}  {testo}")

    # ---- 4. la simulazione della giocata -------------------------
    print("\n" + "=" * 76)
    print("4. SE AVESSI GIOCATO LA SFAVORITA")
    print("=" * 76)
    print(f"  {PUNTATA:.0f} euro per partita, alla quota che il bookmaker")
    print("  offriva davvero (margine compreso). E' la prova che conta:")
    print("  avere ragione sulle percentuali non basta, servono le quote.\n")
    print(f"  {'scarto':<14} {'n':>5} {'vinte':>7} {'quota media':>12} "
          f"{'ritorno':>9}  fascia")
    for lo, hi in FASCE:
        gruppo = [d for d in partite if lo <= d["scarto"] < hi]
        if len(gruppo) < 10:
            continue
        ritorni = [(d["quota"] - 1.0) if d["vinta"] else -1.0 for d in gruppo]
        fascia = intervallo(ritorni)
        vinte = sum(1 for d in gruppo if d["vinta"])
        etichetta = (f"{lo:+.0%}..{hi:+.0%}" if hi < 1 else f"oltre {lo:+.0%}")
        testo = (f"[{fascia[0]:+.1%}, {fascia[1]:+.1%}]" if fascia else "–")
        print(f"  {etichetta:<14} {len(gruppo):>5} {vinte:>7} "
              f"{media([d['quota'] for d in gruppo]):>12.2f} "
              f"{media(ritorni):>+9.1%}  {testo}")

    print("\n  Il ritorno e' per euro puntato: +10% vuol dire che ogni 10")
    print(f"  euro ne tornavano 11. La fascia e' l'incertezza: se contiene")
    print("  lo zero, il risultato puo' essere solo fortuna.")

    # ---- 5. il verdetto ------------------------------------------
    print("\n" + "=" * 76)
    print("5. VERDETTO")
    print("=" * 76)
    if len(forti) >= 10:
        ritorni = [(d["quota"] - 1.0) if d["vinta"] else -1.0 for d in forti]
        fascia = intervallo(ritorni)
        m = media(ritorni)
        print(f"  Sulle {len(forti)} partite con scarto di 10 punti o piu':")
        print(f"  ritorno {m:+.1%}" +
              (f", fascia [{fascia[0]:+.1%}, {fascia[1]:+.1%}]" if fascia else ""))
        if fascia and fascia[0] > 0:
            print("\n  GUADAGNO DIMOSTRATO. E' la prima cosa che regge in questo")
            print("  sistema. Prima di giocarci sul serio va comunque rifatto")
            print("  fra un mese con piu' partite: un risultato solo non basta.")
        elif fascia and fascia[1] < 0:
            print("\n  PERDITA DIMOSTRATA. I casi che hai notato erano quelli")
            print("  che si ricordano: gli altri no. Niente da costruire qui.")
        else:
            print("\n  Non distinguibile dal caso: la fascia attraversa lo zero.")
            print("  Non e' un no, e' un 'non lo sappiamo ancora'. Con l'archivio")
            print("  che da oggi cresce piu' in fretta, fra un mese si rifa'.")
            if fascia and m > 0:
                # l'incertezza si stringe come la radice del numero di
                # partite: per portare il bordo basso sopra lo zero ne
                # servono (larghezza/margine) al quadrato volte tante
                mezza = (fascia[1] - fascia[0]) / 2
                if mezza > m:
                    serve = int(len(forti) * (mezza / m) ** 2)
                    print(f"\n  Il ritorno e' positivo ma l'incertezza e' {mezza:.0%}.")
                    if serve > 20000:
                        print("  Il vantaggio e' cosi' sottile che per dimostrarlo")
                        print("  servirebbero decine di migliaia di partite: per")
                        print("  noi vuol dire mai. Se il ritorno resta su questi")
                        print("  valori, non e' una strada percorribile, anche")
                        print("  fosse reale.")
                    else:
                        print(f"  Perche' diventi dimostrato, mantenendo questo")
                        print(f"  andamento, servirebbero circa {serve} partite")
                        print(f"  con scarto forte, contro le {len(forti)} di adesso.")
    else:
        print("  Troppo poche partite con scarto forte per dire qualcosa.")
    print("=" * 76)


if __name__ == "__main__":
    main()
