"""
SAPPIAMO PREVEDERE I TIRI DI UN GIOCATORE?
==========================================
Stessa disciplina usata per i gol e per i corner, applicata ai singoli.
Legge soltanto, non scrive niente, non tocca il modello dei gol.

LA DOMANDA
----------
Prevedere i tiri di un giocatore e' facile a meta': si sa gia' che un
attaccante tira piu' di un difensore. Il confronto onesto e' quindi:

    sapere CHI e' il giocatore aggiunge qualcosa rispetto a sapere solo
    che RUOLO ha, in quel campionato?

Se non aggiunge niente, il mercato sui tiri e' imprendibile: il
bookmaker il ruolo lo conosce.

I TRE CONFRONTI
---------------
    media di ruolo      tutti gli attaccanti della Liga tirano uguale
    media del giocatore la sua media secca, per quante poche partite abbia
    media smorzata      la sua media tirata verso quella del ruolo, con un
                        peso che cresce con le partite giocate

La terza e' quella che di solito vince: con quattro partite a testa la
media secca e' rumorosa - chi ha tirato 3, 1, 2, 4 ha media 2,5 ma con
un'incertezza enorme - mentre la media di ruolo e' troppo grezza.

COME SI MISURA
--------------
Ogni previsione usa SOLO le partite precedenti di quel giocatore. Si
giudica sull'ultimo terzo delle partite in ordine di data, e solo sui
giocatori rimasti in campo almeno 60 minuti: sono quelli su cui i
bookmaker aprono i mercati.

Oltre all'errore medio si guardano le probabilita' sugli esiti che si
giocano davvero - almeno un tiro, almeno due, almeno tre - perche' e'
li' che il modello si trasforma in una scommessa.

USO:
    python scripts/test_giocatori.py            tiri
    python scripts/test_giocatori.py falli      falli commessi
"""

import os
import sys
import math
import random
import sqlite3
from collections import defaultdict

DB_PATH = "calcio_dati.db"
MIN_MINUTI = 60
MIN_PRECEDENTI = 3
QUOTA_TEST = 0.33
SOGLIE = (0.5, 1.5, 2.5)
SMORZAMENTI = (1.0, 2.0, 3.0, 5.0, 8.0)
RICAMPIONAMENTI = 2000
random.seed(23)


def carica(conn, campo):
    righe = conn.execute(f"""
        SELECT player_id, nome, league_id, date, minuti, ruolo, {campo}
        FROM player_match
        WHERE minuti IS NOT NULL AND {campo} IS NOT NULL
        ORDER BY date
    """).fetchall()
    return [{"pid": r[0], "nome": r[1], "lega": r[2], "data": r[3],
             "minuti": r[4], "ruolo": r[5] or "?", "n": r[6]} for r in righe]


def media(v):
    return sum(v) / len(v) if v else float("nan")


def perdita(lam, avvenuto):
    p = 1 - math.exp(-lam) if avvenuto is None else None
    return p


def poisson_oltre(lam, soglia):
    """P(conteggio > soglia) con Poisson."""
    limite = int(math.floor(soglia))
    somma, termine = 0.0, math.exp(-lam)
    for k in range(limite + 1):
        if k:
            termine *= lam / k
        somma += termine
    return max(1e-9, min(1 - 1e-9, 1 - somma))


def log_perdita(p, avvenuto):
    return -math.log(p if avvenuto else 1 - p)


def confronto(a, b):
    d = [x - y for x, y in zip(a, b)]
    n = len(d)
    if n < 30:
        return None
    medie = sorted(sum(d[random.randrange(n)] for _ in range(n)) / n
                   for _ in range(RICAMPIONAMENTI))
    return media(d), medie[int(.025 * RICAMPIONAMENTI)], medie[int(.975 * RICAMPIONAMENTI)]


def verdetto(c, meglio, peggio):
    if not c:
        return "troppo poche partite"
    _, lo, hi = c
    if lo > 0:
        return f"vince {meglio}, dimostrato"
    if hi < 0:
        return f"vince {peggio}, dimostrato"
    return "non distinguibili"


def prepara(dati):
    """
    Per ogni riga: la media di ruolo e la media del giocatore calcolate
    sulle partite PRECEDENTI, entrambe per 90 minuti.
    """
    storico = defaultdict(list)
    somma_ruolo, conta_ruolo = defaultdict(float), defaultdict(float)
    fuori = []
    for r in dati:
        chiave_ruolo = (r["lega"], r["ruolo"])
        novanta = r["minuti"] / 90.0
        precedenti = storico[r["pid"]]
        if conta_ruolo[chiave_ruolo] >= 20 and len(precedenti) >= MIN_PRECEDENTI:
            mu_ruolo = somma_ruolo[chiave_ruolo] / conta_ruolo[chiave_ruolo]
            eventi = sum(x[0] for x in precedenti)
            minuti = sum(x[1] for x in precedenti)
            fuori.append({**r, "mu_ruolo": mu_ruolo, "eventi": eventi,
                          "novanta_prima": minuti, "partite": len(precedenti),
                          "novanta": novanta})
        storico[r["pid"]].append((r["n"], novanta))
        somma_ruolo[chiave_ruolo] += r["n"]
        conta_ruolo[chiave_ruolo] += novanta
    return fuori


