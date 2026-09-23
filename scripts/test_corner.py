"""
SAPPIAMO PREVEDERE I CORNER?
============================
Prima di sperare in un vantaggio sui mercati dei corner bisogna sapere
se sappiamo prevederli. Questo test lo verifica sulle partite che
abbiamo gia' in archivio. Non tocca il modello dei gol e non scrive
niente nel database.

COSA FA
-------
Costruisce una stima dei corner attesi con la stessa struttura del
modello dei gol:

    corner attesi casa = media del campionato
                       x attacco-corner della squadra di casa
                       x difesa-corner dell'avversario
                       x fattore campo

dove attacco e difesa sono medie pesate delle partite precedenti,
normalizzate per campionato (in Cina si battono piu' corner che in
Colombia), tirate verso 1 quando le partite sono poche.

Ogni previsione usa SOLO le partite precedenti a quella: e' gia' una
prova fuori campione. Il punteggio finale si legge sull'ultimo terzo
delle partite in ordine di data.

LE TRE DOMANDE
--------------
1. I corner seguono una Poisson?
   Se la varianza e' molto piu' grande della media, le probabilita'
   Over/Under sarebbero sbagliate anche indovinando la media. Si prova
   anche la binomiale negativa, che ammette piu' dispersione.

2. Battiamo la media del campionato?
   E' il confronto onesto: prevedere "il solito numero di corner di
   questo campionato" e' gratis. Se non lo battiamo, il modello non
   serve a niente.

3. Le probabilita' Over/Under sono calibrate?
   Quando diciamo 60%, succede il 60% delle volte? Senza questo, anche
   una buona media non si trasforma in scommesse sensate.

USO:
    python scripts/test_corner.py
"""

import os
import math
import random
import sqlite3
from collections import defaultdict

DB_PATH = "calcio_dati.db"
DECADIMENTO = 0.95
PESO_STAGIONE_PRECEDENTE = 0.3
SHRINK = 6.0
MIN_PARTITE = 4
QUOTA_TEST = 0.33
SOGLIE = (8.5, 9.5, 10.5, 11.5)
RICAMPIONAMENTI = 2000
random.seed(19)


# ---------------------------------------------------------------
#  dati
# ---------------------------------------------------------------

def carica(conn):
    righe = conn.execute("""
        SELECT fixture_id, team_id, opponent_id, league_id, season, date,
               is_home, corners
        FROM team_match
        WHERE corners IS NOT NULL
        ORDER BY date
    """).fetchall()
    campi = ("fixture_id", "team_id", "opponent_id", "league_id", "season",
             "date", "is_home", "corners")
    dati = [dict(zip(campi, r)) for r in righe]
    subiti = {(r["fixture_id"], r["team_id"]): r["corners"] for r in dati}
    for r in dati:
        r["subiti"] = subiti.get((r["fixture_id"], r["opponent_id"]))
    return [r for r in dati if r["subiti"] is not None]


def medie_espansive(dati):
    """Media corner del campionato usando solo le giornate precedenti."""
    per_lega = defaultdict(list)
    for r in dati:
        per_lega[r["league_id"]].append(r)
    fuori = {}
    for lista in per_lega.values():
        somma = conta = 0.0
        buffer, data_prec = [], None
        for r in lista:
            if data_prec is not None and r["date"] != data_prec:
                for b in buffer:
                    somma += b["corners"]
                    conta += 1
                buffer = []
            fuori[(r["fixture_id"], r["team_id"])] = somma / conta if conta >= 40 else None
            buffer.append(r)
            data_prec = r["date"]
    return fuori


def forze(dati, medie):
    """Attacco e difesa corner di ogni squadra, con i dati precedenti."""
    per_squadra = defaultdict(list)
    for r in dati:
        per_squadra[r["team_id"]].append(r)
    fuori = {}
    for storia in per_squadra.values():
        for i, r in enumerate(storia):
            prec = [p for p in storia[:i] if p["date"] < r["date"]][::-1]
            media_ora = medie.get((r["fixture_id"], r["team_id"]))
            if media_ora is None or len(prec) < MIN_PARTITE:
                continue
            num_a = den_a = num_d = den_d = 0.0
            for k, p in enumerate(prec):
                media_p = medie.get((p["fixture_id"], p["team_id"]))
                if not media_p:
                    continue
                peso = (DECADIMENTO ** k) * (1.0 if p["season"] == r["season"]
                                             else PESO_STAGIONE_PRECEDENTE)
                num_a += peso * (p["corners"] / media_p)
                den_a += peso
                num_d += peso * (p["subiti"] / media_p)
                den_d += peso
            if den_a <= 0:
                continue
            n = len(prec)
            att = 1.0 + (num_a / den_a - 1.0) * n / (n + SHRINK)
            dif = 1.0 + (num_d / den_d - 1.0) * n / (n + SHRINK)
            fuori[(r["fixture_id"], r["team_id"])] = (att, dif, media_ora, n)
    return fuori


