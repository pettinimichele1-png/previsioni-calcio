#!/usr/bin/env python3
"""
QUOTE LIVE: COSA CI PASSA L'API?

Controllo veloce, da lanciare mentre ci sono partite in corso. Dice:
  - quante partite in corso hanno quote live;
  - da quali bookmaker arrivano (se l'API lo dice);
  - quali mercati ci sono e con che valori, per due o tre partite,
    meglio se una e' all'intervallo;
  - se le quote sono sospese.
Serve a capire se si puo' fare la prova sulla carta all'intervallo.

Uso, dalla cartella del progetto, con partite in corso:
    source ~/.previsioni_env
    python3 scripts/prova_live.py
Costa due chiamate all'API.
"""

import json
import os
import sys
import urllib.parse
import urllib.request

BASE_URL = "https://v3.football.api-sports.io"
API_KEY = os.environ.get("API_FOOTBALL_KEY", "").strip()


def chiama(endpoint, parametri):
    url = f"{BASE_URL}/{endpoint}?" + urllib.parse.urlencode(parametri)
    req = urllib.request.Request(url, headers={"x-apisports-key": API_KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    if not API_KEY:
        print("  Chiave API assente. Prima: source ~/.previsioni_env")
        sys.exit(1)

    # nomi, minuto e punteggio delle partite in corso
    partite = {}
    try:
        for f in chiama("fixtures", {"live": "all"}).get("response") or []:
            fx, st = f.get("fixture") or {}, (f.get("fixture") or {}).get("status") or {}
            partite[fx.get("id")] = {
                "nome": f"{f['teams']['home']['name']} - {f['teams']['away']['name']}",
                "lega": f"{(f.get('league') or {}).get('country', '')} - {(f.get('league') or {}).get('name', '')}",
                "stato": st.get("short"), "minuto": st.get("elapsed"),
                "punteggio": f"{(f.get('goals') or {}).get('home')}-{(f.get('goals') or {}).get('away')}"}
    except Exception as e:
        print(f"  errore dall'API (partite in corso): {e}")
    print(f"  partite in corso adesso: {len(partite)}")

    try:
        dati = chiama("odds/live", {})
    except Exception as e:
        print(f"  errore dall'API (quote live): {e}")
        return
    if dati.get("errors"):
        print(f"  l'API risponde: {dati['errors']}")
        return
    voci = dati.get("response") or []
    print(f"  partite in corso con quote live: {len(voci)}")
    if not voci:
        print("  Nessuna: riprova quando ci sono partite in corso (per esempio stasera o sabato).")
        return

    print(f"\n  Campi di ogni partita: {', '.join(sorted(voci[0].keys()))}")
    libri = set()
    for v in voci:
        for b in v.get("bookmakers") or []:
            libri.add(b.get("name"))
    print("  Bookmaker indicati: " + (", ".join(sorted(libri)) if libri
                                     else "nessuno (le quote arrivano da una fonte sola, senza nome)"))

    # prima le partite all'intervallo, poi le altre
    def ordine(v):
        fid = (v.get("fixture") or {}).get("id")
        return 0 if (partite.get(fid) or {}).get("stato") == "HT" else 1
    for v in sorted(voci, key=ordine)[:3]:
        fid = (v.get("fixture") or {}).get("id")
        info = partite.get(fid, {})
        print("\n" + "-" * 88)
        print(f"  {info.get('nome', fid)}   [{info.get('lega', '')}]   "
              f"{info.get('stato', '?')} {info.get('minuto', '')}'   {info.get('punteggio', '')}")
        stato = v.get("status")
        if stato:
            print(f"  stato delle quote: {stato}")
        mercati = v.get("odds") or []
        for b in v.get("bookmakers") or []:
            mercati = mercati or b.get("bets") or []
        print(f"  mercati: {len(mercati)}")
        for m in mercati[:25]:
            valori = m.get("values") or []
            esempio = ", ".join(
                f"{x.get('value')}{' ' + str(x.get('handicap')) if x.get('handicap') not in (None, '') else ''}"
                f" @ {x.get('odd')}{' (sospesa)' if x.get('suspended') else ''}"
                for x in valori[:4])
            print(f"    {str(m.get('name'))[:34]:<34} {esempio[:90]}")
    print("\n  Mandami questa uscita.")


if __name__ == "__main__":
    main()
