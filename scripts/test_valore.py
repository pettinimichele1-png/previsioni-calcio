#!/usr/bin/env python3
"""
SI GUADAGNA GIOCANDO SOLO QUANDO IL BOOKMAKER PAGA TROPPO?

Il nostro modello non batte il mercato: lo abbiamo verificato in tutti
i modi. Questo test non usa il nostro modello per niente. Verifica
l'unica strada documentata che non richiede di prevedere meglio dei
bookmaker: usare i bookmaker contro se stessi.

L'IDEA
------
Pinnacle e' il bookmaker piu' preciso al mondo: accetta puntate enormi
e non limita chi vince, quindi le sue quote vengono corrette di
continuo da chi scommette meglio. Tolto il suo margine, la sua quota e'
la miglior stima pubblica della probabilita' vera.

I bookmaker "normali" (bet365, William Hill, bwin...) aggiornano i
prezzi piu' lentamente e a volte sbagliano. Quando pagano PIU' del
prezzo giusto di Pinnacle, quella giocata ha un valore atteso positivo.
Si gioca solo li'. Pinnacle in Italia non e' utilizzabile, ma non serve
giocarci: basta leggerne i prezzi.

E' quello che vendono i servizi a pagamento di "value betting". Qui lo
verifichiamo da soli, gratis, su dati storici veri.

LA PROVA PIU' IMPORTANTE: LA CHIUSURA
-------------------------------------
Il guadagno di poche centinaia di giocate e' pieno di rumore: basta
una serie fortunata. Esiste una misura molto piu' stabile: confrontare
la quota presa con la quota di CHIUSURA di Pinnacle, cioe' il prezzo
finale del mercato piu' efficiente. Se in media prendi quote piu' alte
della chiusura, hai un vantaggio vero, qualunque cosa dicano i primi
risultati. Si chiama CLV, closing line value, ed e' il modo in cui i
professionisti capiscono se stanno vincendo per merito o per fortuna.

I DATI
------
football-data.co.uk: CSV gratuiti con le quote di bet365, William
Hill, bwin, Pinnacle e altri, prima della partita e alla chiusura,
per 16 campionati europei. Nessuna chiave, nessuna registrazione.

CONTROLLI
---------
  - si escludono quote sopra 15 e vantaggi sopra il 25%: sono quasi
    sempre errori nei dati, e la letteratura ha mostrato che profitti
    "scoperti" in passato venivano proprio da li'
  - si separa per stagione: se funzionava nel 2020 ma non piu' oggi,
    e' storia, non un'opportunita'
  - fascia di incertezza su ogni numero

Uso, dal server (che ha internet):
    python3 scripts/test_valore.py           scarica e analizza
    python3 scripts/test_valore.py locale    usa i CSV gia' scaricati
"""

import csv
import io
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

CARTELLA = os.environ.get("CARTELLA_VALORE", "dati_valore")
URL = "https://www.football-data.co.uk/mmz4281/{s}/{l}.csv"

LEGHE = {
    "E0": "Premier League", "E1": "Championship",
    "SP1": "La Liga", "SP2": "Segunda", "I1": "Serie A", "I2": "Serie B",
    "D1": "Bundesliga", "D2": "2. Bundesliga", "F1": "Ligue 1",
    "F2": "Ligue 2", "N1": "Eredivisie", "B1": "Jupiler", "P1": "Portogallo",
    "T1": "Turchia", "G1": "Grecia", "SC0": "Scozia",
}
STAGIONI = ["1920", "2021", "2122", "2223", "2324", "2425", "2526", "2627"]
CORRENTE = "2627"
RECENTI = {"2324", "2425", "2526", "2627"}