def partite(dati, f):
    per_fixture = defaultdict(list)
    for r in dati:
        per_fixture[r["fixture_id"]].append(r)
    fuori = []
    for fid, coppia in per_fixture.items():
        if len(coppia) != 2:
            continue
        casa = next((x for x in coppia if x["is_home"] == 1), None)
        ospite = next((x for x in coppia if x["is_home"] == 0), None)
        if not casa or not ospite:
            continue
        a, b = f.get((fid, casa["team_id"])), f.get((fid, ospite["team_id"]))
        if not a or not b:
            continue
        fuori.append({"fid": fid, "data": casa["date"], "lega": casa["league_id"],
                      "casa": a, "ospite": b,
                      "totale": casa["corners"] + ospite["corners"],
                      "media_lega": a[2]})
    fuori.sort(key=lambda m: m["data"])
    return fuori


# ---------------------------------------------------------------
#  modello e probabilita'
# ---------------------------------------------------------------

def attesi(m, fattore):
    att_c, dif_c, media, _ = m["casa"]
    att_o, dif_o, _, _ = m["ospite"]
    radice = math.sqrt(fattore)
    return (media * att_c * dif_o * radice) + (media * att_o * dif_c / radice)


def poisson_over(lam, soglia):
    """P(totale > soglia) con Poisson."""
    limite = int(math.floor(soglia))
    somma, termine = 0.0, math.exp(-lam)
    for k in range(limite + 1):
        if k:
            termine *= lam / k
        somma += termine
    return max(1e-9, min(1 - 1e-9, 1 - somma))


def binneg_over(lam, soglia, dispersione):
    """Come sopra ma con binomiale negativa: ammette piu' dispersione."""
    r = max(1.0, lam / max(1e-6, dispersione - 1.0))
    p = r / (r + lam)
    limite = int(math.floor(soglia))
    somma = termine = p ** r
    for k in range(1, limite + 1):
        termine *= (r + k - 1) / k * (1 - p)
        somma += termine
    return max(1e-9, min(1 - 1e-9, 1 - somma))


def perdita(prob, avvenuto):
    return -math.log(prob if avvenuto else 1 - prob)


def media(v):
    return sum(v) / len(v) if v else float("nan")


def confronto(a, b):
    d = [x - y for x, y in zip(a, b)]
    n = len(d)
    if n < 30:
        return None
    medie = sorted(sum(d[random.randrange(n)] for _ in range(n)) / n
                   for _ in range(RICAMPIONAMENTI))
    return media(d), medie[int(.025 * RICAMPIONAMENTI)], medie[int(.975 * RICAMPIONAMENTI)]


def verdetto(c, meglio="il modello", peggio="la media del campionato"):
    if not c:
        return "troppo poche partite"
    _, lo, hi = c
    if lo > 0:
        return f"vince {meglio}, dimostrato"
    if hi < 0:
        return f"vince {peggio}, dimostrato"
    return "non distinguibili"


# ---------------------------------------------------------------