def stime(riga, smorzamento):
    """Tre stime del numero atteso, per i minuti effettivamente giocati."""
    novanta = max(0.3, riga["novanta"])
    ruolo = riga["mu_ruolo"]
    secca = (riga["eventi"] / riga["novanta_prima"]
             if riga["novanta_prima"] > 0 else ruolo)
    smorzata = ((riga["eventi"] + smorzamento * ruolo) /
                (riga["novanta_prima"] + smorzamento))
    return (max(0.02, ruolo * novanta), max(0.02, secca * novanta),
            max(0.02, smorzata * novanta))


def punteggio(righe, indice, smorzamento):
    """Log loss medio sulle soglie che si giocano davvero."""
    perdite = []
    for r in righe:
        lam = stime(r, smorzamento)[indice]
        for soglia in SOGLIE:
            perdite.append(log_perdita(poisson_oltre(lam, soglia), r["n"] > soglia))
    return media(perdite)


def main():
    campo = "falli_fatti" if len(sys.argv) > 1 and sys.argv[1].startswith("fall") else "tiri"
    etichetta = "FALLI COMMESSI" if campo == "falli_fatti" else "TIRI"
    if not os.path.exists(DB_PATH):
        print(f"{DB_PATH} non trovato.")
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        dati = carica(conn, campo)
    except sqlite3.OperationalError as e:
        print(f"Statistiche per giocatore assenti: {e}")
        return
    conn.close()
    if len(dati) < 500:
        print(f"Solo {len(dati)} righe: troppo poche.")
        return

    righe = [r for r in prepara(dati) if r["minuti"] >= MIN_MINUTI]
    if len(righe) < 300:
        print(f"Solo {len(righe)} righe utilizzabili (servono almeno "
              f"{MIN_PRECEDENTI} partite precedenti a testa): aspetta una o due giornate.")
        return
    righe.sort(key=lambda r: r["data"])
    taglio = int(len(righe) * (1 - QUOTA_TEST))
    allenamento, test = righe[:taglio], righe[taglio:]

    print("=" * 76)
    print(f"{etichetta}: SAPERE CHI E' IL GIOCATORE AGGIUNGE QUALCOSA?")
    print("=" * 76)
    print(f"Righe giocatore-partita totali: {len(dati)}")
    print(f"Utilizzabili (60+ minuti e almeno {MIN_PRECEDENTI} partite prima): {len(righe)}")
    print(f"  di cui per scegliere: {len(allenamento)}, per giudicare: {len(test)}")
    print(f"  giocatori diversi nel giudizio: {len(set(r['pid'] for r in test))}")
    print(f"  periodo del giudizio: dal {test[0]['data'][:10]} al {test[-1]['data'][:10]}")
    veri = [r["n"] for r in test]
    mu = media(veri)
    var = media([(x - mu) ** 2 for x in veri])
    print(f"\n  media: {mu:.2f}   varianza: {var:.2f}   rapporto {var / mu:.2f}"
          + ("  (piu' disperso di una Poisson)" if var / mu > 1.25 else "  (vicino a una Poisson)"))

    # ---- il segnale esiste? -----------------------------------------
    print("\n" + "=" * 76)
    print("1. IL SEGNALE ESISTE?")
    print("=" * 76)

    def correlazione(x, y):
        mx, my = media(x), media(y)
        sx = math.sqrt(sum((a - mx) ** 2 for a in x))
        sy = math.sqrt(sum((b - my) ** 2 for b in y))
        return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy) if sx and sy else 0.0

    per_ruolo = [stime(r, 3.0)[0] for r in test]
    per_giocatore = [stime(r, 3.0)[2] for r in test]
    print(f"  correlazione con la realta' della media di RUOLO:      "
          f"{correlazione(per_ruolo, veri):+.3f}")
    print(f"  correlazione con la realta' della media del GIOCATORE: "
          f"{correlazione(per_giocatore, veri):+.3f}")
    print("  Se la seconda non supera chiaramente la prima, conoscere il")
    print("  giocatore non aggiunge niente a conoscere il suo ruolo.")

    # ---- quanto smorzare --------------------------------------------
    scelte = [(punteggio(allenamento, 2, s), s) for s in SMORZAMENTI]
    smorzamento = min(scelte)[1]
    print(f"\n  Smorzamento scelto sulle partite di stima: {smorzamento:.0f}")
    print("  (quante partite 'virtuali' di media-ruolo si aggiungono a")
    print("  quelle vere del giocatore: piu' alto = piu' prudente)")

    # ---- confronto vero ---------------------------------------------
    print("\n" + "=" * 76)
    print("2. IL CONFRONTO (sulle partite di giudizio)")
    print("=" * 76)
    errori = [[], [], []]
    perdite = [[], [], []]
    for r in test:
        lam = stime(r, smorzamento)
        for i in range(3):
            errori[i].append(abs(lam[i] - r["n"]))
            for soglia in SOGLIE:
                perdite[i].append(log_perdita(poisson_oltre(lam[i], soglia), r["n"] > soglia))
    nomi = ("media di ruolo", "media secca del giocatore", "media smorzata")
    print(f"  {'':<28}{'errore medio':>14}{'log loss':>11}")
    for i, nome in enumerate(nomi):
        print(f"  {nome:<28}{media(errori[i]):>14.3f}{media(perdite[i]):>11.4f}")
    c = confronto(perdite[0], perdite[2])
    print(f"\n  smorzata contro ruolo: {c[0]:+.4f} [{c[1]:+.4f}, {c[2]:+.4f}]  "
          f"-> {verdetto(c, 'conoscere il giocatore', 'il solo ruolo')}")
    c2 = confronto(perdite[1], perdite[2])
    print(f"  smorzata contro media secca: {c2[0]:+.4f} [{c2[1]:+.4f}, {c2[2]:+.4f}]  "
          f"-> {verdetto(c2, 'lo smorzamento', 'la media secca')}")

    # ---- per soglia e calibrazione -----------------------------------
    print("\n" + "=" * 76)
    print("3. SULLE SOGLIE CHE SI GIOCANO")
    print("=" * 76)
    print(f"  {'esito':<16}{'succede':>9}{'ruolo':>10}{'smorzata':>11}{'guadagno':>11}   esito")
    for soglia in SOGLIE:
        a, b, quante = [], [], 0
        for r in test:
            lam = stime(r, smorzamento)
            avvenuto = r["n"] > soglia
            quante += 1 if avvenuto else 0
            a.append(log_perdita(poisson_oltre(lam[0], soglia), avvenuto))
            b.append(log_perdita(poisson_oltre(lam[2], soglia), avvenuto))
        c = confronto(a, b)
        nome = f"almeno {int(soglia + 0.5)}"
        print(f"  {nome:<16}{quante / len(test):>9.1%}{media(a):>10.4f}{media(b):>11.4f}"
              f"{(c[0] if c else 0):>+11.4f}   {verdetto(c, 'il giocatore', 'il ruolo')}")

    print(f"\n  Calibrazione su 'almeno 1': quando diciamo X%, quante volte succede")
    fasce = defaultdict(list)
    for r in test:
        p = poisson_oltre(stime(r, smorzamento)[2], 0.5)
        fasce[min(4, int(p * 5))].append((p, r["n"] > 0.5))
    for indice in sorted(fasce):
        gruppo = fasce[indice]
        if len(gruppo) < 20:
            continue
        print(f"    diciamo {media([p for p, _ in gruppo]):.0%}"
              f"  -> succede {media([1.0 if a else 0.0 for _, a in gruppo]):.0%}"
              f"   ({len(gruppo)} casi)")

    # ---- dove funziona -----------------------------------------------
    print("\n" + "=" * 76)
    print("4. PER RUOLO")
    print("=" * 76)
    print(f"  {'ruolo':<10}{'casi':>7}{'media vera':>12}{'ruolo':>10}{'smorzata':>11}{'guadagno':>11}")
    per_ruolo_gruppi = defaultdict(list)
    for r in test:
        per_ruolo_gruppi[r["ruolo"]].append(r)
    for ruolo, gruppo in sorted(per_ruolo_gruppi.items(), key=lambda kv: -len(kv[1])):
        if len(gruppo) < 50:
            continue
        a, b = [], []
        for r in gruppo:
            lam = stime(r, smorzamento)
            for soglia in SOGLIE:
                avvenuto = r["n"] > soglia
                a.append(log_perdita(poisson_oltre(lam[0], soglia), avvenuto))
                b.append(log_perdita(poisson_oltre(lam[2], soglia), avvenuto))
        c = confronto(a, b)
        print(f"  {ruolo:<10}{len(gruppo):>7}{media([r['n'] for r in gruppo]):>12.2f}"
              f"{media(a):>10.4f}{media(b):>11.4f}{(c[0] if c else 0):>+11.4f}")

    print("\n" + "=" * 76)
    print("""COME LEGGERE
  Il confronto che conta e' "smorzata contro ruolo": se vince il ruolo,
  conoscere il singolo giocatore non serve e il mercato e' imprendibile.
  Se vince il giocatore, abbiamo una base - e a quel punto la domanda
  diventa se il vantaggio sopravvive al margine del bookmaker, che su
  questi mercati e' piu' alto che sull'1X2.""")


if __name__ == "__main__":
    main()
