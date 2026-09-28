#!/usr/bin/env python3
"""
PERCHE' MANCANO LE QUOTE DI ALCUNE PARTITE

Nell'app il confronto con i bookmaker compare solo se abbiamo le quote
di quella partita. Su alcune non compare, e il tetto di chiamate non
c'entra: il giro per giornata finisce da solo dopo poche pagine.

Questo script guarda le previsioni di adesso e risponde a tre domande:

  1. quante partite hanno le quote e quante no, divise per campionato
     e per giorno: se mancano campionati interi o giorni interi, il
     problema e' la copertura; se mancano a macchia di leopardo, e' il
     giro per giornata che non le trova;

  2. cosa risponde l'API se le quote di una partita mancante le
     chiediamo direttamente per quella partita: se ci sono, il giro
     per giornata se le sta perdendo ed e' un problema nostro; se non
     ci sono, l'API non le ha e non c'e' niente da correggere;

  3. quante pagine dice di avere il giro per giornata, per capire se
     ci fermiamo troppo presto.

Costa pochissime chiamate: una per ognuna delle partite campione, piu'
una per la giornata.

Uso, dalla cartella del progetto:
    python3 scripts/diagnosi_quote.py
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import previsioni as P

QUANTE_PROVE = 5          # partite mancanti da interrogare una a una


def chiama(endpoint, parametri):
    url = f"{P.BASE_URL}/{endpoint}?" + urllib.parse.urlencode(parametri)
    req = urllib.request.Request(url, headers={"x-apisports-key": P.API_KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    if not P.API_KEY:
        print("  Chiave API assente: serve il file ~/.previsioni_env")
        sys.exit(1)
    # il file puo' stare nella cartella di lavoro o gia' spostato nel
    # sito, a seconda di quale giro l'ha prodotto
    sito = os.environ.get("SITO_DIR", "docs")
    percorso = next((x for x in (P.USCITA_JSON,
                                 os.path.join(sito, P.USCITA_JSON),
                                 os.path.join("docs", P.USCITA_JSON))
                     if os.path.exists(x)), None)
    if not percorso:
        print(f"  {P.USCITA_JSON} non trovato ne' qui ne' in {sito}/.")
        print("  Lancialo dalla cartella del progetto, dopo un giro della")
        print("  pipeline.")
        sys.exit(1)
    print(f"  Leggo {percorso}\n")

    with open(percorso, encoding="utf-8") as f:
        previsioni = json.load(f).get("previsioni", [])

    con = [p for p in previsioni if p.get("mercato")]
    senza = [p for p in previsioni if not p.get("mercato")]

    print("=" * 74)
    print("1. CHI HA LE QUOTE E CHI NO")
    print("=" * 74)
    print(f"  partite in previsione: {len(previsioni)}")
    print(f"  con quote:  {len(con)}")
    print(f"  senza:      {len(senza)}")
    if not previsioni:
        return

    print("\n  Per giorno:")
    per_giorno = defaultdict(lambda: [0, 0])
    for p in previsioni:
        per_giorno[p["data"][:10]][0 if p.get("mercato") else 1] += 1
    for giorno in sorted(per_giorno):
        c, s = per_giorno[giorno]
        print(f"    {giorno}   con {c:>3}   senza {s:>3}")

    print("\n  Per campionato (solo dove manca qualcosa):")
    per_lega = defaultdict(lambda: [0, 0])
    for p in previsioni:
        per_lega[p.get("campionato", "?")][0 if p.get("mercato") else 1] += 1
    righe = [(nome, c, s) for nome, (c, s) in per_lega.items() if s]
    righe.sort(key=lambda r: -r[2])
    for nome, c, s in righe[:25]:
        stato = "mai" if c == 0 else "a tratti"
        print(f"    {nome[:40]:<42} con {c:>3}  senza {s:>3}   {stato}")

    interi = sum(1 for _, c, s in righe if c == 0)
    print(f"\n  campionati dove non arriva MAI una quota: {interi} su {len(per_lega)}")
    print("  Se sono tanti, il problema e' la copertura dell'API.")
    print("  Se invece manca a tratti dentro gli stessi campionati, e' il")
    print("  nostro giro per giornata che le perde.")

    if not senza:
        print("\n  Non manca niente: nulla da diagnosticare.")
        return

    # ---- 2. le chiediamo una a una -------------------------------
    print("\n" + "=" * 74)
    print("2. SE LE CHIEDIAMO PARTITA PER PARTITA, CI SONO?")
    print("=" * 74)
    campione = senza[:QUANTE_PROVE]
    trovate = 0
    for p in campione:
        etichetta = f"{p['casa']} – {p['fuori']}"
        try:
            dati = chiama("odds", {"fixture": p["fixture_id"]})
        except Exception as e:
            print(f"  {etichetta[:44]:<46} errore: {e}")
            continue
        time.sleep(0.3)
        risposta = dati.get("response", []) or []
        if not risposta:
            print(f"  {etichetta[:44]:<46} l'API non ha quote per questa partita")
            continue
        voce = risposta[0]
        n_book = len(voce.get("bookmakers", []) or [])
        letta = P.quote_mercati(voce)
        if letta:
            trovate += 1
            print(f"  {etichetta[:44]:<46} CI SONO: {n_book} bookmaker, "
                  f"1={letta['1']:.0%} X={letta['X']:.0%} 2={letta['2']:.0%}")
        else:
            print(f"  {etichetta[:44]:<46} {n_book} bookmaker ma nessun 1X2 "
                  "leggibile")

    print(f"\n  Su {len(campione)} partite senza quote, chiedendole una a una")
    print(f"  ne abbiamo trovate {trovate}.")
    if trovate:
        print("  -> Le quote esistono: il giro per giornata se le perde.")
        print("     La correzione e' chiedere una a una quelle che mancano,")
        print(f"     e costerebbe circa {len(senza)} chiamate in piu' a giro.")
    else:
        print("  -> L'API non le ha proprio: non c'e' niente da correggere,")
        print("     quelle partite resteranno senza confronto.")

    # ---- 3. quante pagine ha il giro per giornata ----------------
    print("\n" + "=" * 74)
    print("3. QUANTE PAGINE HA IL GIRO PER GIORNATA")
    print("=" * 74)
    giorno = min(p["data"][:10] for p in previsioni)
    try:
        dati = chiama("odds", {"date": giorno, "page": 1})
        paging = dati.get("paging") or {}
        print(f"  giorno {giorno}: {dati.get('results', 0)} risultati in questa "
              f"pagina, {paging.get('total', '?')} pagine in tutto")
        nostre = {p["fixture_id"] for p in previsioni}
        in_pagina = sum(1 for v in (dati.get("response") or [])
                        if (v.get("fixture") or {}).get("id") in nostre)
        print(f"  di cui nostre, in questa pagina: {in_pagina}")
        print("\n  Se le pagine totali sono poche e dentro ci sono soprattutto")
        print("  partite non nostre, il giro per giornata e' lo strumento")
        print("  sbagliato: conviene chiedere per partita.")
    except Exception as e:
        print(f"  errore: {e}")

    print("\n" + "=" * 74)


if __name__ == "__main__":
    main()
