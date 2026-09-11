"""
CONFRONTO CON LE QUOTE DEI BOOKMAKER
=====================================
Le quote sono il termine di paragone piu' severo che esista: incorporano
informazioni che noi non abbiamo (formazioni, notizie, mercato) e sono
notoriamente ben calibrate. Servono qui come metro di misura, non come
bersaglio da battere.

COSA CALCOLA
------------
1. Il margine del bookmaker
   Le probabilita' implicite in una quota sommano sempre a piu' di 1:
   la differenza e' il margine. Va tolto prima di ogni confronto,
   altrimenti confronteremmo mele con pere.

2. Quanto siamo distanti dal mercato
   Errore medio sulle tre probabilita' e correlazione.

3. Se siamo troppo sicuri di noi
   Confronta quanto sono estreme le nostre probabilita' rispetto alle
   loro. Nel backtesting il modello risultava gia' troppo sicuro, e la
   correzione a shrinkage aveva migliorato le cose.

4. La "temperatura" che ci allineerebbe al mercato
   Cerca l'esponente T per cui p elevato a 1/T, rinormalizzato, minimizza
   la distanza dal mercato.
     T > 1  ->  siamo troppo estremi, andrebbero ammorbidite
     T < 1  ->  siamo troppo timidi
     T = 1  ->  siamo in linea

5. Le divergenze maggiori, da guardare a occhio: se le partite dove ci
   discostiamo di piu' hanno una spiegazione sensata, e' un conto; se
   sembrano casuali, e' un altro.

USO:
    python confronto_quote.py
"""

import os
import sys
import json
import time
import math
import urllib.request
import urllib.parse

PREVISIONI = "previsioni.json"
BASE_URL = "https://v3.football.api-sports.io"
API_KEY = os.environ.get("API_FOOTBALL_KEY", "").strip()

MAX_CHIAMATE = 300
USCITA = "confronto_quote.json"


def chiamata(endpoint, params):
    url = f"{BASE_URL}/{endpoint}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"x-apisports-key": API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            dati = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"    [ERRORE] {endpoint}: {e}")
        return [], {}
    time.sleep(0.3)
    if dati.get("errors"):
        print(f"    [ERRORE API] {dati['errors']}")
        return [], {}
    return dati.get("response", []), dati.get("paging", {})


def quote_da_risposta(voce):
    """
    Estrae le probabilita' 1X2 medie tra i bookmaker, tolto il margine.
    Restituisce (p1, px, p2, margine_medio, n_bookmaker) o None.
    """
    raccolte = []
    for book in voce.get("bookmakers", []) or []:
        for scommessa in book.get("bets", []) or []:
            nome = (scommessa.get("name") or "").lower()
            if nome not in ("match winner", "1x2", "fulltime result"):
                continue
            valori = {}
            for v in scommessa.get("values", []) or []:
                etichetta = str(v.get("value", "")).strip().lower()
                try:
                    quota = float(v.get("odd"))
                except (TypeError, ValueError):
                    continue
                if quota <= 1.0:
                    continue
                if etichetta in ("home", "1"):
                    valori["1"] = quota
                elif etichetta in ("draw", "x"):
                    valori["X"] = quota
                elif etichetta in ("away", "2"):
                    valori["2"] = quota
            if len(valori) == 3:
                grezze = [1 / valori["1"], 1 / valori["X"], 1 / valori["2"]]
                somma = sum(grezze)
                if 1.0 < somma < 1.5:
                    raccolte.append(([g / somma for g in grezze], somma - 1))
            break

    if not raccolte:
        return None
    n = len(raccolte)
    p1 = sum(r[0][0] for r in raccolte) / n
    px = sum(r[0][1] for r in raccolte) / n
    p2 = sum(r[0][2] for r in raccolte) / n
    margine = sum(r[1] for r in raccolte) / n
    return p1, px, p2, margine, n


def applica_temperatura(p, T):
    """Ammorbidisce (T>1) o accentua (T<1) una distribuzione di probabilita'."""
    if T <= 0:
        return p
    valori = [max(x, 1e-9) ** (1.0 / T) for x in p]
    s = sum(valori)
    return [v / s for v in valori]