def main():
    if not os.path.exists(DB_PATH):
        print(f"{DB_PATH} non trovato.")
        return
    conn = sqlite3.connect(DB_PATH)
    print("Carico i corner e ricostruisco le forze (un paio di minuti)...")
    dati = carica(conn)
    medie = medie_espansive(dati)
    elenco = partite(dati, forze(dati, medie))
    conn.close()
    if len(elenco) < 300:
        print(f"Solo {len(elenco)} partite utilizzabili: troppo poche.")
        return

    taglio = int(len(elenco) * (1 - QUOTA_TEST))
    allenamento, test = elenco[:taglio], elenco[taglio:]
    fattore = (sum(m["casa"][0] for m in allenamento) /
               max(1e-6, sum(m["ospite"][0] for m in allenamento)))
    tot = [m["totale"] for m in allenamento]
    mu = media(tot)
    var = media([(x - mu) ** 2 for x in tot])
    dispersione = var / mu

    print(f"\nPartite utilizzabili: {len(elenco)}  "
          f"(stima su {len(allenamento)}, giudizio su {len(test)})")
    print(f"Periodo del test: dal {test[0]['data'][:10]} al {test[-1]['data'][:10]}")

    print("\n" + "=" * 74)
    print("1. I CORNER SEGUONO UNA POISSON?")
    print("=" * 74)
    print(f"  media corner per partita: {mu:.2f}   varianza: {var:.2f}")
    print(f"  rapporto varianza/media: {dispersione:.2f}")
    if dispersione > 1.25:
        print("  Piu' dispersi di una Poisson: le probabilita' Over/Under")
        print("  calcolate con Poisson sarebbero troppo sicure. Serve la")
        print("  binomiale negativa, che il test confronta piu' sotto.")
    else:
        print("  Vicini a una Poisson: si puo' usare quella.")

    print("\n" + "=" * 74)
    print("2. BATTIAMO LA MEDIA DEL CAMPIONATO? (errore sui corner attesi)")
    print("=" * 74)
    nostri = [abs(attesi(m, fattore) - m["totale"]) for m in test]
    base = [abs(2 * m["media_lega"] - m["totale"]) for m in test]
    c = confronto(base, nostri)
    print(f"  errore medio del modello:            {media(nostri):.3f} corner")
    print(f"  errore medio della media di lega:    {media(base):.3f} corner")
    if c:
        print(f"  guadagno: {c[0]:+.3f} corner  [{c[1]:+.3f}, {c[2]:+.3f}]  -> {verdetto(c)}")

    print("\n" + "=" * 74)
    print("3. LE PROBABILITA' OVER/UNDER SONO UTILI E CALIBRATE?")
    print("=" * 74)
    print(f"  {'soglia':<9}{'noi (Poisson)':>15}{'binom. neg.':>13}{'media lega':>12}"
          f"{'guadagno':>11}   esito")
    for soglia in SOGLIE:
        pn, pb, pm, avvenuti = [], [], [], []
        for m in test:
            lam = attesi(m, fattore)
            lam_base = 2 * m["media_lega"]
            avvenuto = m["totale"] > soglia
            avvenuti.append(avvenuto)
            pn.append(perdita(poisson_over(lam, soglia), avvenuto))
            pb.append(perdita(binneg_over(lam, soglia, dispersione), avvenuto))
            pm.append(perdita(poisson_over(lam_base, soglia), avvenuto))
        c = confronto(pm, pb)
        print(f"  {soglia:<9.1f}{media(pn):>15.4f}{media(pb):>13.4f}{media(pm):>12.4f}"
              f"{(c[0] if c else 0):>+11.4f}   {verdetto(c)}")

    print("\n  Calibrazione sulla soglia 9.5: quando diciamo X%, quante volte succede")
    fasce = defaultdict(list)
    for m in test:
        p = binneg_over(attesi(m, fattore), 9.5, dispersione)
        fasce[min(4, int(p * 5))].append((p, m["totale"] > 9.5))
    for indice in sorted(fasce):
        gruppo = fasce[indice]
        if len(gruppo) < 20:
            continue
        print(f"    diciamo {media([p for p, _ in gruppo]):.0%}"
              f"  -> succede {media([1.0 if a else 0.0 for _, a in gruppo]):.0%}"
              f"   ({len(gruppo)} partite)")

    print("\n" + "=" * 74)
    print("4. DOVE FUNZIONA MEGLIO (soglia 9.5, binomiale negativa)")
    print("=" * 74)
    conn = sqlite3.connect(DB_PATH)
    nomi = {r[0]: f"{r[2]} - {r[1]}" for r in
            conn.execute("SELECT id, name, country FROM leagues")}
    conn.close()
    per_lega = defaultdict(list)
    for m in test:
        per_lega[m["lega"]].append(m)
    tabella = []
    for lega, gruppo in per_lega.items():
        if len(gruppo) < 60:
            continue
        pb, pm = [], []
        for m in gruppo:
            avvenuto = m["totale"] > 9.5
            pb.append(perdita(binneg_over(attesi(m, fattore), 9.5, dispersione), avvenuto))
            pm.append(perdita(poisson_over(2 * m["media_lega"], 9.5, ), avvenuto))
        c = confronto(pm, pb)
        if c:
            tabella.append((c[0], nomi.get(lega, str(lega)), len(gruppo), c))
    for guadagno, nome, n, c in sorted(tabella, reverse=True):
        print(f"  {nome[:40]:<42}{n:>5} partite  {guadagno:+.4f}  "
              f"[{c[1]:+.4f}, {c[2]:+.4f}]")
    if not tabella:
        print("  Nessun campionato con abbastanza partite nel test.")

    # ---- 5. c'e' segnale, o lo stiamo solo amplificando? -------------
    print("\n" + "=" * 74)
    print("5. C'E' SEGNALE? CORRELAZIONE E SMORZAMENTO")
    print("=" * 74)

    def correlazione(x, y):
        mx, my = media(x), media(y)
        sx = math.sqrt(sum((a - mx) ** 2 for a in x))
        sy = math.sqrt(sum((b - my) ** 2 for b in y))
        return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy) if sx and sy else 0.0

    lam_test = [attesi(m, fattore) for m in test]
    base_test = [2 * m["media_lega"] for m in test]
    veri_test = [m["totale"] for m in test]
    print(f"  correlazione fra corner attesi e corner veri:  {correlazione(lam_test, veri_test):+.3f}")
    print(f"  la stessa per la sola media di lega:           {correlazione(base_test, veri_test):+.3f}")
    print("  Se la prima e' vicina a zero, non c'e' niente da smorzare.")

    # smorzamento: quanto conviene dare retta alla nostra deviazione
    lam_all = [attesi(m, fattore) for m in allenamento]
    base_all = [2 * m["media_lega"] for m in allenamento]
    veri_all = [m["totale"] for m in allenamento]

    def con_peso(lam, base, w):
        return [b * (l / b) ** w for l, b in zip(lam, base)]

    def punteggio(lams, veri):
        tot = []
        for lam, vero in zip(lams, veri):
            for soglia in SOGLIE:
                tot.append(perdita(binneg_over(lam, soglia, dispersione), vero > soglia))
        return media(tot)

    griglia = [i / 10 for i in range(11)]
    scelte = [(punteggio(con_peso(lam_all, base_all, w), veri_all), w) for w in griglia]
    migliore = min(scelte)[1]
    print(f"\n  Peso migliore da dare alla nostra deviazione (0 = solo media di")
    print(f"  lega, 1 = modello pieno), scelto sulle partite di stima: {migliore:.1f}")
    print("  " + "  ".join(f"{w:.1f}:{s:.4f}" for s, w in sorted(scelte, key=lambda x: x[1])[:6]))

    smorzato = [perdita(binneg_over(l, 9.5, dispersione), m["totale"] > 9.5)
                for l, m in zip(con_peso(lam_test, base_test, migliore), test)]
    solo_lega = [perdita(binneg_over(b, 9.5, dispersione), m["totale"] > 9.5)
                 for b, m in zip(base_test, test)]
    c = confronto(solo_lega, smorzato)
    print(f"\n  Sulla soglia 9.5, con lo smorzamento: {media(smorzato):.4f} "
          f"contro {media(solo_lega):.4f} della sola media di lega")
    if c:
        print(f"  guadagno {c[0]:+.4f} [{c[1]:+.4f}, {c[2]:+.4f}] -> {verdetto(c, 'il modello smorzato')}")

    # ---- 6. solo squadre con storico vero ---------------------------
    print("\n" + "=" * 74)
    print("6. SOLO PARTITE CON SQUADRE GIA' RODATE (almeno 10 partite a testa)")
    print("=" * 74)
    rodate = [m for m in test if m["casa"][3] >= 10 and m["ospite"][3] >= 10]
    if len(rodate) < 100:
        print(f"  Solo {len(rodate)} partite: troppo poche per un giudizio.")
    else:
        lam_r = con_peso([attesi(m, fattore) for m in rodate],
                         [2 * m["media_lega"] for m in rodate], migliore)
        a = [perdita(binneg_over(2 * m["media_lega"], 9.5, dispersione), m["totale"] > 9.5)
             for m in rodate]
        b = [perdita(binneg_over(l, 9.5, dispersione), m["totale"] > 9.5)
             for l, m in zip(lam_r, rodate)]
        c = confronto(a, b)
        print(f"  {len(rodate)} partite: modello {media(b):.4f}, media di lega {media(a):.4f}")
        if c:
            print(f"  guadagno {c[0]:+.4f} [{c[1]:+.4f}, {c[2]:+.4f}] -> {verdetto(c)}")
        print("  Se qui va meglio, il problema era lo scarso storico, non i corner.")

    print("\n" + "=" * 74)
    print("""COME LEGGERE
  Il punto 2 dice se sappiamo stimare QUANTI corner ci saranno; il punto
  3 se sappiamo trasformarlo in probabilita' oneste, che e' cio' che
  serve per scommettere. Un guadagno positivo e dimostrato sul punto 3 e'
  la condizione minima per continuare: senza, le quote che stiamo
  archiviando non servono a niente.
  Anche con esito positivo, resta da vedere il confronto col mercato:
  battere la media del campionato e' molto piu' facile che battere un
  bookmaker.""")


if __name__ == "__main__":
    main()