# bookmaker da mettere alla prova, con il nome leggibile.
# Quelli con licenza italiana sono i piu' interessanti per te.
LIBRI = {
    "B365": "bet365", "WH": "William Hill", "BW": "bwin",
    "IW": "Interwetten", "VC": "BetVictor", "BV": "BetVictor (nuovo)",
    "1XB": "1xBet", "BF": "Betfair Sportsbook", "LB": "Ladbrokes",
    "Max": "MIGLIORE AL MONDO",
}
ITALIANI = {"B365", "WH", "BW", "BF"}   # hanno avuto un sito con licenza ADM
SOGLIE = [0.00, 0.02, 0.04, 0.06]
QUOTA_MAX = 15.0
VANTAGGIO_MAX = 0.25
PUNTATA = 10.0


# ---------------------------------------------------------------
#  scaricamento e lettura
# ---------------------------------------------------------------

def scarica(solo_locale):
    os.makedirs(CARTELLA, exist_ok=True)
    scaricati = saltati = 0
    for s in STAGIONI:
        for l in LEGHE:
            percorso = os.path.join(CARTELLA, f"{s}_{l}.csv")
            # la stagione in corso si aggiorna sempre, le altre una volta
            if os.path.exists(percorso) and (s != CORRENTE or solo_locale):
                continue
            if solo_locale:
                continue
            try:
                req = urllib.request.Request(URL.format(s=s, l=l),
                                             headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    dati = r.read()
                with open(percorso, "wb") as f:
                    f.write(dati)
                scaricati += 1
            except Exception:
                saltati += 1
            time.sleep(0.4)
    if not solo_locale:
        print(f"  file scaricati: {scaricati}, non disponibili: {saltati}")


def num(riga, chiave):
    v = (riga.get(chiave) or "").strip()
    try:
        x = float(v)
        return x if x > 1.0 else None
    except ValueError:
        return None


def leggi():
    partite = []
    for s in STAGIONI:
        for l in LEGHE:
            percorso = os.path.join(CARTELLA, f"{s}_{l}.csv")
            if not os.path.exists(percorso):
                continue
            with open(percorso, "rb") as f:
                grezzo = f.read()
            testo = None
            for cod in ("utf-8-sig", "latin-1"):
                try:
                    testo = grezzo.decode(cod)
                    break
                except UnicodeDecodeError:
                    pass
            if not testo:
                continue
            for riga in csv.DictReader(io.StringIO(testo)):
                riga = {(k or "").strip().lstrip("﻿"): v for k, v in riga.items()}
                ftr = (riga.get("FTR") or "").strip()
                if ftr not in ("H", "D", "A"):
                    continue
                try:
                    gol = int(riga.get("FTHG")) + int(riga.get("FTAG"))
                except (TypeError, ValueError):
                    gol = None
                p = {"stagione": s, "lega": l, "esito": ftr, "gol": gol,
                     "casa": riga.get("HomeTeam", ""), "fuori": riga.get("AwayTeam", "")}
                # 1X2: prima della partita e alla chiusura
                for pref in list(LIBRI) + ["PS", "Avg"]:
                    q = [num(riga, f"{pref}{k}") for k in "HDA"]
                    if all(q):
                        p[pref] = dict(zip("HDA", q))
                    qc = [num(riga, f"{pref}C{k}") for k in "HDA"]
                    if all(qc):
                        p[pref + "_C"] = dict(zip("HDA", qc))
                # Over/Under 2.5
                for pref, nome in (("B365", "B365"), ("P", "PS"), ("Max", "Max"),
                                   ("Avg", "Avg"), ("WH", "WH"), ("1XB", "1XB")):
                    o, u = num(riga, f"{pref}>2.5"), num(riga, f"{pref}<2.5")
                    if o and u:
                        p[nome + "_OU"] = {"O": o, "U": u}
                    oc, uc = num(riga, f"{pref}C>2.5"), num(riga, f"{pref}C<2.5")
                    if oc and uc:
                        p[nome + "_OUC"] = {"O": oc, "U": uc}
                partite.append(p)
    return partite


# ---------------------------------------------------------------
#  la strategia
# ---------------------------------------------------------------

def giusto(quote):
    """Probabilita' senza margine: il prezzo giusto secondo quelle quote."""
    grezze = {k: 1.0 / q for k, q in quote.items()}
    s = sum(grezze.values())
    return {k: v / s for k, v in grezze.items()}


def giocate(partite, libro, riferimento, soglia, mercato="1X2"):
    """
    Tutte le giocate che la regola avrebbe fatto: si gioca un esito
    quando la quota del libro, moltiplicata per la probabilita' giusta
    del riferimento, supera 1 + soglia.
    """
    suf = "" if mercato == "1X2" else "_OU"
    out, scartate = [], 0
    for p in partite:
        q_lib = p.get(libro + suf)
        q_rif = p.get(riferimento + suf)
        if not q_lib or not q_rif:
            continue
        prob = giusto(q_rif)
        chiusura = p.get("PS_C" if mercato == "1X2" else "PS_OUC")
        prob_c = giusto(chiusura) if chiusura else None
        for k, quota in q_lib.items():
            vantaggio = quota * prob[k] - 1.0
            if vantaggio < soglia:
                continue
            if quota > QUOTA_MAX or vantaggio > VANTAGGIO_MAX:
                scartate += 1
                continue
            if mercato == "1X2":
                vinta = p["esito"] == k
            else:
                if p["gol"] is None:
                    continue
                vinta = (p["gol"] > 2.5) == (k == "O")
            out.append({
                "stagione": p["stagione"], "lega": p["lega"], "quota": quota,
                "vantaggio": vantaggio, "vinta": vinta,
                "ritorno": (quota - 1.0) if vinta else -1.0,
                "clv": (quota * prob_c[k] - 1.0) if prob_c else None,
            })
    return out, scartate


def media_ic(valori):
    """Media e fascia di incertezza al 95%."""
    n = len(valori)
    if n < 2:
        return (valori[0] if valori else float("nan")), None
    m = sum(valori) / n
    var = sum((x - m) ** 2 for x in valori) / (n - 1)
    e = 1.96 * math.sqrt(var / n)
    return m, (m - e, m + e)


def fmt_ic(m, ic, pct=True):
    if ic is None:
        return f"{m:+.1%}" if pct else f"{m:+.4f}"
    return f"{m:+.1%} [{ic[0]:+.1%}, {ic[1]:+.1%}]"


def riga_tabella(etichetta, g, n_stagioni):
    if not g:
        return f"  {etichetta:<26} nessuna giocata"
    rit, ic_r = media_ic([x["ritorno"] for x in g])
    clv_v = [x["clv"] for x in g if x["clv"] is not None]
    clv, ic_c = media_ic(clv_v) if clv_v else (float("nan"), None)
    vinte = sum(1 for x in g if x["vinta"]) / len(g)
    q = sum(x["quota"] for x in g) / len(g)
    per_st = len(g) / max(n_stagioni, 1)
    return (f"  {etichetta:<26} {len(g):>6} {per_st:>6.0f} {vinte:>6.0%} {q:>6.2f}  "
            f"{fmt_ic(rit, ic_r):<24} {fmt_ic(clv, ic_c) if clv_v else '–'}")


def intestazione():
    print(f"  {'':<26} {'giocate':>6} {'a st.':>6} {'vinte':>6} {'quota':>6}  "
          f"{'ritorno per euro':<24} CLV (contro la chiusura)")


# ---------------------------------------------------------------
#  controllo in diretta: cosa ci passa la nostra API
# ---------------------------------------------------------------

def controlla_api():
    chiave = os.environ.get("API_FOOTBALL_KEY", "").strip()
    if not chiave:
        print("  (chiave API non caricata: salto il controllo. Per farlo,")
        print("   prima: source ~/.previsioni_env)")
        return
    from datetime import date
    url = "https://v3.football.api-sports.io/odds?" + urllib.parse.urlencode(
        {"date": date.today().isoformat(), "page": 1})
    req = urllib.request.Request(url, headers={"x-apisports-key": chiave})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            dati = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"  errore: {e}")
        return
    nomi = {}
    for voce in dati.get("response", []) or []:
        for b in voce.get("bookmakers", []) or []:
            nomi[b.get("name", "?")] = nomi.get(b.get("name", "?"), 0) + 1
    if not nomi:
        print("  nessuna quota oggi nella prima pagina")
        return
    print("  Bookmaker presenti nelle quote che scarichiamo ogni mezz'ora:")
    for n, c in sorted(nomi.items(), key=lambda t: -t[1]):
        print(f"    {n:<24} in {c} partite")
    ha_pin = any("pinnacle" in n.lower() for n in nomi)
    print(f"\n  Pinnacle presente: {'SI' if ha_pin else 'NO'}")
    if ha_pin:
        print("  -> La versione in diretta si puo' costruire con i dati che")
        print("     scarichiamo gia', senza costi in piu'.")
    else:
        print("  -> Senza Pinnacle, in diretta si userebbe la media di tutti")
        print("     i bookmaker come riferimento: funziona, ma e' meno precisa.")