def distanza(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def main():
    if not os.path.exists(PREVISIONI):
        print(f"{PREVISIONI} non trovato: esegui prima previsioni.py")
        sys.exit(1)
    if not API_KEY:
        print("API_FOOTBALL_KEY mancante.")
        sys.exit(1)

    with open(PREVISIONI, encoding="utf-8") as f:
        dati = json.load(f)
    previsioni = {p["fixture_id"]: p for p in dati["previsioni"]}
    print(f"Previsioni da confrontare: {len(previsioni)}")

    # le quote arrivano per giornata: poche chiamate invece di una per partita
    giorni = sorted({p["data"][:10] for p in previsioni.values()})
    print(f"Giornate coinvolte: {len(giorni)}")

    quote = {}
    chiamate = 0
    for giorno in giorni:
        pagina = 1
        while chiamate < MAX_CHIAMATE:
            risposta, paging = chiamata("odds", {"date": giorno, "page": pagina})
            chiamate += 1
            if not risposta:
                break
            for voce in risposta:
                fid = (voce.get("fixture") or {}).get("id")
                if fid in previsioni:
                    estratte = quote_da_risposta(voce)
                    if estratte:
                        quote[fid] = estratte
            totale = (paging or {}).get("total", 1)
            if pagina >= totale:
                break
            pagina += 1
        print(f"  {giorno}: quote trovate finora {len(quote)}")

    print(f"\nChiamate usate: {chiamate}")
    if not quote:
        print("Nessuna quota disponibile. Il piano potrebbe non includere le quote,")
        print("oppure non sono ancora pubblicate per queste partite.")
        sys.exit(0)

    coppie = []
    for fid, (m1, mx, m2, margine, nb) in quote.items():
        p = previsioni[fid]["mercati"]
        coppie.append({
            "fixture_id": fid,
            "partita": f"{previsioni[fid]['casa']} - {previsioni[fid]['fuori']}",
            "campionato": previsioni[fid]["campionato"],
            "nostro": [p["1"], p["X"], p["2"]],
            "mercato": [m1, mx, m2],
            "margine": margine,
            "bookmaker": nb,
        })

    print("=" * 70)
    print("COPERTURA")
    print("=" * 70)
    print(f"Partite con quote: {len(coppie)} su {len(previsioni)} "
          f"({len(coppie)/len(previsioni)*100:.0f}%)")
    margine_medio = sum(c["margine"] for c in coppie) / len(coppie)
    book_medi = sum(c["bookmaker"] for c in coppie) / len(coppie)
    print(f"Margine medio dei bookmaker: {margine_medio*100:.1f}%")
    print(f"Bookmaker per partita (media): {book_medi:.1f}")

    print("\n" + "=" * 70)
    print("QUANTO SIAMO DISTANTI DAL MERCATO")
    print("=" * 70)
    err = sum(distanza(c["nostro"], c["mercato"]) for c in coppie) / len(coppie)
    print(f"Errore medio sulle tre probabilita': {err*100:.2f} punti percentuali")

    # correlazione sulla vittoria interna
    xs = [c["nostro"][0] for c in coppie]
    ys = [c["mercato"][0] for c in coppie]
    mx_, my_ = sum(xs)/len(xs), sum(ys)/len(ys)
    num = sum((a-mx_)*(b-my_) for a, b in zip(xs, ys))
    den = math.sqrt(sum((a-mx_)**2 for a in xs) * sum((b-my_)**2 for b in ys))
    corr = num/den if den > 0 else 0
    print(f"Correlazione sulla vittoria interna: {corr:.3f}")
    if corr > 0.85:
        print("  -> molto alta: vediamo le partite in modo simile al mercato")
    elif corr > 0.6:
        print("  -> buona: concordiamo sulla direzione, non sull'intensita'")
    else:
        print("  -> bassa: valutiamo le partite in modo sostanzialmente diverso")

    print("\n" + "=" * 70)
    print("SIAMO TROPPO SICURI?")
    print("=" * 70)
    nostro_max = sum(max(c["nostro"]) for c in coppie) / len(coppie)
    mercato_max = sum(max(c["mercato"]) for c in coppie) / len(coppie)
    nostro_pareggio = sum(c["nostro"][1] for c in coppie) / len(coppie)
    mercato_pareggio = sum(c["mercato"][1] for c in coppie) / len(coppie)
    print(f"{'':<28} {'noi':>8} {'mercato':>10}")
    print("-" * 50)
    print(f"{'probabilita piu alta (media)':<28} {nostro_max:>7.1%} {mercato_max:>10.1%}")
    print(f"{'probabilita di pareggio':<28} {nostro_pareggio:>7.1%} {mercato_pareggio:>10.1%}")
    estremi_nostri = sum(1 for c in coppie if max(c["nostro"]) > 0.65)
    estremi_mercato = sum(1 for c in coppie if max(c["mercato"]) > 0.65)
    print(f"{'partite oltre il 65%':<28} {estremi_nostri:>8} {estremi_mercato:>10}")

    print("\n" + "=" * 70)
    print("TEMPERATURA CHE CI ALLINEEREBBE")
    print("=" * 70)
    migliore = None
    for i in range(60, 201):
        T = i / 100
        d = sum(distanza(applica_temperatura(c["nostro"], T), c["mercato"])
                for c in coppie) / len(coppie)
        if migliore is None or d < migliore[1]:
            migliore = (T, d)
    T, d = migliore
    print(f"Temperatura ottimale: {T:.2f}")
    print(f"Errore medio con quella temperatura: {d*100:.2f} punti "
          f"(prima era {err*100:.2f})")
    if T > 1.08:
        print("  -> le nostre probabilita' sono TROPPO ESTREME: andrebbero ammorbidite")
    elif T < 0.92:
        print("  -> le nostre probabilita' sono TROPPO TIMIDE")
    else:
        print("  -> siamo sostanzialmente in linea con il mercato")

    print("\n" + "=" * 70)
    print("DIVERGENZE MAGGIORI")
    print("=" * 70)
    coppie.sort(key=lambda c: -distanza(c["nostro"], c["mercato"]))
    print(f"{'partita':<34} {'noi (1/X/2)':>18} {'mercato':>18}")
    print("-" * 72)
    for c in coppie[:10]:
        n = "/".join(f"{x*100:.0f}" for x in c["nostro"])
        m = "/".join(f"{x*100:.0f}" for x in c["mercato"])
        print(f"{c['partita'][:33]:<34} {n:>18} {m:>18}")

    print("\nDove piu' concordiamo:")
    for c in coppie[-5:]:
        n = "/".join(f"{x*100:.0f}" for x in c["nostro"])
        m = "/".join(f"{x*100:.0f}" for x in c["mercato"])
        print(f"{c['partita'][:33]:<34} {n:>18} {m:>18}")

    with open(USCITA, "w", encoding="utf-8") as f:
        json.dump({"temperatura_ottimale": T, "errore_medio": err,
                   "correlazione": corr, "margine_medio": margine_medio,
                   "confronti": coppie}, f, ensure_ascii=False, indent=1)
    print(f"\nDettaglio salvato in {USCITA}")


if __name__ == "__main__":
    main()