# ---------------------------------------------------------------

def main():
    solo_locale = len(sys.argv) > 1 and sys.argv[1] == "locale"
    print("=" * 96)
    print("0. DATI")
    print("=" * 96)
    scarica(solo_locale)
    partite = leggi()
    if not partite:
        print("  Nessun dato letto. Se il server non raggiunge football-data.co.uk")
        print("  il test non puo' partire.")
        return
    stagioni = sorted({p["stagione"] for p in partite})
    print(f"  partite lette: {len(partite)} in {len(stagioni)} stagioni")
    print(f"  con quote di Pinnacle: {sum(1 for p in partite if 'PS' in p)}")
    print(f"  con chiusura di Pinnacle: {sum(1 for p in partite if 'PS_C' in p)}")
    presenti = [l for l in LIBRI if sum(1 for p in partite if l in p) > 500]
    print("  bookmaker con dati sufficienti: " +
          ", ".join(LIBRI[l] for l in presenti))

    # ---- 1. Pinnacle e' davvero il piu' preciso? --------------------
    print("\n" + "=" * 96)
    print("1. IL RIFERIMENTO REGGE? QUANTO SBAGLIA OGNI BOOKMAKER")
    print("=" * 96)
    print("  Log loss sull'esito, piu' basso e' meglio. Se Pinnacle non fosse")
    print("  il piu' preciso, tutto il resto del test non avrebbe senso.\n")
    comuni = [p for p in partite if "PS" in p and "B365" in p]
    for pref, nome in (("PS", "Pinnacle"), ("PS_C", "Pinnacle alla chiusura"),
                       ("Avg", "media del mercato"), ("B365", "bet365"),
                       ("WH", "William Hill")):
        g = [p for p in comuni if pref in p]
        if len(g) < 500:
            continue
        ll = sum(-math.log(giusto(p[pref])[p["esito"]]) for p in g) / len(g)
        print(f"  {nome:<26} {ll:.4f}   su {len(g)} partite")

    n_st = len(stagioni)

    # ---- 2. la regola principale, 1X2 -----------------------------
    print("\n" + "=" * 96)
    print("2. ESITO FINALE: GIOCARE SOLO QUANDO IL BOOKMAKER PAGA PIU' DI PINNACLE")
    print("=" * 96)
    print("  La soglia e' il vantaggio minimo richiesto: 2% vuol dire giocare")
    print("  solo se la quota e' almeno il 2% sopra il prezzo giusto.")
    print("  Il CLV e' la prova che conta: se e' positivo con la fascia tutta")
    print("  sopra lo zero, il vantaggio e' vero, anche se il ritorno balla.")
    print("  (IT) = ha avuto un sito con licenza italiana. Le quote del test sono")
    print("  quelle internazionali: sul sito .it possono essere leggermente diverse.")
    print("  'MIGLIORE AL MONDO' e' la quota piu' alta fra tutti i bookmaker, anche")
    print("  quelli che in Italia non puoi usare: e' il tetto teorico, non realistico.\n")
    for soglia in SOGLIE:
        print(f"  --- soglia {soglia:.0%} ---")
        intestazione()
        for libro in presenti:
            g, _ = giocate(partite, libro, "PS", soglia)
            nome = LIBRI[libro] + (" (IT)" if libro in ITALIANI else "")
            print(riga_tabella(nome, g, n_st))
        print()

    # ---- 3. over/under ------------------------------------------
    print("=" * 96)
    print("3. OVER/UNDER 2.5: STESSA REGOLA")
    print("=" * 96)
    for soglia in (0.00, 0.02, 0.04):
        print(f"  --- soglia {soglia:.0%} ---")
        intestazione()
        for libro, nome in (("B365", "bet365 (IT)"), ("WH", "William Hill (IT)"),
                            ("1XB", "1xBet"), ("Max", "MIGLIORE AL MONDO")):
            g, _ = giocate(partite, libro, "PS", soglia, "OU")
            if g:
                print(riga_tabella(nome, g, n_st))
        print()

    # ---- 4. e' ancora vivo? --------------------------------------
    print("=" * 96)
    print("4. FUNZIONA ANCORA OGGI? STAGIONE PER STAGIONE")
    print("=" * 96)
    print("  bet365 contro Pinnacle, soglia 2%, esito finale e Over/Under insieme.\n")
    tutte = (giocate(partite, "B365", "PS", 0.02)[0] +
             giocate(partite, "B365", "PS", 0.02, "OU")[0])
    intestazione()
    for s in stagioni:
        g = [x for x in tutte if x["stagione"] == s]
        print(riga_tabella(f"20{s[:2]}/{s[2:]}", g, 1))

    # ---- 5. quote basse ------------------------------------------
    print("\n" + "=" * 96)
    print("5. PER FASCIA DI QUOTA: FUNZIONA ANCHE SULLE QUOTE BASSE?")
    print("=" * 96)
    print("  Stesse giocate della sezione 4.\n")
    intestazione()
    for lo, hi, et in ((1.0, 1.60, "sotto 1.60"), (1.60, 2.20, "1.60 - 2.20"),
                       (2.20, 3.50, "2.20 - 3.50"), (3.50, 99, "sopra 3.50")):
        g = [x for x in tutte if lo <= x["quota"] < hi]
        print(riga_tabella(et, g, n_st))

    # ---- 6. per campionato ---------------------------------------
    print("\n" + "=" * 96)
    print("6. PER CAMPIONATO (solo stagioni recenti)")
    print("=" * 96)
    intestazione()
    recenti = [x for x in tutte if x["stagione"] in RECENTI]
    n_rec = len([s for s in stagioni if s in RECENTI])
    for l, nome in LEGHE.items():
        g = [x for x in recenti if x["lega"] == l]
        if g:
            print(riga_tabella(nome, g, n_rec))

    # ---- 7. la versione originale, con la media -------------------
    print("\n" + "=" * 96)
    print("7. LA VERSIONE DELLO STUDIO ORIGINALE: MEDIA DEL MERCATO COME RIFERIMENTO")
    print("=" * 96)
    print("  Serve se in diretta non avessimo Pinnacle.\n")
    intestazione()
    for soglia in (0.02, 0.05):
        for libro in ("B365", "Max"):
            g, _ = giocate(partite, libro, "Avg", soglia)
            print(riga_tabella(f"{LIBRI[libro]} vs media, {soglia:.0%}", g, n_st))

    # ---- 8. le nostre API ----------------------------------------
    print("\n" + "=" * 96)
    print("8. SI PUO' FARE IN DIRETTA CON I DATI CHE GIA' SCARICHIAMO?")
    print("=" * 96)
    controlla_api()

    # ---- 9. verdetto ---------------------------------------------
    print("\n" + "=" * 96)
    print("9. VERDETTO")
    print("=" * 96)
    rec = [x for x in tutte if x["stagione"] in RECENTI]
    # Il CLV di una singola giocata varia poco, quindi bastano poche
    # giocate per leggerlo: il ritorno invece ne vorrebbe migliaia.
    if len(rec) < 10:
        print(f"  Solo {len(rec)} occasioni nelle stagioni recenti: bet365 non")
        print("  paga quasi mai sopra il prezzo giusto di Pinnacle. Anche se ogni")
        print("  tanto succede, e' troppo raro per guadagnarci qualcosa.")
        return
    rit, ic_r = media_ic([x["ritorno"] for x in rec])
    clv_v = [x["clv"] for x in rec if x["clv"] is not None]
    clv, ic_c = media_ic(clv_v) if clv_v else (float("nan"), None)
    per_st = len(rec) / max(n_rec, 1)
    print(f"  bet365 contro Pinnacle, soglia 2%, ultime {n_rec} stagioni:")
    print(f"    giocate: {len(rec)}, cioe' circa {per_st:.0f} a stagione")
    print(f"    ritorno: {fmt_ic(rit, ic_r)}")
    if clv_v:
        print(f"    CLV:     {fmt_ic(clv, ic_c)}")
    atteso = per_st * PUNTATA * (clv if clv_v else rit)
    print(f"    con {PUNTATA:.0f} euro a giocata, guadagno atteso: circa "
          f"{atteso:+.0f} euro a stagione")
    print()
    print("  Come leggerlo: il ritorno dipende da quante partite sono andate")
    print("  bene, e con queste quantita' balla moltissimo. Il CLV no: dice se")
    print("  le quote prese erano migliori del prezzo finale del mercato, e")
    print("  quello e' il vantaggio vero. Si giudica dal CLV, non dal saldo.")
    print()
    if ic_c and ic_c[0] > 0 and per_st < 20:
        print("  IL VANTAGGIO C'E', MA LE OCCASIONI SONO TROPPO RARE: meno di")
        print("  venti a stagione. Il guadagno atteso qui sopra e' quello vero,")
        print("  ed e' troppo piccolo per valere la fatica e il rischio di")
        print("  farsi limitare il conto.")
    elif ic_c and ic_c[0] > 0:
        print("  FUNZIONA. Le quote prese battono la chiusura del mercato piu'")
        print("  efficiente: e' un vantaggio vero, non fortuna. E' la prima")
        print("  strada con una prova positiva in tutto il nostro lavoro.")
        print("  Tre avvertenze prima di metterci soldi:")
        print("   - le quote di questo test sono quelle internazionali: su")
        print("     bet365.it possono essere un po' diverse;")
        print("   - chi vince con questo metodo viene limitato dai bookmaker,")
        print("     di solito in settimane o mesi;")
        print("   - il guadagno per stagione qui sopra e' l'ordine di grandezza")
        print("     vero: decidi tu se vale la fatica.")
    elif ic_c and ic_c[1] < 0:
        print("  NON FUNZIONA con bet365: le quote prese sono peggiori della")
        print("  chiusura. Guarda le altre righe della sezione 2: se nessun")
        print("  bookmaker italiano ha CLV positivo, questa strada e' chiusa.")
    else:
        print("  NON ANCORA DISTINGUIBILE: il CLV non e' ne' chiaramente sopra")
        print("  ne' chiaramente sotto lo zero. Guarda le sezioni 2 e 4 per")
        print("  capire se c'e' un bookmaker o una soglia che fa meglio.")
    print("=" * 96)


if __name__ == "__main__":
    main()
