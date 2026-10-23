"""
PREVISIONI PER LE PARTITE IN ARRIVO - versione con modello v4
=============================================================
Applica alle partite future il modello addestrato da addestra_modello.py:
Poisson con correzione Dixon-Coles, shrinkage, piu' i sei indicatori
di formazione e di squadra.

FORMAZIONI NON ANCORA USCITE
----------------------------
Al mattino le formazioni ufficiali non esistono. In quel caso gli
indicatori di formazione valgono zero, cioe' "come la media": non
spostano la previsione ne' in bene ne' in male.
Quando le formazioni escono, basta rilanciare questo script: i valori
veri sostituiscono gli zeri e le probabilita' si aggiornano.

USO:
    python previsioni_v2.py
"""

import os
import sys
import itertools
import json
import time
import math
import sqlite3
import urllib.request
import urllib.parse
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from nucleo import media_pesata, carica_conservazione, indicatori_squadra
from consumo_api import frena
from campionati_valore import CAMPIONATI_VALORE

DB_PATH = "calcio_dati.db"
MODELLO = "modello.json"
BASE_URL = "https://v3.football.api-sports.io"
API_KEY = os.environ.get("API_FOOTBALL_KEY", "").strip()

GIORNI_AVANTI = int(os.environ.get("GIORNI_AVANTI", "4"))
# scarica anche le quote per il confronto (circa 90 chiamate in piu')
CON_QUOTE = os.environ.get("CON_QUOTE", "1") != "0"
MAX_CHIAMATE_QUOTE = 200
# quote grezze dell'ultimo giro, per verifica.py, e quando e' stato
# scaricato ogni giorno: i giorni lontani si riscaricano ogni 3 ore
QUOTE_GIRO = "quote_giro.json"
# all'intervallo (v21.0): tutti i campionati veri, anche senza Pinnacle; il
# tipo di ogni campionato (League o Cup) si legge una volta al giorno
FILE_LEGHE = os.path.join("stato", "leghe_tipo.json")
ESCLUSE_INTERVALLO = re.compile(
    r"women|femenin|feminin|frauen|femminil|w-league|wk-league|nwsl|wsl|"
    r"\bu-?1[5-9]\b|\bu-?2[0-3]\b|youth|primavera|reserve|junior|academy|development|"
    r"revela|sub-?2|friendl", re.I)
ORE_GIORNI_LONTANI = 3
MIN_PARTITE = 3
MAX_GOL = 8

USCITA_JSON = "previsioni.json"
USCITA_HTML = "previsioni.html"
USCITA_SELEZIONE = "selezione.html"
SOGLIA_VANTAGGIO = 0.05     # vantaggio stimato minimo sulla quota
SOGLIA_AFFIDABILITA = 60    # dati sotto questa soglia non entrano


def chiamata(endpoint, params):
    url = f"{BASE_URL}/{endpoint}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"x-apisports-key": API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            dati = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"  [ERRORE] {endpoint}: {e}")
        return []
    time.sleep(0.3)
    if dati.get("errors"):
        print(f"  [ERRORE API] {dati['errors']}")
        return []
    return dati.get("response", [])


def poisson(k, lam):
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def matrice(lc, lf, rho):
    def tau(x, y):
        if x == 0 and y == 0: return 1.0 - lc * lf * rho
        if x == 0 and y == 1: return 1.0 + lc * rho
        if x == 1 and y == 0: return 1.0 + lf * rho
        if x == 1 and y == 1: return 1.0 - rho
        return 1.0
    M, tot = [], 0.0
    for x in range(MAX_GOL + 1):
        px = poisson(x, lc)
        riga = [px * poisson(y, lf) * max(0.01, tau(x, y)) for y in range(MAX_GOL + 1)]
        M.append(riga)
        tot += sum(riga)
    return [[v / tot for v in riga] for riga in M]


# ---------------------------------------------------------------
#  I MERCATI
#
#  Ogni mercato e' definito UNA VOLTA SOLA, in esito_avvenuto(), come
#  regola sul risultato finale. Da quella stessa regola nascono tre
#  cose:
#    - la probabilita', sommando le caselle della matrice che la
#      soddisfano;
#    - la verifica della schedina a partita giocata;
#    - i sistemi integrali.
#  Cosi' il calcolo e la verifica non possono andare in disaccordo:
#  sono la stessa riga di codice letta due volte.
#
#  Attenzione: le probabilita' di due mercati diversi NON si
#  moltiplicano mai fra loro sulla stessa partita. Gli eventi non sono
#  indipendenti (se finisce 3-0 si avverano insieme l'1, l'Over 2.5 e
#  il NoGol). Le combinazioni si ottengono sommando le caselle che
#  soddisfano tutte le condizioni insieme, ed e' esattamente quello
#  che fa il codice qui sotto.
# ---------------------------------------------------------------

GRUPPI_MERCATI = {
    "esito":     ["1", "X", "2", "1X", "12", "X2"],
    "gol":       ["over05", "under05", "over15", "under15",
                  "over25", "under25", "over35", "under35",
                  "over45", "under45", "pari", "dispari"],
    "multigol":  ["mg_0_1", "mg_0_2", "mg_0_3", "mg_1_2", "mg_1_3",
                  "mg_1_4", "mg_2_3", "mg_2_4", "mg_2_5",
                  "mg_3_4", "mg_3_5", "mg_3_6"],
    "entrambe":  ["gol_gol", "no_gol"],
    "squadra":   ["casa_segna", "casa_nonsegna",
                  "fuori_segna", "fuori_nonsegna",
                  "casa_over15", "casa_under15",
                  "casa_over25", "casa_under25",
                  "fuori_over15", "fuori_under15",
                  "fuori_over25", "fuori_under25",
                  "casa_mg_1_2", "casa_mg_1_3",
                  "fuori_mg_1_2", "fuori_mg_1_3"],
    "scarto":    ["casa_2plus", "casa_3plus", "casa_h1",
                  "fuori_2plus", "fuori_3plus", "fuori_h1"],
    "combo":     ["1+over25", "1+under25", "2+over25", "2+under25",
                  "1X+over25", "1X+under25", "X2+over25", "X2+under25",
                  "12+over25", "12+under25",
                  "X+over25", "X+under25",
                  "1+over15", "2+over15", "1X+over15", "X2+over15",
                  "1+over35", "2+over35",
                  "1+gol", "1+nogol", "2+gol", "2+nogol",
                  "X+gol", "1X+gol", "1X+nogol",
                  "X2+gol", "X2+nogol", "12+gol", "12+nogol",
                  "1+mg_1_3", "2+mg_1_3", "1X+mg_1_3", "X2+mg_1_3",
                  "1+mg_2_4", "2+mg_2_4"],
}

TUTTI_I_MERCATI = [k for gruppo in GRUPPI_MERCATI.values() for k in gruppo]

_MASCHERE = {}


def maschere(n):
    """
    Per ogni mercato, l'elenco delle caselle della matrice che lo fanno
    vincere. Dipende solo dalla dimensione della matrice, non dalla
    partita: si calcola una volta sola e vale per tutte.
    """
    if n not in _MASCHERE:
        _MASCHERE[n] = {
            k: [(x, y) for x in range(n) for y in range(n)
                if esito_avvenuto(k, x, y)]
            for k in TUTTI_I_MERCATI
        }
    return _MASCHERE[n]


def mercati(M):
    """Tutte le probabilita' derivate dalla matrice dei punteggi."""
    caselle = maschere(len(M))
    fuori = {k: sum(M[x][y] for x, y in caselle[k]) for k in TUTTI_I_MERCATI}
    punteggi = sorted(((M[x][y], f"{x}-{y}") for x in range(6) for y in range(6)),
                      reverse=True)[:5]
    fuori["punteggi_probabili"] = [{"risultato": r, "prob": round(p, 4)}
                                   for p, r in punteggi]
    return fuori


def quote_mercati(voce):
    """
    Probabilita' implicite in TUTTI i mercati che seguiamo, tolto il
    margine. La risposta dell'API contiene gia' tutti i mercati di ogni
    bookmaker: non costa chiamate in piu' leggerli.

    Ogni mercato viene normalizzato per conto suo, perche' ognuno ha il
    suo margine.
    """
    gruppi = {"1x2": [], "ou25": [], "gg": []}
    quote_grezze = {"1x2": [], "ou25": [], "gg": []}

    for book in voce.get("bookmakers", []) or []:
        trovati = {}
        for scommessa in book.get("bets", []) or []:
            nome = (scommessa.get("name") or "").lower()
            valori = {}
            for v in scommessa.get("values", []) or []:
                et = str(v.get("value", "")).strip().lower()
                try:
                    q = float(v.get("odd"))
                except (TypeError, ValueError):
                    continue
                if q <= 1.0:
                    continue
                valori[et] = q

            if nome in ("match winner", "1x2", "fulltime result"):
                c = {k: valori.get(a) for k, a in
                     (("1", "home"), ("X", "draw"), ("2", "away"))}
                if all(c.values()):
                    trovati["1x2"] = c
            elif nome in ("goals over/under", "over/under"):
                sopra = valori.get("over 2.5")
                sotto = valori.get("under 2.5")
                if sopra and sotto:
                    trovati["ou25"] = {"over25": sopra, "under25": sotto}
            elif nome in ("both teams score", "both teams to score"):
                si, no = valori.get("yes"), valori.get("no")
                if si and no:
                    trovati["gg"] = {"gol_gol": si, "no_gol": no}

        for chiave, c in trovati.items():
            grezze = {k: 1 / q for k, q in c.items()}
            s = sum(grezze.values())
            if 1.0 < s < 1.6:
                gruppi[chiave].append({k: v / s for k, v in grezze.items()})
                quote_grezze[chiave].append((c, s - 1))

    risultato = {}
    for chiave, lista in gruppi.items():
        if not lista:
            continue
        n = len(lista)
        for et in lista[0]:
            risultato[et] = sum(d[et] for d in lista) / n
            # quota media effettivamente disponibile, per il calcolo del valore
            risultato[f"quota_{et}"] = sum(
                q[0][et] for q in quote_grezze[chiave]) / n
        risultato[f"margine_{chiave}"] = sum(
            q[1] for q in quote_grezze[chiave]) / n
        risultato[f"book_{chiave}"] = n

    if "1" in risultato:
        risultato["margine"] = risultato.get("margine_1x2", 0)
        risultato["bookmaker"] = risultato.get("book_1x2", 0)
    return risultato or None


def quote_1x2(voce):
    """
    Probabilita' 1X2 medie tra i bookmaker, tolto il margine.
    Le probabilita' implicite in una quota sommano a piu' di 1: quella
    differenza e' il guadagno del bookmaker e va tolta, altrimenti il
    confronto sarebbe falsato.
    """
    raccolte = []
    for book in voce.get("bookmakers", []) or []:
        for scommessa in book.get("bets", []) or []:
            if (scommessa.get("name") or "").lower() not in (
                    "match winner", "1x2", "fulltime result"):
                continue
            v = {}
            for val in scommessa.get("values", []) or []:
                et = str(val.get("value", "")).strip().lower()
                try:
                    q = float(val.get("odd"))
                except (TypeError, ValueError):
                    continue
                if q <= 1.0:
                    continue
                if et in ("home", "1"): v["1"] = q
                elif et in ("draw", "x"): v["X"] = q
                elif et in ("away", "2"): v["2"] = q
            if len(v) == 3:
                grezze = [1 / v["1"], 1 / v["X"], 1 / v["2"]]
                s = sum(grezze)
                if 1.0 < s < 1.5:
                    raccolte.append(([g / s for g in grezze], s - 1))
            break
    if not raccolte:
        return None
    n = len(raccolte)
    return {"1": sum(r[0][0] for r in raccolte) / n,
            "X": sum(r[0][1] for r in raccolte) / n,
            "2": sum(r[0][2] for r in raccolte) / n,
            "margine": sum(r[1] for r in raccolte) / n,
            "bookmaker": n}


def giorni_da_scaricare(previsioni):
    """
    I giorni di cui scaricare le quote in questo giro.

    L'API da' le quote per giornata, di tutte le partite del mondo, a
    pagine da 10: ogni giorno costa decine di chiamate. Oggi e domani si
    scaricano a ogni giro; i giorni piu' lontani, dove le quote si
    muovono poco, al giro delle 7 e delle 8 e poi ogni 3 ore. Un giorno
    con una partita non ancora archiviata si scarica sempre, cosi'
    verifica.py la archivia con le quote del momento come prima.
    """
    adesso = datetime.now(timezone.utc)
    vicino = (adesso + timedelta(days=1)).date().isoformat()
    ora_it = datetime.now(ZoneInfo("Europe/Rome"))
    mattino = ora_it.hour in (7, 8) and ora_it.minute < 30
    ultimi = leggi_giro().get("ultimo_scarico", {})
    try:
        conn = sqlite3.connect(DB_PATH)
        archiviate = {r[0] for r in conn.execute(
            "SELECT fixture_id FROM archivio_previsioni")}
        conn.close()
    except sqlite3.OperationalError:
        archiviate = set()
    giorni = set()
    for p in previsioni:
        g = p["data"][:10]
        if mattino or g <= vicino or p["fixture_id"] not in archiviate:
            giorni.add(g)
            continue
        try:
            eta = adesso - datetime.fromisoformat(ultimi[g])
        except (KeyError, ValueError):
            eta = None
        if eta is None or eta >= timedelta(hours=ORE_GIORNI_LONTANI, minutes=-10):
            giorni.add(g)
    lontani = {p["data"][:10] for p in previsioni} - giorni
    if lontani:
        print(f"  giorni lontani con le quote di meno di {ORE_GIORNI_LONTANI} ore fa: "
              f"{', '.join(sorted(lontani))}")
    return giorni


def leggi_giro():
    try:
        with open(QUOTE_GIRO, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def tipi_leghe():
    """{id: [tipo, paese, nome]} di tutti i campionati, letto dall'API una
    volta al giorno (1 chiamata) e tenuto in FILE_LEGHE."""
    try:
        with open(FILE_LEGHE, encoding="utf-8") as f:
            d = json.load(f)
        if datetime.now(timezone.utc) - datetime.fromisoformat(d["letto"]) < timedelta(days=1):
            return {int(k): v for k, v in d["leghe"].items()}
    except (OSError, ValueError, KeyError, TypeError):
        d = None
    risposta = chiamata("leagues", {"current": "true"})
    if not risposta:
        return {int(k): v for k, v in (d or {}).get("leghe", {}).items()}
    leghe = {x["league"]["id"]: [x["league"].get("type"), (x.get("country") or {}).get("name"),
                                 x["league"].get("name")] for x in risposta}
    try:
        with open(FILE_LEGHE, "w", encoding="utf-8") as f:
            json.dump({"letto": datetime.now(timezone.utc).isoformat(), "leghe": leghe}, f, ensure_ascii=False)
    except OSError:
        pass
    return leghe


# competizioni che l'API chiama coppa ma che sono campionati: ammesse
# all'intervallo per scelta dell'utente, una per una (v22.5)
COPPE_AMMESSE = {1202}      # Australia - Australian Championship


def ammessa_intervallo(lid, tipi):
    """Un campionato vero (non coppa), non russo, non femminile, giovanile o di riserve."""
    t = tipi.get(lid)
    if not t or (t[0] != "League" and int(lid) not in COPPE_AMMESSE) or (t[1] or "") == "Russia":
        return False
    return not ESCLUSE_INTERVALLO.search(t[2] or "")


def gol_da_voce(voce):
    """
    (gol attesi casa, ospite, fonte) dalle quote prima della partita: da
    Pinnacle se c'e', se no dalla media dei bookmaker (almeno due), tolto
    il margine a ognuno. None se non bastano. Usa i calcoli di valore.py.
    """
    try:
        import valore as V
    except Exception:
        return None
    libri = V.leggi_quote(voce)
    if libri.get("Pinnacle"):
        gp = V.gol_pinnacle(libri["Pinnacle"])
        if gp:
            return gp["lc"], gp["lf"], "pinnacle"
    esito = {"home": [], "draw": [], "away": []}
    sopra = {}
    for l in libri.values():
        mw, ou = l.get("match winner") or {}, l.get("goals over/under") or {}
        if all(k in mw for k in esito):
            p = V.potenza({k: mw[k] for k in esito})
            for k in esito:
                esito[k].append(p[k])
        for linea in ("2.5", "3.5", "1.5"):
            if f"over {linea}" in ou and f"under {linea}" in ou:
                sopra.setdefault(linea, []).append(V.potenza({"o": ou[f"over {linea}"], "u": ou[f"under {linea}"]})["o"])
    if len(esito["home"]) < 2 or not sopra:
        return None
    linea = "2.5" if "2.5" in sopra else max(sopra, key=lambda k: len(sopra[k]))
    media = lambda v: sum(v) / len(v)
    po = media(sopra[linea])
    giuste = {"match winner": {k: 1 / media(v) for k, v in esito.items()},
              "goals over/under": {f"over {linea}": 1 / po, f"under {linea}": 1 / (1 - po)}}
    gp = V.gol_pinnacle(giuste)
    return (gp["lc"], gp["lf"], "media_bookmaker") if gp else None


def scrivi_giro(voci, completi, extra=(), intervallo=()):
    """
    Lascia a verifica.py le quote grezze appena scaricate, cosi' non le
    riscarica: e' lo stesso download, fatto pochi secondi prima.
    """
    adesso = datetime.now(timezone.utc)
    ultimi = leggi_giro().get("ultimo_scarico", {})
    for g in completi:
        ultimi[g] = adesso.isoformat()
    ieri = (adesso - timedelta(days=1)).date().isoformat()
    ultimi = {g: t for g, t in ultimi.items() if g >= ieri}
    try:
        with open(QUOTE_GIRO, "w", encoding="utf-8") as f:
            json.dump({"generato": adesso.isoformat(),
                       "giorni_scaricati": sorted(completi),
                       "ultimo_scarico": ultimi,
                       "voci": voci,
                       # le partite dei campionati solo per valore.py
                       "partite_extra": list(extra),
                       # tutte le altre partite, solo per l'intervallo (v21.0):
                       # gol attesi da Pinnacle o dalla media dei bookmaker
                       "partite_intervallo": list(intervallo)}, f, ensure_ascii=False)
    except OSError as e:
        print(f"  [quote del giro non salvate: {e}]")


def stima_senza_quote(info_stima, intervallo):
    """
    (v22.5) Le partite di oggi e domani dei campionati ammessi all'intervallo
    che non hanno quote nell'API: gol attesi stimati dallo storico della
    stagione (storico_intervallo.gol_attesi_stima), fonte "stima". Servono
    solo all'analisi dell'intervallo; valore.py le usa solo se non ha i gol
    attesi da Pinnacle.
    """
    try:
        from storico_intervallo import gol_attesi_stima
    except Exception:
        return 0
    nuove = 0
    for fid, info in info_stima.items():
        if fid in intervallo:
            continue
        try:
            g = gol_attesi_stima(info.get("lega"), info.get("id_casa"), info.get("id_fuori"), info["data"])
        except Exception:
            g = None
        if g:
            intervallo[fid] = dict(info, fixture_id=fid, lc=round(g[0], 4), lf=round(g[1], 4), fonte="stima")
            nuove += 1
    return nuove


def scarica_quote(giorni, id_ammessi, extra=(), info_intervallo=None, info_stima=None):
    """Quote per giornata: poche chiamate invece di una per partita."""
    quote = {}
    voci = {}
    completi = []
    chiamate = 0
    id_extra = {e["fixture_id"] for e in extra}
    info_intervallo = info_intervallo or {}
    intervallo = {}
    for giorno in sorted(giorni):
        pagina = 1
        while chiamate < MAX_CHIAMATE_QUOTE:
            url = f"{BASE_URL}/odds?" + urllib.parse.urlencode(
                {"date": giorno, "page": pagina})
            req = urllib.request.Request(url, headers={"x-apisports-key": API_KEY})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    dati = json.loads(r.read().decode("utf-8"))
            except Exception as e:
                print(f"    [ERRORE quote] {e}")
                break
            time.sleep(0.3)
            chiamate += 1
            risposta = dati.get("response", [])
            if not risposta:
                if not dati.get("errors"):
                    completi.append(giorno)
                break
            for voce in risposta:
                fid = (voce.get("fixture") or {}).get("id")
                if fid in id_extra:
                    voci[fid] = voce      # solo per valore.py
                if fid in info_intervallo and fid not in intervallo:
                    g = gol_da_voce(voce)
                    if g:
                        intervallo[fid] = dict(info_intervallo[fid], fixture_id=fid,
                                               lc=round(g[0], 4), lf=round(g[1], 4), fonte=g[2])
                if fid in id_ammessi:
                    voci[fid] = voce
                    est = quote_mercati(voce)
                    if est:
                        quote[fid] = est
            if pagina >= (dati.get("paging") or {}).get("total", 1):
                completi.append(giorno)
                break
            pagina += 1
    print(f"  quote trovate: {len(quote)} (chiamate {chiamate})")
    if info_stima:
        print(f"  partite senza quote con i gol attesi stimati dallo storico: {stima_senza_quote(info_stima, intervallo)}")
    if info_intervallo:
        fonti = {}
        for x in intervallo.values():
            fonti[x["fonte"]] = fonti.get(x["fonte"], 0) + 1
        print(f"  partite per l'intervallo con i gol attesi: {len(intervallo)} "
              f"({', '.join(f'{k} {n}' for k, n in fonti.items()) or 'nessuna'})")
    scrivi_giro(voci, completi, extra, intervallo.values())
    return quote


def ricorda_quote(fresche, id_ammessi):
    """
    Tiene memoria delle quote viste, e le riusa quando l'API smette di
    darcele.

    L'API toglie le quote di una partita quando si avvicina al calcio
    d'inizio: nel log si vede il conteggio che cala di sera in sera,
    13, 12, 11. Finora chi usciva dall'elenco restava senza confronto
    proprio nell'ora in cui uno lo guarda, e soprattutto non finiva
    nell'archivio che serve a verificare se battiamo il mercato.

    Le quote ricordate sono le ultime viste prima che sparissero,
    cioe' le piu' vicine al calcio d'inizio: sono le migliori che
    potessimo avere, non un ripiego.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS quote_note (
                fixture_id INTEGER PRIMARY KEY,
                aggiornato TEXT,
                dati TEXT)
        """)
        adesso = datetime.now(timezone.utc).isoformat()
        for fid, q in fresche.items():
            conn.execute("INSERT OR REPLACE INTO quote_note VALUES (?,?,?)",
                         (fid, adesso, json.dumps(q)))
        # le partite vecchie sono gia' state verificate: non servono piu'
        conn.execute("DELETE FROM quote_note WHERE aggiornato < ?",
                     ((datetime.now(timezone.utc) -
                       timedelta(days=30)).isoformat(),))
        conn.commit()

        mancanti = [f for f in id_ammessi if f not in fresche]
        recuperate = 0
        if mancanti:
            # a blocchi, per non costruire una query lunghissima
            for i in range(0, len(mancanti), 400):
                pezzo = mancanti[i:i + 400]
                segni = ",".join("?" * len(pezzo))
                for fid, dati in conn.execute(
                        f"SELECT fixture_id, dati FROM quote_note "
                        f"WHERE fixture_id IN ({segni})", pezzo):
                    try:
                        fresche[fid] = json.loads(dati)
                        recuperate += 1
                    except ValueError:
                        pass
        conn.close()
        if recuperate:
            print(f"  quote ricordate da prima: {recuperate}")
    except sqlite3.OperationalError as e:
        print(f"  [quote ricordate non disponibili: {e}]")
    return fresche


def nettezza(p1, px, p2):
    """
    Quanto il pronostico e' sbilanciato, da 0 a 100.
    Misura di quanto l'esito favorito supera il 33% del puro caso:
        33% -> 0     (tre esiti equivalenti, nessuna indicazione)
        50% -> 25
        76% -> 64
        90% -> 85
    NON dice quanto la previsione e' affidabile: dice solo quanto e'
    decisa. Una previsione nettissima puo' poggiare su dati pessimi,
    per questo esiste un indice separato.
    """
    massimo = max(p1, px, p2)
    return round(max(0.0, (massimo - 1 / 3) / (2 / 3)) * 100, 1)


def affidabilita(sc, sf, fc, ff):
    """
    Quanto ci si puo' fidare dei numeri, da 0 a 100.
    Pesa quanto storico hanno le due squadre e che tipo di formazioni
    stiamo usando. E' indipendente dalla nettezza.
    """
    n_min = min(sc["n"], sf["n"])
    punteggio_storia = min(1.0, n_min / 10.0)
    valore = {"ufficiale": 1.0, "probabile": 0.6}
    punteggio_form = sum(valore.get((f or {}).get("tipo"), 0.25)
                         for f in (fc, ff)) / 2
    totale = 100 * (0.6 * punteggio_storia + 0.4 * punteggio_form)
    etichetta = "alta" if totale >= 75 else ("media" if totale >= 50 else "bassa")
    return round(totale, 1), etichetta


def stato_squadre(conn, mod):
    """
    Indicatori di ogni squadra aggiornati a oggi.
    Il calcolo vero e' in nucleo.py, lo stesso usato dall'addestramento:
    cosi' le due parti non possono piu' andare fuori sincrono.
    """
    conservazione = carica_conservazione()
    cur = conn.cursor()
    cur.execute("""
        SELECT fixture_id, team_id, opponent_id, league_id, season, date, is_home,
               goals_for, goals_against, xg_finale, possession, fouls
        FROM team_match ORDER BY date
    """)
    righe = cur.fetchall()
    xg_partita = {(r[0], r[1]): r[9] for r in righe}

    storia, somme = {}, {}
    for fid, tid, opp, lid, sea, data, casa, gf, ga, xg, poss, falli in righe:
        storia.setdefault(tid, []).append({
            "league_id": lid, "season": sea, "date": data, "is_home": casa,
            "goals_for": gf, "goals_against": ga, "xg_finale": xg,
            "xg_against": xg_partita.get((fid, opp)),
            "possession": poss, "fouls": falli})
        # media per campionato E stagione: una partita di Serie B va
        # rapportata alla Serie B, non al campionato attuale della squadra
        s = somme.setdefault((lid, sea), {"gol": [0.0, 0], "xg": [0.0, 0]})
        if gf is not None:
            s["gol"][0] += gf; s["gol"][1] += 1
        if xg is not None:
            s["xg"][0] += xg; s["xg"][1] += 1

    def media_di(lega, stagione, quale):
        s = somme.get((lega, stagione))
        if not s:
            return None
        chiave = "gol" if quale == 0 else "xg"
        return s[chiave][0] / s[chiave][1] if s[chiave][1] >= 20 else None

    # indicatori di formazione per le partite in programma
    formazione = {}
    try:
        cur.execute("""
            SELECT fixture_id, team_id, tipo, produzione_out, difesa_out, portiere_nuovo
            FROM formazioni_previste
        """)
        for fid, tid, tipo, prod, dif, port in cur.fetchall():
            formazione[(fid, tid)] = {"tipo": tipo, "produzione_out": prod,
                                      "difesa_out": dif, "portiere_nuovo": port}
    except sqlite3.OperationalError:
        pass

    # stato dell'allenatore: preso dall'ultima partita registrata
    allenatore = {}
    try:
        cur.execute("""
            SELECT team_id, allenatore_nuovo, partite_allenatore
            FROM features WHERE partite_allenatore IS NOT NULL
            ORDER BY date
        """)
        for tid, nuovo, quante in cur.fetchall():
            # una partita in piu' e' passata da allora
            allenatore[tid] = {"allenatore_nuovo": 1 if (quante + 1) < 3 else 0,
                               "partite_allenatore": quante + 1}
    except sqlite3.OperationalError:
        pass

    w = mod["peso_xg"]
    stato = {}
    for tid, partite in storia.items():
        partite = list(reversed(partite))          # dalla piu' recente
        if len(partite) < MIN_PARTITE:
            continue
        lega_ora = partite[0]["league_id"]
        ind = indicatori_squadra(partite, lega_ora, media_di, conservazione)

        att_gol, att_xg = ind.get("att_gol"), ind.get("att_xg")
        dif_gol, dif_xg = ind.get("dif_gol"), ind.get("dif_xg")
        stato[tid] = {
            "n": len(partite),
            "att": w * (att_xg if att_xg else (att_gol or 1)) +
                   (1 - w) * (att_gol if att_gol else 1),
            "dif": w * (dif_xg if dif_xg else (dif_gol or 1)) +
                   (1 - w) * (dif_gol if dif_gol else 1),
            "possesso": ind.get("possesso"),
            "falli": ind.get("falli"),
            "att_gol_ctx": ind.get("att_gol_ctx", {}),
            "volatilita_gol": ind.get("volatilita_gol"),
            "volatilita_dif": ind.get("volatilita_dif"),
            "lega": lega_ora,
            **allenatore.get(tid, {}),
        }
    return stato, formazione


def leggi_rendimento():
    """
    Come sarebbero andate le segnalazioni passate. E' l'unico modo di
    sapere se questo criterio vale qualcosa: senza, la pagina sarebbe
    una promessa non verificata.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            SELECT s.esito, s.prob_nostra, s.quota, f.goals_home, f.goals_away
            FROM segnalazioni s
            JOIN fixtures f ON f.id = s.fixture_id
            WHERE f.goals_home IS NOT NULL AND f.status IN ('FT','AET','PEN')
        """)
        righe = cur.fetchall()
        conn.close()
    except sqlite3.OperationalError:
        return None

    if not righe:
        return None

    vinte = 0
    ritorno = 0.0
    for esito, prob, quota, gc, ga in righe:
        avvenuto = {
            "1": gc > ga, "X": gc == ga, "2": gc < ga,
            "over25": (gc + ga) >= 3, "under25": (gc + ga) < 3,
            "gol_gol": gc > 0 and ga > 0, "no_gol": not (gc > 0 and ga > 0),
        }.get(esito)
        if avvenuto is None:
            continue
        if avvenuto:
            vinte += 1
            ritorno += (quota or 0) - 1
        else:
            ritorno -= 1
    return len(righe), vinte, ritorno


def salva_segnalazioni(scelte, conn):
    """Registra cosa e' stato segnalato, per poterlo verificare dopo."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS segnalazioni (
            fixture_id INTEGER, esito TEXT,
            data TEXT, casa TEXT, fuori TEXT,
            prob_nostra REAL, prob_mercato REAL, quota REAL, vantaggio REAL,
            segnalato_il TEXT,
            PRIMARY KEY (fixture_id, esito))
    """)
    adesso = datetime.now(timezone.utc).isoformat()
    for v in scelte:
        conn.execute("""INSERT OR IGNORE INTO segnalazioni
                        VALUES (?,?,?,?,?,?,?,?,?,?)""",
                     (v["fixture_id"], v["esito"], v["data"], v["casa"],
                      v["fuori"], v["prob"], v["mercato"], v["quota"],
                      v["vantaggio"], adesso))
    conn.commit()


# etichetta leggibile per ogni esito
NOMI_ESITO = {
    "1": "Vittoria casa", "X": "Pareggio", "2": "Vittoria trasferta",
    "over25": "Over 2.5", "under25": "Under 2.5",
    "gol_gol": "Gol / Gol", "no_gol": "NoGol",
}


def scrivi_selezione(previsioni, generato):
    """
    Le partite dove il modello stima piu' probabilita' di quanta ne
    implichi la quota. Il vantaggio e' stimato CON LE NOSTRE probabilita':
    se sono sbagliate, il vantaggio non esiste.
    """
    scelte = []
    for p in previsioni:
        if p.get("affidabilita", 0) < SOGLIA_AFFIDABILITA:
            continue
        mk = p.get("mercato")
        if not mk:
            continue
        m = p["mercati"]
        for esito in NOMI_ESITO:
            if esito not in m or esito not in mk:
                continue
            quota = mk.get(f"quota_{esito}")
            if not quota:
                continue
            # valore atteso: probabilita' nostra per quota, meno la posta
            vantaggio = m[esito] * quota - 1
            if vantaggio >= SOGLIA_VANTAGGIO:
                scelte.append({
                    "fixture_id": p["fixture_id"], "esito": esito,
                    "data": p["data"], "casa": p["casa"], "fuori": p["fuori"],
                    "campionato": p["campionato"],
                    "prob": m[esito], "mercato": mk[esito], "quota": quota,
                    "vantaggio": vantaggio,
                    "formazioni": p.get("formazioni", "nessuna"),
                })
    scelte.sort(key=lambda x: -x["vantaggio"])

    try:
        conn = sqlite3.connect(DB_PATH)
        salva_segnalazioni(scelte, conn)
        conn.close()
    except sqlite3.OperationalError:
        pass

    # ---- riquadro del rendimento passato --------------------------
    rend = leggi_rendimento()
    if rend and rend[0] >= 20:
        n, vinte, ritorno = rend
        percentuale = ritorno / n * 100
        classe = "positivo" if ritorno > 0 else "negativo"
        riquadro = (
            f'<div class="grande {classe}">{percentuale:+.1f}%</div>'
            f'<div class="spiega">Rendimento delle {n} segnalazioni gia&#39; '
            f'concluse, a puntata costante: {vinte} vinte, {n-vinte} perse.<br>'
            f'Con cosi&#39; poche giocate questo numero oscilla molto: serve '
            f'qualche centinaio di casi prima che significhi qualcosa.</div>')
    elif rend:
        riquadro = (f'<div class="grande">{rend[0]} concluse</div>'
                    f'<div class="spiega">Troppo poche per dire se il criterio '
                    f'funziona. Il conto parte da qui e cresce ogni giorno.</div>')
    else:
        riquadro = ('<div class="grande">in attesa</div>'
                    '<div class="spiega">Nessuna segnalazione ancora conclusa. '
                    'Il rendimento comparira&#39; qui appena le prime partite '
                    'saranno giocate.</div>')

    # ---- le segnalazioni ------------------------------------------
    voci = []
    for v in scelte:
        marchio = ('<span class="uff">ufficiali</span>'
                   if v["formazioni"] == "ufficiale" else
                   '<span class="prob">stimate</span>')
        voci.append(
            f'<div class="scelta">'
            f'<div class="alto">'
            f'<span class="ora">{v["data"][8:10]}/{v["data"][5:7]} '
            f'{v["data"][11:16]}</span>'
            f'<span class="quota">{v["quota"]:.2f}</span></div>'
            f'<div class="partita">{v["casa"]} - {v["fuori"]}</div>'
            f'<div class="esito">{NOMI_ESITO[v["esito"]]}</div>'
            f'<div class="numeri">'
            f'<span>noi <b>{v["prob"]*100:.0f}%</b></span>'
            f'<span>mercato <b>{v["mercato"]*100:.0f}%</b></span>'
            f'<span class="vant">vantaggio stimato '
            f'<b>{v["vantaggio"]*100:+.0f}%</b></span></div>'
            f'<div class="sotto"><span class="lega">{v["campionato"]}</span>'
            f'{marchio}</div></div>')

    if not voci:
        voci = ['<div class="vuoto">Nessuna partita supera la soglia in questo '
                'momento. Significa che il modello e&#39; sostanzialmente '
                'd&#39;accordo con le quote: e&#39; la situazione normale.</div>']

    html = f"""<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Selezione</title>
<style>
 body {{ font-family:-apple-system,system-ui,sans-serif; margin:0; padding:12px;
        background:#f4f5f7; color:#1c2733; }}
 h1 {{ font-size:18px; margin:0 0 4px; }}
 .sottotitolo {{ font-size:11px; color:#5b6b7b; margin-bottom:12px; }}
 .riquadro {{ background:#fff; border-radius:6px; padding:12px; margin-bottom:14px; }}
 .tit {{ font-size:10px; text-transform:uppercase; letter-spacing:.5px;
         color:#7b8794; margin-bottom:6px; }}
 .grande {{ font-size:26px; font-weight:600; }}
 .grande.positivo {{ color:#1e7d3c; }}
 .grande.negativo {{ color:#b03030; }}
 .spiega {{ font-size:11px; color:#5b6b7b; margin-top:6px; line-height:1.6; }}
 .scelta {{ background:#fff; border-radius:6px; padding:11px 12px;
            margin-bottom:9px; }}
 .alto {{ display:flex; justify-content:space-between; align-items:center; }}
 .ora {{ font-size:11px; color:#7b8794; }}
 .quota {{ font-size:20px; font-weight:600; }}
 .partita {{ font-size:14px; font-weight:500; margin-top:3px; }}
 .esito {{ font-size:13px; color:#1e7d3c; font-weight:600; margin-top:2px; }}
 .numeri {{ display:flex; flex-wrap:wrap; gap:10px; margin-top:7px;
            font-size:11px; color:#5b6b7b; }}
 .vant {{ color:#8a6d1f; }}
 .sotto {{ display:flex; justify-content:space-between; align-items:center;
           margin-top:7px; }}
 .lega {{ font-size:10px; color:#97a3ae; }}
 .uff {{ background:#2c3e50; color:#fff; font-size:9px; padding:2px 6px;
         border-radius:3px; }}
 .prob {{ background:#aeb8c2; color:#fff; font-size:9px; padding:2px 6px;
          border-radius:3px; }}
 .vuoto {{ background:#fff; border-radius:6px; padding:18px; font-size:12px;
           color:#5b6b7b; line-height:1.6; }}
 .nota {{ margin-top:16px; font-size:10px; color:#7b8794; line-height:1.7; }}
 a {{ color:#2c3e50; }}
</style></head><body>
<h1>Selezione</h1>
<div class="sottotitolo">
Esiti dove stimiamo almeno {SOGLIA_VANTAGGIO:.0%} di vantaggio sulla quota
&middot; aggiornata il {generato[:16].replace('T', ' alle ')} UTC
</div>

<div class="riquadro">
 <div class="tit">Come sono andate finora</div>
 {riquadro}
</div>

{''.join(voci)}

<div class="nota">
<b>Cosa vuol dire "vantaggio stimato".</b> E' calcolato con le NOSTRE
probabilita': se sono sbagliate, il vantaggio non esiste. Non e' una
misura del mercato, e' una misura del nostro disaccordo col mercato.<br><br>
<b>Due cose che sappiamo dai test.</b> Primo: il confronto col mercato
finora dice "non distinguibile", quindi non abbiamo dimostrato di essere
migliori delle quote. Secondo: quando divergiamo molto dal mercato, nei
nostri test avevamo torto noi piu' spesso che ragione.<br><br>
Il riquadro in alto e' l'unica cosa che potra' dire se questo criterio
vale qualcosa, e servira' qualche mese di dati.<br><br>
<a href="index.html">Tutte le partite</a> &middot;
<a href="verifica.html">Verifica</a>
</div>
</body></html>"""
    with open(USCITA_SELEZIONE, "w", encoding="utf-8") as f:
        f.write(html)
    return len(scelte)




# ============================================================
#  PAGINA DELLE GIOCATE
# ============================================================

USCITA_GIOCATE = "giocate.html"

# LE GIOCATE BASATE SUL "VANTAGGIO STIMATO" SONO SPENTE.
# Singole consigliate, Valore e Selezione scelgono gli esiti dove la nostra
# probabilita' supera quella del mercato. La verifica dal vivo (settembre
# 2026, 536 partite) ha mostrato che il mercato e' piu' preciso di noi e
# che quelle giocate avrebbero reso -14,6% per puntata, perdita dimostrata:
# il vantaggio stimato misurava il nostro errore, non un'occasione.
# Si riaccendono solo se il modello dimostra di battere il mercato.
VALORE_ATTIVO = False
MIN_AFFIDABILITA_GIOCATE = 55
QUOTA_MINIMA_ALTA = 1.45     # sotto non vale la pena giocare
PROB_MINIMA_MISTA = 0.30     # sotto e' un esito campato per aria
QUOTE_MISTE = (5.0, 10.0, 17.0)
MISTE_ATTIVE = False
MAX_EVENTI_MISTA = 5         # da due a cinque partite, non di piu'
# Tetto alla quota di ogni singolo esito di una mista: 2.20 vuol dire
# almeno il 42% di probabilita'. La regola sui componenti controlla i
# pezzi uno per uno, questa controlla il loro prodotto: due condizioni
# al 60% fanno una combo al 36%, cioe' una scelta rischiosa presa solo
# perche' paga.
QUOTA_MASSIMA_MISTA = 2.20
# Limiti sul calcolo del vantaggio. Su un esito al 5% il vantaggio
# stimato e' quasi tutto rumore: basta un errore di due punti nella
# nostra probabilita' per farlo schizzare. E un vantaggio oltre il 50%
# non e' un'occasione: e' il segnale che stiamo dicendo qualcosa di
# molto diverso dal mercato, e nei nostri test in quei casi sbagliavamo
# noi piu' spesso che il mercato.
PROB_MINIMA_VALORE = 0.20
VANTAGGIO_MASSIMO = 0.50
RHO_SISTEMI = -0.05          # sostituito dal valore del modello a runtime

# Nei sistemi ogni esito deve valere almeno questa quota: un 1X al 93%
# vale 1.05, e la combinazione piu' bassa del sistema finirebbe sotto
# 1.10, cioe' una giocata che non vale la pena fare.
QUOTA_MINIMA_ESITO = 1.10
# v21.1: in un sistema nessuna partita puo' superare il 93% (prima c'erano
# sistemi al 100%, con esiti che coprivano ogni risultato)
UNIONE_MASSIMA = 0.93
# v21.1: in Alta probabilita' solo eventi con la quota vera di Bet365
SOLO_QUOTE_VERE = True

# Nelle miste non si gioca contro un favorito chiaro: se una squadra e'
# data sopra questa soglia, l'esito scelto per quella partita deve
# comprenderla. Giocare "X2" dove il padrone di casa e' al 69% significa
# scommettere sul 31% solo perche' paga di piu'.
SOGLIA_FAVORITO = 0.55

# Ogni pezzo di una combo deve reggersi da solo: se giochiamo
# "1X + Under 2.5", il modello deve ritenere probabile ANCHE che i gol
# siano pochi, non solo che la squadra di casa non perda. Altrimenti si
# sceglie una condizione qualsiasi perche' alza la quota.
SOGLIA_COMPONENTE = 0.55
SOGLIA_PRUDENTE = 0.70     # sotto questa, un esito non e' piu' "prudente"
# Quante gambe dello stesso mercato possono stare in una schedina.
# Ripetere quattro volte lo stesso tipo di giocata non e' solo brutto
# da vedere: se su quel mercato il modello sbaglia, sbaglia su tutte
# e quattro insieme.
MAX_STESSO_MERCATO = 2
PEZZI = {"gol": "gol_gol", "nogol": "no_gol"}
ESITI_1X2 = {"1": {"1"}, "X": {"X"}, "2": {"2"},
             "1X": {"1", "X"}, "X2": {"X", "2"}, "12": {"1", "2"}}
FUSO_GIOCATE = ZoneInfo("Europe/Rome")
GIORNI_GIOCATE = 2

NOMI = {
    # esito finale
    "1": "1", "X": "X", "2": "2",
    "1X": "1X", "12": "12", "X2": "X2",
    # totale gol
    "over05": "Over 0.5", "under05": "Under 0.5",
    "over15": "Over 1.5", "under15": "Under 1.5",
    "over25": "Over 2.5", "under25": "Under 2.5",
    "over35": "Over 3.5", "under35": "Under 3.5",
    "over45": "Over 4.5", "under45": "Under 4.5",
    "pari": "Pari", "dispari": "Dispari",
    # multigol
    "mg_0_1": "Multigol 0-1", "mg_0_2": "Multigol 0-2",
    "mg_0_3": "Multigol 0-3", "mg_1_2": "Multigol 1-2",
    "mg_1_3": "Multigol 1-3", "mg_1_4": "Multigol 1-4",
    "mg_2_3": "Multigol 2-3", "mg_2_4": "Multigol 2-4",
    "mg_2_5": "Multigol 2-5", "mg_3_4": "Multigol 3-4",
    "mg_3_5": "Multigol 3-5", "mg_3_6": "Multigol 3-6",
    # entrambe a segno
    "gol_gol": "Gol", "no_gol": "NoGol",
    # gol della singola squadra
    "casa_segna": "Casa segna", "casa_nonsegna": "Casa non segna",
    "fuori_segna": "Ospite segna", "fuori_nonsegna": "Ospite non segna",
    "casa_over15": "Casa Over 1.5", "casa_under15": "Casa Under 1.5",
    "casa_over25": "Casa Over 2.5", "casa_under25": "Casa Under 2.5",
    "fuori_over15": "Ospite Over 1.5", "fuori_under15": "Ospite Under 1.5",
    "fuori_over25": "Ospite Over 2.5", "fuori_under25": "Ospite Under 2.5",
    "casa_mg_1_2": "Casa Multigol 1-2", "casa_mg_1_3": "Casa Multigol 1-3",
    "fuori_mg_1_2": "Ospite Multigol 1-2", "fuori_mg_1_3": "Ospite Multigol 1-3",
    # scarto / handicap
    "casa_2plus": "Casa vince di 2+", "casa_3plus": "Casa vince di 3+",
    "fuori_2plus": "Ospite vince di 2+", "fuori_3plus": "Ospite vince di 3+",
    "casa_h1": "Casa handicap +1", "fuori_h1": "Ospite handicap +1",
    # combinazioni
    "1+over25": "1 + Over 2.5", "1+under25": "1 + Under 2.5",
    "2+over25": "2 + Over 2.5", "2+under25": "2 + Under 2.5",
    "1X+over25": "1X + Over 2.5", "1X+under25": "1X + Under 2.5",
    "X2+over25": "X2 + Over 2.5", "X2+under25": "X2 + Under 2.5",
    "12+over25": "12 + Over 2.5", "12+under25": "12 + Under 2.5",
    "X+over25": "X + Over 2.5", "X+under25": "X + Under 2.5",
    "1+over15": "1 + Over 1.5", "2+over15": "2 + Over 1.5",
    "1X+over15": "1X + Over 1.5", "X2+over15": "X2 + Over 1.5",
    "1+over35": "1 + Over 3.5", "2+over35": "2 + Over 3.5",
    "1+gol": "1 + Gol", "1+nogol": "1 + NoGol",
    "2+gol": "2 + Gol", "2+nogol": "2 + NoGol",
    "X+gol": "X + Gol", "1X+gol": "1X + Gol", "1X+nogol": "1X + NoGol",
    "X2+gol": "X2 + Gol", "X2+nogol": "X2 + NoGol",
    "12+gol": "12 + Gol", "12+nogol": "12 + NoGol",
    "1+mg_1_3": "1 + Multigol 1-3", "2+mg_1_3": "2 + Multigol 1-3",
    "1X+mg_1_3": "1X + Multigol 1-3", "X2+mg_1_3": "X2 + Multigol 1-3",
    "1+mg_2_4": "1 + Multigol 2-4", "2+mg_2_4": "2 + Multigol 2-4",
}

# Da quali mercati il motore pesca per costruire le proposte.
# Non e' l'elenco di quello che l'app mostra: l'app mostra tutto.
# Qui c'e' solo cio' che ha senso proporre in una schedina.
SEMPLICI = ["1", "X", "2", "over25", "under25", "gol_gol", "no_gol"]
COMBO = ["1+over25", "1+under25", "2+over25", "2+under25",
         "1X+over25", "1X+under25", "X2+over25", "X2+under25",
         "12+over25", "12+under25", "1+gol", "1+nogol",
         "2+gol", "2+nogol", "1X+gol", "1X+nogol", "X2+gol", "X2+nogol",
         "1+mg_1_3", "2+mg_1_3", "1X+mg_1_3", "X2+mg_1_3"]
SICURI = ["1X", "12", "X2", "over15", "under35",
          "mg_1_4", "casa_h1", "fuori_h1"]
# Mercati a quota piu' alta ma ancora leggibili dal modello: servono
# alle miste, dove da soli 1X e Under non arrivano mai alla quota
# richiesta.
#
# Restano fuori di proposito:
#  - Pari/Dispari: e' quasi sempre una moneta, il modello non ha nulla
#    da dire e finirebbe in schedina solo per la quota;
#  - "vince di 2+" e simili: non superano mai la soglia di componente,
#    starebbero nell'elenco senza poterci mai entrare.
# Nell'app si vedono lo stesso: sono giocabili, ma non li proponiamo.
ALTA_QUOTA = ["mg_1_2", "mg_1_3", "mg_2_3", "mg_2_4",
              "casa_over15", "fuori_over15",
              "casa_mg_1_2", "casa_mg_1_3",
              "fuori_mg_1_2", "fuori_mg_1_3",
              "casa_segna", "fuori_segna",
              "casa_under25", "fuori_under25"]


def prob_unione(M, esiti):
    """
    Probabilita' che ALMENO UNO degli esiti si verifichi nella stessa
    partita. In un sistema integrale e' questo che conta: basta che una
    delle scelte fatte su quella partita risulti giusta.

    Si calcola sommando le celle della matrice dove almeno un esito e'
    soddisfatto. Sommare le probabilita' separate sarebbe sbagliato,
    perche' gli esiti si sovrappongono: se finisce 2-0 si avverano
    insieme il risultato esatto, l'1 e l'1X+Over.
    """
    n = len(M)
    totale = 0.0
    for x in range(n):
        for y in range(n):
            for e in esiti:
                if esito_avvenuto(e, x, y):
                    totale += M[x][y]
                    break
    return totale


def _quota_equa(p):
    return 1.0 / max(p, 0.001)


def _voce(p, esito, prob, quota=None, mercato=None):
    return {"fixture_id": p["fixture_id"], "data": p["data"],
            "casa": p["casa"], "fuori": p["fuori"],
            "campionato": p["campionato"], "esito": esito,
            "nome": NOMI.get(esito, esito), "prob": prob,
            "quota": quota, "mercato": mercato,
            "formazioni": p.get("formazioni", "nessuna")}


def _sostenuto(p, esito):
    """Ogni componente dell'esito e' ritenuta probabile dal modello."""
    m = p["mercati"]
    for pezzo in esito.split("+"):
        if m.get(PEZZI.get(pezzo, pezzo), 0) < SOGLIA_COMPONENTE:
            return False
    return True


def _col_favorito(p, esito):
    """L'esito e' compatibile con il favorito della partita?"""
    m = p["mercati"]
    favorito = max(("1", "X", "2"), key=lambda k: m.get(k, 0))
    if m.get(favorito, 0) < SOGLIA_FAVORITO:
        return True                      # partita aperta: vale tutto
    insieme = ESITI_1X2.get(esito.split("+")[0])
    return insieme is None or favorito in insieme


def _raccogli(previsioni, chiavi, prob_min=0.0):
    """Tutti gli esiti disponibili di un certo tipo, ordinabili."""
    fuori = []
    for p in previsioni:
        if p.get("affidabilita", 0) < MIN_AFFIDABILITA_GIOCATE:
            continue
        m = p["mercati"]
        mk = p.get("mercato") or {}
        for k in chiavi:
            if k not in m or m[k] < prob_min:
                continue
            fuori.append(_voce(p, k, m[k],
                               mk.get(f"quota_{k}"), mk.get(k)))
    return fuori


def _schedina(voci, titolo, nota):
    """
    Probabilita' complessiva: gli esiti di partite DIVERSE si
    moltiplicano. Il calcolo e' valido solo perche' non mettiamo mai
    due esiti della stessa partita separati.
    """
    prob = 1.0
    quota = 1.0
    for v in voci:
        prob *= v["prob"]
        quota *= v["quota"] if v["quota"] else _quota_equa(v["prob"])
    # eventi senza la quota di Bet365: lì la quota e' la nostra stima
    return {"titolo": titolo, "nota": nota, "voci": voci,
            "prob": prob, "quota": quota, "stimate": sum(1 for v in voci if not v["quota"])}


def _sistema(gruppi, titolo, nota):
    """
    Su una partita si possono mettere piu' esiti: se si escludono fra
    loro le probabilita' si SOMMANO. Qui invece usiamo combo, che sono
    gia' un singolo esito congiunto.
    """
    prob = 1.0
    quota = 1.0
    voci = []
    for gruppo in gruppi:
        p_gruppo = sum(v["prob"] for v in gruppo)
        prob *= min(1.0, p_gruppo)
        quota *= _quota_equa(min(1.0, p_gruppo))
        voci.extend(gruppo)
    return {"titolo": titolo, "nota": nota, "voci": voci,
            "prob": prob, "quota": quota, "sistema": True}


def _finestra_giorni(previsioni):
    """
    Solo le partite dei primi due giorni in programma. Una schedina
    spalmata su cinque giorni resta aperta troppo a lungo: si segue
    peggio, e un evento perso il primo giorno la chiude subito.
    """
    per_giorno = {}
    for p in previsioni:
        try:
            g = datetime.fromisoformat(p["data"]).astimezone(FUSO_GIOCATE).date()
        except (KeyError, TypeError, ValueError):
            continue
        per_giorno.setdefault(g, []).append(p)
    if not per_giorno:
        return previsioni
    primo = min(per_giorno)
    return [x for g in (primo, primo + timedelta(days=1))
            for x in per_giorno.get(g, [])]


def _solo_primo_giorno(previsioni):
    """Solo le partite del primo giorno in programma, cioe' oggi."""
    per_giorno = {}
    for p in previsioni:
        try:
            g = datetime.fromisoformat(p["data"]).astimezone(FUSO_GIOCATE).date()
        except (KeyError, TypeError, ValueError):
            continue
        per_giorno.setdefault(g, []).append(p)
    return per_giorno[min(per_giorno)] if per_giorno else []


def _alta_probabilita(previsioni, quante, etichetta=None, gia_fatte=(),
                      oltre_oggi=None):
    """
    Schedine di esiti molto probabili, combinati fino a superare una
    quota che renda la vincita sensata.

    Una doppia chance al 93% da' quota 1.07: vincerla non cambia
    niente. Percio' si usano anche esiti un po' meno scontati, cosi'
    bastano due o tre eventi invece di otto, e si scarta tutto quello
    che sta oltre il 90%.
    """
    sicuri = [v for v in _raccogli(previsioni, SEMPLICI + SICURI,
                                   prob_min=0.60)
              if v["prob"] <= 0.90 and (v["quota"] or not SOLO_QUOTE_VERE)]
    sicuri.sort(key=lambda v: -v["prob"])

    # Per ogni partita si tengono piu' esiti possibili, non solo il piu'
    # probabile. Prendendo sempre e solo il massimo si finiva col
    # proporre quattro volte lo stesso mercato: l'handicap +1 della
    # casa sta quasi sempre fra il 75 e il 90%, quindi vinceva su
    # ogni partita e le schedine uscivano identiche.
    per_partita = {}
    for v in sicuri:
        per_partita.setdefault(v["fixture_id"], []).append(v)
    ordine = sorted(per_partita.values(), key=lambda lista: -lista[0]["prob"])

    fuori = []
    for partenza in range(len(ordine)):
        if len(fuori) >= quante:
            break
        voci, quota, quanti = [], 1.0, {}
        for lista in ordine[partenza:]:
            # una sola giocata per partita: gli esiti della stessa gara
            # non sono indipendenti e le probabilita' non si potrebbero
            # moltiplicare. Fra quelli disponibili si prende il piu'
            # probabile fra i mercati non ancora saturi.
            v = next((x for x in lista
                      if quanti.get(x["esito"], 0) < MAX_STESSO_MERCATO), None)
            if v is None:
                continue
            voci.append(v)
            quanti[v["esito"]] = quanti.get(v["esito"], 0) + 1
            quota *= v["quota"] or _quota_equa(v["prob"])
            if quota >= QUOTA_MINIMA_ALTA and len(voci) >= 2:
                break
            if len(voci) >= 5:
                break
        if not (quota >= QUOTA_MINIMA_ALTA and 2 <= len(voci) <= 5):
            continue
        # Il secondo gruppo ha senso solo se arriva davvero oltre oggi.
        # Di domenica le partite migliori sono quasi tutte di giornata,
        # quindi senza questo controllo i due gruppi verrebbero uguali.
        if oltre_oggi is not None and not any(
                v["fixture_id"] not in oltre_oggi for v in voci):
            continue
        # Dentro lo stesso gruppo, schedine che condividono la maggior
        # parte delle partite sono una schedina sola mostrata piu'
        # volte: se ne accetta una nuova solo se almeno meta' delle
        # partite sono diverse.
        nuove = {x["fixture_id"] for x in voci}
        if any(len(nuove & {x["fixture_id"] for x in s["voci"]}) > len(nuove) / 2
               for s in fuori):
            continue
        # Con le schedine dell'altro gruppo invece si rifiuta solo la
        # copia esatta: quelle di oggi e quelle sui due giorni si
        # chiudono in momenti diversi, quindi possono benissimo
        # contenere la stessa partita. Altrimenti al gruppo di oggi
        # resterebbero sempre e solo gli scarti dell'altro.
        if any(nuove == {x["fixture_id"] for x in s["voci"]} for s in gia_fatte):
            continue
        titolo = f"Alta probabilita' - {len(voci)} eventi"
        nota = ("Esiti molto probabili combinati fino a superare quota "
                f"{QUOTA_MINIMA_ALTA:.2f}, la soglia sotto la quale vincere "
                "non cambierebbe nulla.")
        if etichetta:
            titolo += f" - {etichetta}"
            nota += " Tutte le partite si giocano oggi: la schedina si chiude in giornata."
        fuori.append(_schedina(voci, titolo, nota))
    return fuori


FILE_GIOCATE = "giocate_giorno.json"
PROB_MINIMA_ESATTO = 0.14   # v21.3: sotto, il risultato esatto non si propone
ORA_PROPOSTE = 7          # a che ora del mattino nascono le proposte


def giornata_giocate(adesso=None):
    """
    Qual e' la "giornata" delle proposte in questo momento.

    Non coincide con la giornata del calendario: comincia alle sette
    del mattino. Prima di quell'ora si vedono ancora le proposte del
    giorno prima, e le nuove nascono al primo ciclo dopo le sette,
    quando sono uscite le prime formazioni probabili e si sanno gli
    infortuni e i rinvii dell'ultimo momento. Costruirle a mezzanotte
    e un minuto vorrebbe dire farle con la minima informazione
    disponibile su quella giornata.
    """
    adesso = adesso or datetime.now(FUSO_GIOCATE)
    return (adesso - timedelta(hours=ORA_PROPOSTE)).date()


def _calcola_esatti(previsioni, quanti=5):
    """I punteggi piu' probabili, nella stessa finestra delle schedine."""
    tutti = []
    for p in _finestra_giorni(previsioni):
        punteggi = p["mercati"].get("punteggi_probabili") or []
        # v21.3: solo i punteggi abbastanza probabili
        if not punteggi or punteggi[0]["prob"] < PROB_MINIMA_ESATTO:
            continue
        q = (p.get("quote_esatti") or {}).get(punteggi[0]["risultato"])
        secondo = punteggi[1]["prob"] if len(punteggi) > 1 else 0.0
        tutti.append({
            "fixture_id": p["fixture_id"],
            "risultato": punteggi[0]["risultato"],
            "prob": punteggi[0]["prob"],
            "distacco": punteggi[0]["prob"] - secondo,
            "alternativi": punteggi[1:3],
            # la quota di Bet365 per quel punteggio e quanto paga rispetto al giusto
            "quota": q, "vantaggio": round(punteggi[0]["prob"] * q - 1, 4) if q else None,
            # solo i campi che servono a mostrarli: cosi' restano validi
            # anche quando la partita e' gia' stata giocata
            "p": {k: p.get(k) for k in ("casa", "fuori", "campionato", "data",
                                        "formazioni", "gol_attesi_casa",
                                        "gol_attesi_fuori")},
        })
    tutti.sort(key=lambda d: -d["prob"])
    return tutti[:quanti], len(tutti)


# --- giocate dalle probabilita' di Pinnacle (v21.1) -------------------
# Le schedine di Giocate usavano il nostro modello, meno preciso del
# mercato, e una quota stimata (1 / probabilita') quasi ovunque: la quota
# vera dei bookmaker c'era solo per l'1X2. Ora le probabilita' vengono
# dalle quote di Pinnacle (gol attesi, poi tutti i mercati come in
# quote_giuste.py) e la quota di ogni evento e' quella di Bet365 dove
# l'API la passa; dove no resta stimata, e l'app lo dice.
LIBRO_GIOCATE = "Bet365"
SU_LIBRO = {"1": ("match winner", "home"), "X": ("match winner", "draw"), "2": ("match winner", "away"),
            "1X": ("double chance", "home/draw"), "12": ("double chance", "home/away"),
            "X2": ("double chance", "draw/away"),
            "gol_gol": ("both teams score", "yes"), "no_gol": ("both teams score", "no"),
            "casa_segna": ("total - home", "over 0.5"), "fuori_segna": ("total - away", "over 0.5")}
for _l in ("1.5", "2.5", "3.5"):
    _k = _l.replace(".", "")
    SU_LIBRO[f"over{_k}"] = ("goals over/under", f"over {_l}")
    SU_LIBRO[f"under{_k}"] = ("goals over/under", f"under {_l}")
    for _chi, _m in (("casa", "total - home"), ("fuori", "total - away")):
        SU_LIBRO[f"{_chi}_over{_k}"] = (_m, f"over {_l}")
        SU_LIBRO[f"{_chi}_under{_k}"] = (_m, f"under {_l}")


def con_pinnacle(previsioni):
    """
    Le stesse previsioni, ma con probabilita' e gol attesi ricavati da
    Pinnacle e le quote di Bet365 per evento. Solo le partite dove
    Pinnacle c'e'; le quote vengono da QUOTE_GIRO, appena scaricato.
    """
    try:
        import valore as V
        Q = V.Q
    except Exception:
        return []
    if Q is None:
        return []
    cal = Q.carica_calibrazione()
    voci = leggi_giro().get("voci", {})
    fuori = []
    for p in previsioni:
        voce = voci.get(str(p["fixture_id"]))
        libri = V.leggi_quote(voce) if voce else {}
        gp = V.gol_pinnacle(libri["Pinnacle"]) if libri.get("Pinnacle") else None
        if not gp:
            continue
        m = mercati(Q.matrice(gp["lc"], gp["lf"]))
        grezze, _ = Q.tutte(*gp["p"], gp["po"], gol=(gp["lc"], gp["lf"]), linea=gp["linea"],
                            quota_pt=(cal or {}).get("quota_primo_tempo"))
        m.update({k: v for k, v in Q.correggi(grezze, cal).items() if k in TUTTI_I_MERCATI})
        libro = libri.get(LIBRO_GIOCATE) or {}
        quote = {}
        for k, (mercato, esito) in SU_LIBRO.items():
            q = (libro.get(mercato) or {}).get(esito)
            if q:
                quote[f"quota_{k}"] = q
        # le quote di Bet365 sui risultati esatti ("1:0" -> "1-0"), v21.3
        esatti = {}
        for b in (voce.get("bookmakers") or []):
            if b.get("name") != LIBRO_GIOCATE:
                continue
            for bet in b.get("bets") or []:
                if bet.get("name") in ("Exact Score", "Correct Score"):
                    for x in bet.get("values") or []:
                        try:
                            esatti[str(x.get("value")).replace(":", "-")] = float(x.get("odd"))
                        except (TypeError, ValueError):
                            pass
        fuori.append(dict(p, mercati=m, mercato=quote, affidabilita=100, quote_esatti=esatti,
                          gol_attesi_casa=gp["lc"], gol_attesi_fuori=gp["lf"]))
    return fuori


# --- schedine ragionate (v22.6) -------------------------------------
# Due schedine al giorno, a quota mirata, costruite come le farebbe una
# persona che guarda l'analisi: una sola giocata per partita, coerente con
# quello che dicono le quote di Pinnacle (chi e' favorito vince o segna,
# le partite chiuse vanno sui pochi gol, quelle aperte sui gol), mai contro
# un favorito chiaro, e fra le giocate sensate quella che Bet365 paga piu'
# vicino al prezzo giusto (il margine pesa meno). Solo partite con la
# quota vera di Bet365 e non ancora iniziate. Prendono il posto delle
# vecchie "miste" (spente dalla v21.1), richieste di nuovo dall'utente
# con questo criterio.
RAGIONATE = (
    # titolo, quota minima, quota massima, probabilita' minima e massima di ogni giocata, giocate al massimo
    ("Quota 5-10", 5.0, 10.0, 0.60, 0.88, 7),
    ("Quota oltre 20", 20.0, 40.0, 0.47, 0.80, 8),
)
MARGINE_MASSIMO_RAGIONATE = 0.05   # Bet365 al massimo il 5% sotto il prezzo giusto
MERCATI_RAGIONATE = ["1", "2", "1X", "X2", "12", "over15", "over25", "under25", "under35",
                     "gol_gol", "no_gol", "casa_segna", "fuori_segna",
                     "casa_over15", "fuori_over15", "casa_under15", "fuori_under15",
                     "casa_under25", "fuori_under25"]


def motivo_giocata(p, esito):
    """Il perche' di una giocata, in poche parole, dai gol attesi di Pinnacle."""
    lc, lf = p.get("gol_attesi_casa"), p.get("gol_attesi_fuori")
    if not lc or not lf:
        return ""
    attesi = f"gol attesi {lc:.1f}-{lf:.1f}".replace(".", ",")
    tot = lc + lf
    if lc / lf >= 1.8:
        quadro = f"{p['casa']} nettamente favorito"
    elif lf / lc >= 1.8:
        quadro = f"{p['fuori']} nettamente favorito"
    elif lc / lf >= 1.25:
        quadro = f"{p['casa']} favorito"
    elif lf / lc >= 1.25:
        quadro = f"{p['fuori']} favorito"
    else:
        quadro = "partita equilibrata"
    if esito.startswith(("over", "gol_gol")) or esito.endswith(("over15",)):
        ritmo = ", partita da gol" if tot >= 2.7 else ""
    elif esito.startswith(("under", "no_gol")) or "under" in esito:
        ritmo = ", partita da pochi gol" if tot <= 2.6 else ""
    else:
        ritmo = ""
    return f"{quadro}{ritmo} ({attesi})"


def _giocate_sensate(previsioni, pmin, pmax):
    """Per ogni partita, le giocate coerenti con l'analisi: {fixture_id: [voci]}."""
    adesso = datetime.now(timezone.utc)
    per_partita = {}
    for p in previsioni:
        try:
            if datetime.fromisoformat(p["data"]) <= adesso + timedelta(minutes=10):
                continue
        except (KeyError, TypeError, ValueError):
            continue
        m, mk = p["mercati"], p.get("mercato") or {}
        for k in MERCATI_RAGIONATE:
            q, pr = mk.get(f"quota_{k}"), m.get(k)
            if not q or pr is None or not pmin <= pr <= pmax:
                continue
            if not _col_favorito(p, k):
                continue                 # mai contro un favorito chiaro
            vantaggio = pr * q - 1
            if vantaggio < -MARGINE_MASSIMO_RAGIONATE:
                continue                 # Bet365 la paga troppo poco
            v = _voce(p, k, pr, q)
            v["nome"] = v["nome"].replace("Casa", p["casa"]).replace("Ospite", p["fuori"])
            if k == "1":
                v["nome"] = f"{p['casa']} vince"
            elif k == "2":
                v["nome"] = f"{p['fuori']} vince"
            elif k in ("1X", "X2"):
                v["nome"] = f"{p['casa'] if k == '1X' else p['fuori']} o pareggio ({k})"
            v["vantaggio"] = vantaggio
            v["motivo"] = motivo_giocata(p, k)
            per_partita.setdefault(p["fixture_id"], []).append(v)
    return per_partita


def _schedine_ragionate(previsioni):
    """Le due schedine ragionate del giorno (vedi RAGIONATE)."""
    fuori = []
    for titolo, qmin, qmax, pmin, pmax, massimo in RAGIONATE:
        per_partita = _giocate_sensate(previsioni, pmin, pmax)
        # di ogni partita la giocata pagata meglio rispetto al giusto;
        # a parita', la piu' probabile
        migliori = [max(lista, key=lambda v: (round(v["vantaggio"], 3), v["prob"]))
                    for lista in per_partita.values()]
        migliori.sort(key=lambda v: (-v["vantaggio"], -v["prob"]))
        voci, quota = [], 1.0
        for v in migliori:
            if quota >= qmin or len(voci) >= massimo:
                break
            voci.append(v)
            quota *= v["quota"]
        if quota > qmax and voci:
            # l'ultima giocata ha sforato: si prova a sostituirla con una
            # delle rimaste che faccia stare la quota nella fascia
            senza = quota / voci[-1]["quota"]
            rimaste = [v for v in migliori if v not in voci]
            adatte = [v for v in rimaste if qmin <= senza * v["quota"] <= qmax]
            if adatte:
                voci[-1] = max(adatte, key=lambda v: (v["vantaggio"], v["prob"]))
                quota = senza * voci[-1]["quota"]
        if not (qmin <= quota <= qmax) or len(voci) < 2:
            continue
        voci.sort(key=lambda v: v["data"])
        fuori.append(_schedina(voci, f"{titolo} - {len(voci)} partite",
                               "Una giocata per partita, coerente con l'analisi, pagata da Bet365 "
                               "vicino al prezzo giusto."))
    return fuori


# --- sistemi integrali (v22.8, criteri dell'utente) -------------------
# Ogni mattina sistemi integrali a 3 partite con 3 esiti ciascuna (27
# colonne, stesso importo per colonna), almeno un risultato esatto per
# partita, quota della colonna minima circa 30 e massima di almeno 800.
# Due versioni: ESCLUSIVI (i tre esiti di una partita si escludono: copre
# piu' risultati, vince una colonna sola) e SOVRAPPOSTI (un esito e due
# risultati esatti dentro quell'esito: se esce l'esatto vincono piu'
# colonne insieme, ma si vince molto piu' di rado). Probabilita' dalla
# matrice dei gol di Pinnacle, quote vere di Bet365 (anche degli esatti).
# Una coppia di sistemi al giorno, due o tre coppie quando ci sono tante
# partite; partite diverse in ogni sistema.
INTEGRALI_QMIN = (26.0, 40.0)        # non piu' usata dalla v23.2
# v23.2 (scelta dell'utente 23/10/2026): per esclusivi e sovrapposti nessuna
# quota minima della colonna, massima di almeno 500; solo esiti sensati
INTEGRALI_QMAX_MINIMA = 500.0
INTEGRALI_PROB_ESITO = 0.15          # l'esito principale almeno al 15%
INTEGRALI_PROB_ESATTO = 0.05         # un risultato esatto almeno al 5%
INTEGRALI_RESA_ESATTO = 0.80         # Bet365 al massimo il 20% sotto il giusto su un esatto
INTEGRALI_COPPIE = ((27, 3), (18, 2), (0, 1))   # partite utilizzabili -> coppie di sistemi
MERCATI_INTEGRALI = ["1", "X", "2", "1X", "X2", "12", "over15", "over25", "over35", "under25",
                     "under35", "gol_gol", "no_gol", "casa_segna", "fuori_segna",
                     "casa_over15", "fuori_over15", "casa_under15", "fuori_under15"]


def _nome_integrale(p, k):
    if "-" in k and k[0].isdigit():
        return f"esatto {k}"
    if k == "1":
        return f"{p['casa']} vince"
    if k == "2":
        return f"{p['fuori']} vince"
    if k == "X":
        return "pareggio"
    return NOMI.get(k, k).replace("Casa", p["casa"]).replace("Ospite", p["fuori"])


def _terne_integrali(p, tipo):
    """Le terne di esiti possibili su una partita, con la loro distribuzione."""
    try:
        import quote_giuste as Qg
        M = Qg.matrice(p["gol_attesi_casa"], p["gol_attesi_fuori"])
    except Exception:
        return []
    n, m, mk = len(M), p["mercati"], p.get("mercato") or {}
    esatte = p.get("quote_esatti") or {}

    def quota(k):
        return esatte.get(k) if "-" in k else mk.get(f"quota_{k}")
    # v23.1: nei sovrapposti niente quota minima della colonna (scelta
    # dell'utente), quindi l'esito principale puo' essere anche il favorito
    # v23.2: solo esiti con una probabilita' concreta, pagati da Bet365 non
    # troppo sotto il giusto
    base = [k for k in MERCATI_INTEGRALI if quota(k) and quota(k) >= 1.15
            and m.get(k) and m[k] >= INTEGRALI_PROB_ESITO and m[k] * quota(k) >= 0.90]
    esatti = sorted(((M[x][y], f"{x}-{y}") for x in range(min(n, 6)) for y in range(min(n, 6))
                     if esatte.get(f"{x}-{y}") and M[x][y] >= INTEGRALI_PROB_ESATTO
                     and M[x][y] * esatte[f"{x}-{y}"] >= INTEGRALI_RESA_ESATTO), reverse=True)[:10]
    if tipo == "esclusivi":
        terne = [t for t in itertools.combinations(base + [e for _, e in esatti], 3)
                 if any("-" in e for e in t)]
    else:
        terne = []
        for b in base:
            dentro = [e for _, e in esatti if esito_avvenuto(b, int(e.split("-")[0]), int(e.split("-")[1]))]
            terne += [(b, e1, e2) for e1, e2 in itertools.combinations(dentro, 2)]
    fuori = []
    for t in terne:
        qs = [quota(k) for k in t]
        dist, doppi = {}, 0.0
        for x in range(n):
            for y in range(n):
                presi = [k for k in t if esito_avvenuto(k, x, y)]
                f = round(sum(quota(k) for k in presi), 3)
                dist[f] = dist.get(f, 0.0) + M[x][y]
                if len(presi) >= 2:
                    doppi += M[x][y]
        copre = sum(v for f, v in dist.items() if f > 0)
        resa = sum(f * v for f, v in dist.items()) / 3
        if resa < 0.75:
            continue
        if tipo == "esclusivi" and doppi > 0:
            continue                     # esclusivi davvero: mai due esiti insieme
        fuori.append({"p": p, "esiti": t, "quote": qs, "dist": dist, "copre": copre,
                      "doppi": doppi, "resa": resa})
    fuori.sort(key=lambda x: -x["copre"])
    # per ogni fascia di quota dell'esito principale la terna che
    # copre di piu', cosi' restano in gara sia gli esiti sicuri sia quelli a
    # quota piu' alta (prendere solo le piu' coprenti lasciava solo i primi)
    fasce = {}
    for x in fuori:
        f = int(math.log(min(x["quote"])) / 0.12)
        if f not in fasce or x["copre"] > fasce[f]["copre"]:
            fasce[f] = x
    return list(fasce.values())


def _valuta_integrale(scelte):
    d = {1.0: 1.0}
    for s in scelte:
        nuovo = {}
        for a, pa in d.items():
            for b, pb in s["dist"].items():
                k = round(a * b, 2)
                nuovo[k] = nuovo.get(k, 0.0) + pa * pb
        d = nuovo
    colonne = 3 ** len(scelte)
    return {"vince": sum(v for f, v in d.items() if f > 0),
            "attivo": sum(v for f, v in d.items() if f >= colonne),
            "incasso_max": max(d), "resa": math.prod(s["resa"] for s in scelte) - 1}


def _miglior_integrale(per_partita, usate, quota_minima=None):
    """Il sistema a 3 partite con la probabilita' piu' alta di andare in attivo."""
    voci = [(fid, t) for fid, lista in per_partita.items() if fid not in usate for t in lista]
    # le partite piu' promettenti, per non provare milioni di combinazioni
    voci.sort(key=lambda v: -v[1]["copre"])
    voci = voci[:160]
    candidati = []
    for a, b, c in itertools.combinations(range(len(voci)), 3):
        fa, fb, fc = voci[a][0], voci[b][0], voci[c][0]
        if fa == fb or fa == fc or fb == fc:
            continue
        sc = (voci[a][1], voci[b][1], voci[c][1])
        qmin = math.prod(min(s["quote"]) for s in sc)
        if quota_minima and not quota_minima[0] <= qmin <= quota_minima[1]:
            continue
        qmax = math.prod(max(s["quote"]) for s in sc)
        if qmax < INTEGRALI_QMAX_MINIMA:
            continue
        candidati.append((math.prod(s["copre"] for s in sc), sc, qmin, qmax))
    candidati.sort(key=lambda x: -x[0])
    migliore = None
    for _, sc, qmin, qmax in candidati[:250]:
        v = _valuta_integrale(sc)
        chiave = (round(v["attivo"], 4), v["resa"])
        if migliore is None or chiave > migliore[0]:
            migliore = (chiave, sc, qmin, qmax, v)
    return migliore


def _miglior_sovrapposto(per_partita, usate, partite_max=35):
    """
    (v23.1, per tutti e due i tipi dalla v23.2) Il sistema con la probabilita'
    piu' alta di andare in attivo, senza quota minima della colonna (scelta
    dell'utente) e con la quota massima di almeno INTEGRALI_QMAX_MINIMA. Si provano tutte le terne
    delle partite piu' promettenti; per ogni fascia di quota minima si
    valutano per bene le combinazioni che coprono di piu'.
    """
    libere = {fid: l for fid, l in per_partita.items() if fid not in usate}
    partite = sorted(libere, key=lambda f: -max(t["copre"] for t in libere[f]))[:partite_max]
    per_fascia = {}
    for a, b, c in itertools.combinations(partite, 3):
        for sc in itertools.product(libere[a], libere[b], libere[c]):
            if math.prod(max(s["quote"]) for s in sc) < INTEGRALI_QMAX_MINIMA:
                continue
            qmin = math.prod(min(s["quote"]) for s in sc)
            per_fascia.setdefault(int(math.log(qmin) / 0.25), []).append(
                (math.prod(s["copre"] for s in sc), sc, qmin))
    migliore = None
    for lista in per_fascia.values():
        lista.sort(key=lambda x: -x[0])
        for _, sc, qmin in lista[:40]:
            v = _valuta_integrale(sc)
            chiave = (round(v["attivo"], 4), v["resa"])
            if migliore is None or chiave > migliore[0]:
                qmax = math.prod(max(s["quote"]) for s in sc)
                migliore = (chiave, sc, qmin, qmax, v)
    return migliore


def _pool_integrali(previsioni):
    """Le partite per i sistemi: quelle del modello piu' quelle dei campionati
    aggiunti (campionati_valore.py), tutte con Pinnacle, della giornata delle
    giocate (dalle 7 alle 7) e non ancora iniziate."""
    nostre = {p["fixture_id"] for p in previsioni}
    try:
        extra = [dict(e, affidabilita=100) for e in leggi_giro().get("partite_extra", [])
                 if e.get("fixture_id") not in nostre]
        tutte = list(previsioni) + con_pinnacle(extra)
    except Exception:
        tutte = list(previsioni)
    oggi = giornata_giocate()
    adesso = datetime.now(timezone.utc)
    fuori = []
    for p in tutte:
        try:
            d = datetime.fromisoformat(p["data"])
        except (KeyError, TypeError, ValueError):
            continue
        locale = d.astimezone(FUSO_GIOCATE)
        g = locale.date() - timedelta(days=1) if locale.hour < 7 else locale.date()
        if g == oggi and d > adesso + timedelta(minutes=10):
            fuori.append(p)
    return fuori


# --- sistemi integrali con partite ed esiti variabili (v23.3) ---------
# Regole dell'utente (23/10/2026), per esclusivi e sovrapposti: nessuna
# quota minima della colonna, quota massima di almeno INTEGRALI_QMAX_MINIMA,
# da 2 a 4 partite, da 2 a 4 esiti per partita, stesso importo per colonna
# (colonne = prodotto degli esiti di ogni partita), almeno un risultato
# esatto per partita, solo esiti con probabilita' concrete. Si cerca il
# sistema che va in attivo piu' spesso con una ricerca a fascio: le coppie
# di partite migliori, poi si aggiunge una partita alla volta tenendo i
# candidati piu' promettenti.
INTEGRALI_PARTITE = (2, 4)
INTEGRALI_MASSIMO = 6               # sistemi al giorno, al massimo (v23.4)
INTEGRALI_ATTIVO_MINIMO = 0.05      # sotto il 5% di andare in attivo non si propone
INTEGRALI_ESITI = (2, 4)
INTEGRALI_FASCIO = 500
INTEGRALI_PARTITE_PROVATE = 30


def _insiemi_integrali(p, tipo):
    """Gli insiemi di 2-4 esiti possibili su una partita, con la distribuzione
    del fattore G = (somma delle quote degli esiti usciti) / numero di esiti."""
    try:
        import quote_giuste as Qg
        M = Qg.matrice(p["gol_attesi_casa"], p["gol_attesi_fuori"])
    except Exception:
        return []
    n, m, mk = len(M), p["mercati"], p.get("mercato") or {}
    esatte = p.get("quote_esatti") or {}

    def quota(k):
        return esatte.get(k) if "-" in k else mk.get(f"quota_{k}")
    base = [k for k in MERCATI_INTEGRALI if quota(k) and quota(k) >= 1.15
            and m.get(k) and m[k] >= INTEGRALI_PROB_ESITO and m[k] * quota(k) >= 0.90]
    esatti = [e for _, e in sorted(((M[x][y], f"{x}-{y}") for x in range(min(n, 6)) for y in range(min(n, 6))
                                    if esatte.get(f"{x}-{y}") and M[x][y] >= INTEGRALI_PROB_ESATTO
                                    and M[x][y] * esatte[f"{x}-{y}"] >= INTEGRALI_RESA_ESATTO), reverse=True)[:8]]
    if not esatti:
        return []
    celle = [(x, y, M[x][y]) for x in range(n) for y in range(n)]
    dove = {k: {(x, y) for x, y, _ in celle if esito_avvenuto(k, x, y)} for k in base + esatti}
    lo, hi = INTEGRALI_ESITI
    insiemi = []
    if tipo == "esclusivi":
        tutti = base + esatti
        def estendi(scelti, da):
            if lo <= len(scelti) <= hi and any("-" in k for k in scelti):
                insiemi.append(tuple(scelti))
            if len(scelti) == hi:
                return
            for i in range(da, len(tutti)):
                k = tutti[i]
                if all(not (dove[k] & dove[c]) for c in scelti):
                    estendi(scelti + [k], i + 1)
        estendi([], 0)
    else:
        for b in base:
            dentro = [e for e in esatti if next(iter(dove[e])) in dove[b]]
            for r in range(lo - 1, hi):
                insiemi += [(b,) + c for c in itertools.combinations(dentro, r)]
    fuori = []
    for t in insiemi:
        k = len(t)
        qs = [quota(e) for e in t]
        dist = {}
        for x, y, pr in celle:
            g = round(sum(quota(e) for e in t if (x, y) in dove[e]) / k, 3)
            dist[g] = dist.get(g, 0.0) + pr
        copre = sum(v for g, v in dist.items() if g > 0)
        resa = sum(g * v for g, v in dist.items())
        if resa < 0.75:
            continue
        fuori.append({"p": p, "esiti": t, "quote": qs, "dist": dist, "copre": copre,
                      "resa": resa, "k": k})
    # per ogni numero di esiti e fascia di quota minima, l'insieme che copre di piu'
    fasce = {}
    for x in fuori:
        f = (x["k"], int(math.log(min(x["quote"])) / 0.15))
        if f not in fasce or x["copre"] > fasce[f]["copre"]:
            fasce[f] = x
    return sorted(fasce.values(), key=lambda x: -x["copre"])[:14]


def _prodotto_dist(a, b):
    out = {}
    for x, pa in a.items():
        for y, pb in b.items():
            k = round(x * y, 3)
            out[k] = out.get(k, 0.0) + pa * pb
    return out


def _cerca_integrali(per_partita):
    """Tutti i sistemi buoni trovati (2-4 partite), dal piu' probabile in attivo."""
    partite = sorted(per_partita, key=lambda f: -max(x["copre"] for x in per_partita[f]))
    partite = partite[:INTEGRALI_PARTITE_PROVATE]
    indice = {f: i for i, f in enumerate(partite)}

    def stato(scelte, dist):
        attivo = sum(v for g, v in dist.items() if g >= 1 - 1e-9)
        return {"scelte": scelte, "dist": dist, "attivo": attivo,
                "qmax": math.prod(max(s["quote"]) for s in scelte)}
    fascio = [stato((s,), s["dist"]) for f in partite for s in per_partita[f]]
    finali = []
    for numero in range(2, INTEGRALI_PARTITE[1] + 1):
        nuovi = []
        for st in fascio:
            ultimo = indice[st["scelte"][-1]["p"]["fixture_id"]]
            for f in partite[ultimo + 1:]:
                for s in per_partita[f]:
                    nuovi.append(stato(st["scelte"] + (s,), _prodotto_dist(st["dist"], s["dist"])))
        if not nuovi:
            break
        nuovi.sort(key=lambda x: -x["attivo"])
        if numero >= INTEGRALI_PARTITE[0]:
            finali += [x for x in nuovi if x["qmax"] >= INTEGRALI_QMAX_MINIMA]
        fascio = nuovi[:INTEGRALI_FASCIO]
    for x in finali:
        x["resa"] = sum(g * v for g, v in x["dist"].items()) - 1
        x["vince"] = sum(v for g, v in x["dist"].items() if g > 0)
        x["colonne"] = math.prod(s["k"] for s in x["scelte"])
        x["qmin"] = math.prod(min(s["quote"]) for s in x["scelte"])
        x["incasso_max"] = max(x["dist"]) * x["colonne"]
    finali.sort(key=lambda x: (-round(x["attivo"], 4), -x["resa"]))
    return finali


def _sistemi_integrali(previsioni):
    """I sistemi integrali del giorno (vedi INTEGRALI_*), a coppie: uno a esiti
    esclusivi e uno a esiti sovrapposti, partite diverse fra tutti i sistemi."""
    oggi = _pool_integrali(previsioni)
    trovati, utili = {}, set()
    for tipo in ("esclusivi", "sovrapposti"):
        per_partita = {}
        for p in oggi:
            lista = _insiemi_integrali(p, tipo)
            if lista:
                per_partita[p["fixture_id"]] = lista
        utili |= set(per_partita)
        trovati[tipo] = _cerca_integrali(per_partita) if len(per_partita) >= 2 else []
    # v23.4 (scelta dell'utente): ogni giorno quanti piu' sistemi possibile,
    # al massimo INTEGRALI_MASSIMO, alternando i due tipi; quando un tipo non
    # ne ha piu' si continua con l'altro. Partite diverse fra tutti i sistemi.
    fuori, usate = [], set()
    ordine = ["esclusivi", "sovrapposti"]
    while len(fuori) < INTEGRALI_MASSIMO:
        x = tipo = None
        for t in ordine:
            x = next((c for c in trovati[t] if c["attivo"] >= INTEGRALI_ATTIVO_MINIMO
                      and not any(s["p"]["fixture_id"] in usate for s in c["scelte"])), None)
            if x:
                tipo = t
                break
        if not x:
            break
        ordine = [o for o in ordine if o != tipo] + [tipo]     # la prossima volta l'altro tipo
        if x:
            voci, unioni = [], {}
            for s in sorted(x["scelte"], key=lambda s: s["p"]["data"]):
                p = s["p"]
                usate.add(p["fixture_id"])
                unioni[str(p["fixture_id"])] = s["copre"]
                for k, q in zip(s["esiti"], s["quote"]):
                    v = _voce(p, k, None, q)
                    v["nome"] = f"{_nome_integrale(p, k)} " + f"{q:.2f}".replace(".", ",")
                    voci.append(v)
            fuori.append({
                "titolo": f"Sistema {len(fuori) + 1} - esiti " + tipo,
                "nota": ("Gli esiti di ogni partita si escludono: si prende un esito per partita."
                         if tipo == "esclusivi" else
                         "Un esito e dei risultati esatti dentro: se esce l'esatto vincono piu' colonne insieme."),
                "tipo": tipo, "voci": voci, "unioni": unioni, "sistema": True,
                "prob": x["vince"], "attivo": x["attivo"], "resa": x["resa"],
                "quota": x["qmin"], "quota_max": x["qmax"], "incasso_max": x["incasso_max"],
                "combinazioni": x["colonne"]})
    return fuori


# --- quota alta (v23.0) ---------------------------------------------
# Il metodo delle schedine a quota 15 e 36 del 21/10/2026 (uscite tutte e
# due), chiesto dall'utente per la scheda "Quota alta": per ogni quota
# obiettivo la combinazione con la PROBABILITA' PIU' ALTA, una giocata per
# partita, scelta fra tutte le partite di oggi (modello e campionati
# aggiunti, giornata 7-7, non ancora iniziate). Solo giocate pagate da
# Bet365 al massimo QUOTA_ALTA_MARGINE sotto il prezzo giusto di Pinnacle,
# mai contro un favorito chiaro, ognuna almeno al QUOTA_ALTA_PROB_MINIMA.
# La ricerca e' esatta: programmazione dinamica sul logaritmo della quota
# (la probabilita' di una schedina e' il prodotto, la quota pure).
QUOTA_ALTA = (("Quota 15-30", 15.0, 30.0), ("Quota circa 40", 36.0, 46.0))
QUOTA_ALTA_MARGINE = 0.025
QUOTA_ALTA_PROB_MINIMA = 0.45
QUOTA_ALTA_GIOCATE = (3, 9)
MERCATI_QUOTA_ALTA = ["1", "2", "X", "1X", "X2", "12", "over15", "over25", "over35", "under25",
                      "under35", "gol_gol", "no_gol", "casa_segna", "fuori_segna",
                      "casa_over15", "fuori_over15", "casa_under15", "fuori_under15",
                      "casa_under25", "fuori_under25"]


def _candidate_quota_alta(p):
    m, mk = p["mercati"], p.get("mercato") or {}
    fuori = []
    for k in MERCATI_QUOTA_ALTA:
        q, pr = mk.get(f"quota_{k}"), m.get(k)
        if not q or pr is None or pr < QUOTA_ALTA_PROB_MINIMA or q < 1.05:
            continue
        if pr * q - 1 < -QUOTA_ALTA_MARGINE or not _col_favorito(p, k):
            continue
        v = _voce(p, k, pr, q)
        v["nome"] = {"1": f"{p['casa']} vince", "2": f"{p['fuori']} vince", "X": "pareggio",
                     "1X": f"{p['casa']} o pareggio (1X)", "X2": f"{p['fuori']} o pareggio (X2)"}.get(
            k, v["nome"].replace("Casa", p["casa"]).replace("Ospite", p["fuori"]))
        v["vantaggio"] = pr * q - 1
        v["motivo"] = motivo_giocata(p, k)
        fuori.append(v)
    return fuori


def _miglior_schedina(per_partita, qmin, qmax, evita=()):
    """La schedina con la probabilita' piu' alta e la quota fra qmin e qmax.
    evita: partite da usare il meno possibile (contano come se fossero
    molto meno probabili, v23.5)."""
    passo = 0.005
    hi = int(math.log(qmax) / passo) + 1
    massimo = QUOTA_ALTA_GIOCATE[1]
    # stato: (giocate, quota in log a passi) -> (log prob, voci)
    stati = {(0, 0): (0.0, ())}
    for lista in per_partita:
        nuovi = dict(stati)
        for (n, b), (lp, voci) in stati.items():
            if n >= massimo:
                continue
            for v in lista:
                nb = b + int(round(math.log(v["quota"]) / passo))
                if nb > hi:
                    continue
                chiave = (n + 1, nb)
                val = lp + math.log(v["prob"]) + (math.log(0.2) if v["fixture_id"] in evita else 0.0)
                if chiave not in nuovi or val > nuovi[chiave][0]:
                    nuovi[chiave] = (val, voci + (v,))
        stati = nuovi
    migliore = None
    for (n, b), (lp, voci) in stati.items():
        if n < QUOTA_ALTA_GIOCATE[0]:
            continue
        q = math.prod(v["quota"] for v in voci)
        if qmin <= q <= qmax and (migliore is None or lp > migliore[0]):
            migliore = (lp, voci)
    return list(migliore[1]) if migliore else None


def _schedine_quota_alta(previsioni):
    pool = _pool_integrali(previsioni)
    per_partita = [l for l in (_candidate_quota_alta(p) for p in pool) if l]
    fuori, usate = [], set()
    for titolo, qmin, qmax in QUOTA_ALTA:
        # v23.5 (scelta dell'utente): la seconda schedina con partite diverse
        # dalla prima; se non si riesce, il meno possibile in comune
        voci = _miglior_schedina([l for l in per_partita if l[0]["fixture_id"] not in usate], qmin, qmax)
        if not voci and usate:
            voci = _miglior_schedina(per_partita, qmin, qmax, evita=usate)
        if not voci:
            continue
        usate |= {v["fixture_id"] for v in voci}
        voci.sort(key=lambda v: v["data"])
        s = _schedina(voci, f"{titolo} - {len(voci)} partite",
                      "La combinazione con la probabilita' piu' alta per questa quota: una giocata per partita, "
                      "pagata da Bet365 vicino al prezzo giusto di Pinnacle.")
        s["resa"] = s["prob"] * s["quota"] - 1
        fuori.append(s)
    return fuori


def _proposte_giorno(previsioni):
    """
    Schedine e risultati esatti del giorno. Si costruiscono UNA volta, al
    primo giro della mattina, e restano quelli fino al giorno dopo: se
    cambiassero a ogni aggiornamento, una schedina vista alle nove
    potrebbe sparire dopo che l'hai giocata.
    """
    oggi = giornata_giocate().isoformat()
    if os.path.exists(FILE_GIOCATE):
        try:
            with open(FILE_GIOCATE, encoding="utf-8") as f:
                salvate = json.load(f)
            if (salvate.get("giorno") == oggi and salvate.get("giocate")
                    and "esatti" in salvate):
                # v22.6: le schedine ragionate si aggiungono a quelle gia'
                # fissate stamattina, senza cambiare le altre
                if "integrali" not in salvate["giocate"]:
                    salvate["giocate"]["integrali"] = _sistemi_integrali(con_pinnacle(previsioni))
                    with open(FILE_GIOCATE, "w", encoding="utf-8") as f:
                        json.dump(salvate, f, ensure_ascii=False)
                if "quota_alta" not in salvate["giocate"]:
                    salvate["giocate"]["quota_alta"] = _schedine_quota_alta(con_pinnacle(previsioni))
                    with open(FILE_GIOCATE, "w", encoding="utf-8") as f:
                        json.dump(salvate, f, ensure_ascii=False)
                return salvate
        except (ValueError, OSError):
            pass

    # v21.3: anche i risultati esatti di nuovo da Pinnacle (piu' precisi del
    # modello anche sui punteggi: 11,6% contro 10,8% sull'archivio), solo dal
    # 14% in su e con la quota di Bet365 accanto
    da_pinnacle = con_pinnacle(previsioni)
    esatti, disponibili = _calcola_esatti(da_pinnacle)
    dati = {"giorno": oggi, "fonte": "pinnacle", "fonte_esatti": "pinnacle",
            "generato": datetime.now(timezone.utc).isoformat(timespec="minutes"),
            "giocate": costruisci_giocate(da_pinnacle),
            "esatti": esatti, "esatti_disponibili": disponibili}
    try:
        with open(FILE_GIOCATE, "w", encoding="utf-8") as f:
            json.dump(dati, f, ensure_ascii=False)
    except OSError:
        pass
    return dati


def giocate_correnti(previsioni):
    """Le schedine del giorno e l'ora in cui sono state fatte."""
    dati = _proposte_giorno(previsioni)
    return dati["giocate"], dati["generato"]


def esatti_correnti(previsioni):
    """I risultati esatti del giorno e fra quante partite sono scelti."""
    dati = _proposte_giorno(previsioni)
    return dati["esatti"], dati.get("esatti_disponibili", len(dati["esatti"]))


def costruisci_giocate(previsioni):
    """Le proposte, divise per logica."""
    proposte = {"singole": [], "alta": [], "valore": [],
                "sistemi": [], "miste": [], "ragionate": [], "integrali": [], "quota_alta": []}
    previsioni = _finestra_giorni(previsioni)

    # --- singole: le migliori per vantaggio stimato ----------------
    con_quota = [v for v in _raccogli(previsioni, SEMPLICI + SICURI)
                 if v["quota"] and v["mercato"]
                 and v["prob"] >= PROB_MINIMA_VALORE]
    for v in con_quota:
        v["vantaggio"] = v["prob"] * v["quota"] - 1
    con_quota = [v for v in con_quota if v["vantaggio"] <= VANTAGGIO_MASSIMO]
    con_quota.sort(key=lambda v: -v["vantaggio"])
    proposte["singole"] = con_quota[:3]

    # --- alta probabilita' -----------------------------------------
    # Prima quelle sui due giorni, poi quelle di sola oggi: si chiudono
    # in serata invece di restare aperte fino a domani.
    # Prima quelle che si chiudono in giornata, che sono le piu' utili
    # subito. Poi quelle che arrivano a domani, ma solo se contengono
    # davvero una partita di domani: altrimenti sarebbero le stesse.
    primo_giorno = _solo_primo_giorno(previsioni)
    try:
        giorno = datetime.fromisoformat(
            primo_giorno[0]["data"]).astimezone(FUSO_GIOCATE).date()
    except (KeyError, IndexError, TypeError, ValueError):
        giorno = None
    proposte["alta"] = _alta_probabilita(
        primo_giorno, 3,
        etichetta=("solo oggi" if giorno == giornata_giocate()
                   else "un solo giorno"))
    if len(primo_giorno) < len(previsioni):
        di_oggi = {p["fixture_id"] for p in primo_giorno}
        proposte["alta"] += _alta_probabilita(
            previsioni, 3, etichetta="fino a domani",
            gia_fatte=proposte["alta"], oltre_oggi=di_oggi)

    # --- valore atteso ---------------------------------------------
    valore = [v for v in con_quota if v["vantaggio"] > 0]
    usate, scelti = set(), []
    for v in valore:
        if v["fixture_id"] in usate:
            continue
        usate.add(v["fixture_id"])
        scelti.append(v)
    for n, etichetta in ((2, "doppia"), (3, "tripla")):
        if len(scelti) >= n:
            proposte["valore"].append(_schedina(
                scelti[:n], f"Valore atteso - {etichetta}",
                "Solo esiti dove stimiamo piu' probabilita' di quanta "
                "ne implichi la quota."))

    # --- sistemi integrali ------------------------------------------
    # v22.8: al posto dei vecchi sistemi, quelli fatti con i criteri
    # dell'utente (vedi INTEGRALI_*), solo con le partite di oggi
    proposte["integrali"] = _sistemi_integrali(previsioni)
    if not proposte["integrali"]:
        proposte.setdefault("note", {})["sistemi"] = (
            "Oggi non ci sono abbastanza partite per un sistema con quota minima circa 30 e massima di almeno 800.")

    # --- quota alta (v23.0): prende il posto delle schedine ragionate ----
    proposte["quota_alta"] = _schedine_quota_alta(previsioni)
    if not proposte["quota_alta"]:
        proposte.setdefault("note", {})["quota_alta"] = (
            "Oggi non ci sono abbastanza partite con giocate pagate vicino al prezzo giusto per arrivare a queste quote.")

    # --- miste a quota mirata ---------------------------------------
    # spente dalla v21.1 (decisione dell'utente): 4 vinte su 47, e su tanti
    # eventi il margine del bookmaker si moltiplica
    if not MISTE_ATTIVE:
        return proposte
    # Tre proposte con quote crescenti. Per ognuna si aggiungono eventi
    # finche' la quota non si avvicina al bersaglio, partendo da punti
    # diversi della lista per non produrre tre schedine identiche.
    per_id = {p["fixture_id"]: p for p in previsioni}
    candidati = [v for v in _raccogli(previsioni,
                                      SEMPLICI + SICURI + COMBO + ALTA_QUOTA,
                                      prob_min=PROB_MINIMA_MISTA)
                 if _col_favorito(per_id[v["fixture_id"]], v["esito"])
                 and _sostenuto(per_id[v["fixture_id"]], v["esito"])
                 and (v["quota"] or _quota_equa(v["prob"])) <= QUOTA_MASSIMA_MISTA]
    migliori = {}
    for v in candidati:
        q = v["quota"] or _quota_equa(v["prob"])
        attuale = migliori.get(v["fixture_id"])
        if attuale is None or q > (attuale["quota"] or
                                   _quota_equa(attuale["prob"])):
            migliori[v["fixture_id"]] = v
    pool = sorted(migliori.values(),
                  key=lambda v: (v["quota"] or _quota_equa(v["prob"])))

    usate_miste = set()
    note_miste = []
    for bersaglio in QUOTE_MISTE:
        minimo, massimo = bersaglio * 0.70, bersaglio * 1.40
        migliore = None
        for partenza in range(len(pool)):
            voci, quota, quanti = [], 1.0, {}
            for v in pool[partenza:]:
                if len(voci) >= MAX_EVENTI_MISTA:
                    break
                if quanti.get(v["esito"], 0) >= MAX_STESSO_MERCATO:
                    continue
                q = v["quota"] or _quota_equa(v["prob"])
                if quota * q > massimo and len(voci) >= 2:
                    break
                voci.append(v)
                quanti[v["esito"]] = quanti.get(v["esito"], 0) + 1
                quota *= q
                if quota >= bersaglio:
                    break
            if len(voci) < 2 or not minimo <= quota <= massimo:
                continue
            chiave = tuple(sorted(x["fixture_id"] for x in voci))
            if chiave in usate_miste:
                continue
            scarto = abs(quota - bersaglio)
            if migliore is None or scarto < migliore[0]:
                migliore = (scarto, voci, quota, chiave)
        if migliore:
            _, voci, quota, chiave = migliore
            usate_miste.add(chiave)
            proposte["miste"].append(_schedina(
                voci, f"Mista - quota {quota:.2f}",
                f"Costruita attorno a quota {bersaglio:.0f}, con esiti in cui "
                f"ogni condizione e' sostenuta dal modello."))
        else:
            # meglio non proporre niente che proporre una schedina storta
            note_miste.append(f"{bersaglio:.0f}")

    if note_miste:
        quali = ", ".join(note_miste)
        proposte["note"] = {"miste":
            f"Oggi non ci sono le condizioni per la mista attorno a quota "
            f"{quali}: servono partite con esiti solidi, e non ce ne sono "
            f"abbastanza fra quelle dei prossimi due giorni."}
        proposte["valore"] = []
    return proposte


def _costruisci_sistemi(previsioni):
    """
    Sistemi integrali: piu' esiti sulla stessa partita, anche diversi
    fra loro (risultato esatto, combo, esito finale). Il sistema genera
    tutte le combinazioni prendendone uno per partita, e per vincere
    qualcosa basta che in OGNI partita almeno uno si avveri.

    La probabilita' si calcola con l'unione sulla matrice: gli esiti
    scelti si sovrappongono, e sommarli darebbe numeri senza senso.
    """
    adatte = [p for p in previsioni
              if p.get("affidabilita", 0) >= MIN_AFFIDABILITA_GIOCATE
              and p.get("gol_attesi_casa") and p.get("gol_attesi_fuori")]
    if len(adatte) < 2:
        return []

    preparate = []
    for p in adatte:
        M = matrice(p["gol_attesi_casa"], p["gol_attesi_fuori"], RHO_SISTEMI)
        m = p["mercati"]

        # per ogni partita scelgo tre esiti di natura diversa:
        # uno prudente, uno intermedio, uno ardito. Nessuno puo' valere
        # meno della quota minima.
        def abbastanza(k):
            pe = m.get(k, 0)
            return pe > 0 and _quota_equa(pe) >= QUOTA_MINIMA_ESITO

        # La gamba prudente non deve essere la piu' probabile in
        # assoluto: sarebbe quasi sempre una quasi-certezza che paga
        # 1.12 e rende inutile tutto il sistema. Fra quelle abbastanza
        # sicure si prende quella che paga di piu'.
        prudenti = [k for k in SICURI if abbastanza(k)]
        solide = [k for k in prudenti if m[k] >= SOGLIA_PRUDENTE]
        prudente = (min(solide, key=lambda k: m[k]) if solide else
                    max(prudenti, key=lambda k: m[k]) if prudenti else None)
        # La seconda gamba resta nella fascia di mezzo: se ci mettessimo
        # anche i mercati ad alta probabilita' (l'Under 2.5 di una
        # squadra, per dire) l'unione salirebbe al 99% e la quota del
        # sistema crollerebbe. Quelli servono alle miste, non qui.
        esatto = (m.get("punteggi_probabili") or [{}])[0].get("risultato")
        # v21.1: la seconda gamba non deve coprire quello che la prima lascia
        # fuori (Over 1.5 + Under 2.5 = ogni risultato, sistema al 100%):
        # si prende la piu' probabile che tiene l'unione sotto UNIONE_MASSIMA
        def tiene(k):
            return prob_unione(M, [e for e in (prudente, k, esatto) if e]) <= UNIONE_MASSIMA
        intermedi = [k for k in ("1", "2", "over25", "under25",
                                 "gol_gol", "no_gol", "mg_1_2", "mg_2_3")
                     if m.get(k, 0) >= 0.35 and abbastanza(k) and tiene(k)]
        intermedio = max(intermedi, key=lambda k: m[k]) if intermedi else None
        combo = [k for k in COMBO if m.get(k, 0) >= 0.30 and abbastanza(k) and tiene(k)]
        combo_scelta = max(combo, key=lambda k: m[k]) if combo else None

        scelta = [e for e in (prudente, intermedio or combo_scelta, esatto)
                  if e]
        if len(scelta) < 2:
            continue
        unione = prob_unione(M, scelta)
        if unione > UNIONE_MASSIMA:
            continue
        preparate.append({"p": p, "esiti": scelta, "unione": unione,
                          "quote": [m.get(e) for e in scelta]})

    preparate.sort(key=lambda d: -d["unione"])
    fuori = []
    for n, etichetta in ((2, "due partite"), (3, "tre partite")):
        if len(preparate) < n:
            continue
        gruppo = preparate[:n]
        prob = 1.0
        combinazioni = 1
        voci = []
        for d in gruppo:
            prob *= d["unione"]
            combinazioni *= len(d["esiti"])
            for e in d["esiti"]:
                voci.append(_voce(d["p"], e,
                                  d["p"]["mercati"].get(e) or
                                  next((s["prob"] for s in
                                        d["p"]["mercati"]["punteggi_probabili"]
                                        if s["risultato"] == e), 0.0)))
        # quota: la piu' bassa e la piu' alta fra tutte le combinazioni
        minima = massima = 1.0
        for d in gruppo:
            valori = []
            for e in d["esiti"]:
                pe = d["p"]["mercati"].get(e)
                if pe is None:
                    pe = next((s["prob"] for s in
                               d["p"]["mercati"]["punteggi_probabili"]
                               if s["risultato"] == e), 0.1)
                valori.append(_quota_equa(pe))
            minima *= min(valori)
            massima *= max(valori)
        fuori.append({
            "unioni": {str(d["p"]["fixture_id"]): round(d["unione"], 4)
                       for d in gruppo},
            "titolo": f"Sistema integrale - {etichetta}",
            "nota": (f"{combinazioni} combinazioni. Per vincere qualcosa "
                     f"basta che in ogni partita si avveri almeno uno degli "
                     f"esiti scelti."),
            "voci": voci, "prob": prob, "quota": minima,
            "quota_max": massima, "combinazioni": combinazioni,
            "sistema": True})
    return fuori


# ============================================================
#  VERIFICA DELLE SCHEDINE PROPOSTE
# ============================================================

def esito_avvenuto(esito, gc, ga):
    """
    Se un esito si e' verificato, dato il risultato finale.
    Gestisce esiti semplici, combo (a+b) e risultati esatti (2-1).
    Restituisce None se l'esito non e' riconosciuto.
    """
    if "+" in esito:
        parti = esito.split("+")
        valori = [esito_avvenuto(p, gc, ga) for p in parti]
        if any(v is None for v in valori):
            return None
        return all(valori)

    if "-" in esito and esito[0].isdigit():
        try:
            x, y = esito.split("-")
            return gc == int(x) and ga == int(y)
        except ValueError:
            return None

    totale = gc + ga
    scarto = gc - ga

    def fascia(minimo, massimo):
        """Il totale dei gol cade dentro l'intervallo: e' il multigol."""
        return minimo <= totale <= massimo

    tabella = {
        # --- esito finale e doppia chance ---
        "1": gc > ga, "X": gc == ga, "2": gc < ga,
        "1X": gc >= ga, "12": gc != ga, "X2": gc <= ga,

        # --- totale gol ---
        "over05": totale >= 1, "under05": totale < 1,
        "over15": totale >= 2, "under15": totale < 2,
        "over25": totale >= 3, "under25": totale < 3,
        "over35": totale >= 4, "under35": totale < 4,
        "over45": totale >= 5, "under45": totale < 5,
        "pari": totale % 2 == 0, "dispari": totale % 2 == 1,

        # --- multigol: quanti gol in tutto, dentro una fascia ---
        "mg_0_1": fascia(0, 1), "mg_0_2": fascia(0, 2), "mg_0_3": fascia(0, 3),
        "mg_1_2": fascia(1, 2), "mg_1_3": fascia(1, 3), "mg_1_4": fascia(1, 4),
        "mg_2_3": fascia(2, 3), "mg_2_4": fascia(2, 4), "mg_2_5": fascia(2, 5),
        "mg_3_4": fascia(3, 4), "mg_3_5": fascia(3, 5), "mg_3_6": fascia(3, 6),

        # --- entrambe le squadre a segno ---
        "gol_gol": gc > 0 and ga > 0, "gol": gc > 0 and ga > 0,
        "no_gol": not (gc > 0 and ga > 0), "nogol": not (gc > 0 and ga > 0),

        # --- gol della singola squadra ---
        # "casa_nonsegna" e' anche la porta inviolata dell'ospite,
        # e viceversa: e' lo stesso evento visto dai due lati.
        "casa_segna": gc > 0, "casa_nonsegna": gc == 0,
        "fuori_segna": ga > 0, "fuori_nonsegna": ga == 0,
        "casa_over15": gc >= 2, "casa_under15": gc < 2,
        "casa_over25": gc >= 3, "casa_under25": gc < 3,
        "fuori_over15": ga >= 2, "fuori_under15": ga < 2,
        "fuori_over25": ga >= 3, "fuori_under25": ga < 3,
        "casa_mg_1_2": 1 <= gc <= 2, "casa_mg_1_3": 1 <= gc <= 3,
        "fuori_mg_1_2": 1 <= ga <= 2, "fuori_mg_1_3": 1 <= ga <= 3,

        # --- scarto di gol, cioe' l'handicap ---
        # "casa_2plus": la casa vince con almeno due gol di scarto.
        # "casa_h1": la casa con un gol di vantaggio regalato non perde,
        #            cioe' l'handicap +1 tipico dei bookmaker.
        "casa_2plus": scarto >= 2, "casa_3plus": scarto >= 3,
        "fuori_2plus": scarto <= -2, "fuori_3plus": scarto <= -3,
        "casa_h1": scarto >= -1, "fuori_h1": scarto <= 1,
    }
    return tabella.get(esito)


def salva_schedine(proposte, conn):
    """Registra le proposte, per poterle verificare quando si gioca."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schedine (
            codice TEXT PRIMARY KEY, categoria TEXT, titolo TEXT,
            quota REAL, prob REAL, n_eventi INTEGER, proposta_il TEXT)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schedine_eventi (
            codice TEXT, fixture_id INTEGER, esito TEXT,
            casa TEXT, fuori TEXT, prob REAL,
            PRIMARY KEY (codice, fixture_id, esito))
    """)
    adesso = datetime.now(timezone.utc).isoformat()
    for categoria, elenco in proposte.items():
        if not isinstance(elenco, list):
            continue                      # le note non sono schedine
        for s in elenco:
            # una singola e' una schedina con un solo evento
            if categoria == "singole":
                s = {"titolo": "Singola", "quota": s.get("quota"),
                     "prob": s["prob"], "voci": [s]}
            # il codice identifica la schedina dai suoi eventi: la stessa
            # proposta rigenerata piu' volte non viene contata due volte
            parti = sorted(f"{v['fixture_id']}:{v['esito']}" for v in s["voci"])
            codice = categoria + "|" + "|".join(parti)
            conn.execute("""INSERT OR IGNORE INTO schedine
                            VALUES (?,?,?,?,?,?,?)""",
                         (codice, categoria, s["titolo"], s["quota"],
                          s["prob"], len(s["voci"]), adesso))
            for v in s["voci"]:
                conn.execute("""INSERT OR IGNORE INTO schedine_eventi
                                VALUES (?,?,?,?,?,?)""",
                             (codice, v["fixture_id"], v["esito"],
                              v["casa"], v["fuori"], v["prob"]))
    conn.commit()


def rendimento_schedine():
    """
    Come sono andate le schedine gia' concluse. Una schedina conta solo
    se TUTTE le sue partite sono state giocate.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            SELECT s.codice, s.categoria, s.quota, s.prob, s.n_eventi
            FROM schedine s
        """)
        schedine = cur.fetchall()
        if not schedine:
            conn.close()
            return None

        cur.execute("""
            SELECT e.codice, e.esito, f.goals_home, f.goals_away, e.fixture_id
            FROM schedine_eventi e
            JOIN fixtures f ON f.id = e.fixture_id
            WHERE f.goals_home IS NOT NULL AND f.status IN ('FT','AET','PEN')
        """)
        esiti, partite = {}, {}
        for codice, esito, gc, ga, fid in cur.fetchall():
            a = esito_avvenuto(esito, gc, ga)
            esiti.setdefault(codice, []).append(a)
            partite.setdefault(codice, {}).setdefault(fid, []).append(a)
        conn.close()
    except sqlite3.OperationalError:
        return None

    per_categoria = {}
    for codice, categoria, quota, prob, n_eventi in schedine:
        avvenuti = esiti.get(codice, [])
        if len(avvenuti) < n_eventi or any(a is None for a in avvenuti):
            continue        # non tutte le partite sono state giocate
        if categoria == "sistemi":
            # v21.1: un sistema e' vinto se in OGNI partita si avvera almeno
            # uno degli esiti scelti (prima si chiedeva che si avverassero tutti)
            vinta = all(any(x) for x in partite.get(codice, {}).values())
        else:
            vinta = all(avvenuti)
        d = per_categoria.setdefault(categoria, {"n": 0, "vinte": 0,
                                                 "ritorno": 0.0,
                                                 "attesa": 0.0})
        d["n"] += 1
        d["attesa"] += prob
        if vinta:
            d["vinte"] += 1
            d["ritorno"] += quota - 1
        else:
            d["ritorno"] -= 1
    return per_categoria or None


def esatti_come_schedine(esatti):
    """
    I risultati esatti proposti, nella forma di una schedina da un
    evento solo. Servono a registrarli e a verificarli come tutto il
    resto: altrimenti resterebbero solo dentro il file del giorno, che
    viene riscritto ogni mattina, e non sapremmo mai quante volte il
    risultato proposto e' poi uscito davvero.
    """
    fuori = []
    for d in esatti:
        p = d.get("p") or {}
        fuori.append({
            "titolo": "Risultato esatto",
            "quota": _quota_equa(d["prob"]),
            "prob": d["prob"],
            "voci": [{"fixture_id": d["fixture_id"], "esito": d["risultato"],
                      "casa": p.get("casa", ""), "fuori": p.get("fuori", ""),
                      "prob": d["prob"]}],
        })
    return fuori


def scrivi_giocate(previsioni, generato, rho=-0.05):
    global RHO_SISTEMI
    RHO_SISTEMI = rho
    proposte, _ = giocate_correnti(previsioni)
    esatti, _ = esatti_correnti(previsioni)
    try:
        conn = sqlite3.connect(DB_PATH)
        salva_schedine(dict(proposte, esatti=esatti_come_schedine(esatti)), conn)
        conn.close()
    except sqlite3.OperationalError:
        pass

    def riga_voce(v):
        marchio = ('<span class="uff">uff</span>'
                   if v["formazioni"] == "ufficiale" else '')
        quota = (f'{v["quota"]:.2f}' if v["quota"]
                 else f'{_quota_equa(v["prob"]):.2f}<span class="eq">eq</span>')
        return (f'<div class="ev">'
                f'<div class="ev-sx"><div class="ev-p">{v["casa"]} - {v["fuori"]}'
                f'{marchio}</div>'
                f'<div class="ev-l">{v["campionato"]} &middot; '
                f'{v["data"][8:10]}/{v["data"][5:7]} {v["data"][11:16]}</div></div>'
                f'<div class="ev-dx"><div class="ev-e">{v["nome"]}</div>'
                f'<div class="ev-q">{quota}</div></div></div>')

    def scheda(s):
        colore = ("alta" if s["prob"] >= 0.50 else
                  ("media" if s["prob"] >= 0.25 else "bassa"))
        if s.get("sistema"):
            quota = (f'{s["quota"]:.2f}<span class="q-max">'
                     f'&ndash;{s["quota_max"]:.0f}</span>')
        else:
            quota = f'{s["quota"]:.2f}'
        return (f'<div class="giocata">'
                f'<div class="g-top"><span class="g-tit">{s["titolo"]}</span>'
                f'<span class="g-q">{quota}</span></div>'
                f'<div class="g-nota">{s["nota"]}</div>'
                f'{"".join(riga_voce(v) for v in s["voci"])}'
                + (f'<div class="g-prob {colore}">probabilita\' di vincere '
                   f'almeno una combinazione: <b>{s["prob"]*100:.1f}%</b>'
                   f'</div></div>' if s.get("sistema") else
                   f'<div class="g-prob {colore}">probabilita\' che esca '
                   f'tutto: <b>{s["prob"]*100:.1f}%</b></div></div>'))

    # --- riepilogo di come sono andate -----------------------------
    rend = rendimento_schedine()
    if rend:
        totale = {"n": 0, "vinte": 0, "ritorno": 0.0, "attesa": 0.0}
        righe_r = []
        for categoria in ("alta", "valore", "sistemi", "miste"):
            d = rend.get(categoria)
            if not d or d["n"] < 1:
                continue
            for k in totale:
                totale[k] += d[k]
            resa = d["ritorno"] / d["n"] * 100
            righe_r.append(
                f'<tr><td>{categoria}</td><td>{d["n"]}</td>'
                f'<td>{d["vinte"]}</td>'
                f'<td>{d["vinte"]/d["n"]*100:.0f}%</td>'
                f'<td>{d["attesa"]/d["n"]*100:.0f}%</td>'
                f'<td class="{"pos" if resa > 0 else "neg"}">{resa:+.0f}%</td></tr>')
        if totale["n"] >= 5:
            resa_tot = totale["ritorno"] / totale["n"] * 100
            riepilogo = f"""
<div class="riquadro">
 <div class="tit">Come sono andate le proposte</div>
 <div class="grande {"pos" if resa_tot > 0 else "neg"}">{resa_tot:+.0f}%</div>
 <div class="spiega">
  {totale["vinte"]} vinte su {totale["n"]} concluse &middot;
  ci aspettavamo di vincerne il {totale["attesa"]/totale["n"]*100:.0f}%,
  ne sono uscite il {totale["vinte"]/totale["n"]*100:.0f}%
 </div>
 <table class="riep">
  <tr><th>categoria</th><th>gioc.</th><th>vinte</th><th>%</th>
      <th>attesa</th><th>resa</th></tr>
  {"".join(righe_r)}
 </table>
 <div class="spiega">La colonna "attesa" e\' quanto il modello diceva di
  vincere, "%" quanto e\' uscito davvero: se i due numeri sono vicini le
  probabilita\' sono oneste. La "resa" e\' il guadagno a puntata costante.
  Con poche giocate oscilla moltissimo.</div>
</div>"""
        else:
            riepilogo = (f'<div class="riquadro"><div class="tit">Come sono '
                         f'andate le proposte</div><div class="grande">'
                         f'{totale["n"]} concluse</div><div class="spiega">'
                         f'Troppo poche per dire qualcosa. Il conto cresce '
                         f'ogni giorno.</div></div>')
    else:
        riepilogo = ('<div class="riquadro"><div class="tit">Come sono andate '
                     'le proposte</div><div class="grande">in attesa</div>'
                     '<div class="spiega">Nessuna proposta ancora conclusa. '
                     'Il riepilogo comparira\' qui appena le prime partite '
                     'saranno giocate.</div></div>')

    sezioni = []

    if proposte["singole"]:
        voci = []
        for v in proposte["singole"]:
            voci.append(
                f'<div class="giocata"><div class="g-top">'
                f'<span class="g-tit">{v["nome"]}</span>'
                f'<span class="g-q">{v["quota"]:.2f}</span></div>'
                f'{riga_voce(v)}'
                f'<div class="g-prob {"alta" if v["prob"]>=0.5 else "media"}">'
                f'noi <b>{v["prob"]*100:.0f}%</b> &middot; '
                f'mercato {v["mercato"]*100:.0f}% &middot; '
                f'vantaggio stimato <b>{v["vantaggio"]*100:+.0f}%</b></div></div>')
        sezioni.append(('Singole consigliate',
                        'Le tre giocate con il miglior rapporto fra la nostra '
                        'probabilita\' e la quota offerta.', voci))

    for chiave, titolo, spiegazione in (
            ("alta", "Alta probabilita\'",
             "Esiti molto probabili, uno per partita. Quote basse ma buone "
             "possibilita\' che escano tutti."),
            ("valore", "Valore atteso",
             "Solo esiti dove il modello stima piu\' probabilita\' di quanta "
             "ne implichi la quota."),
            ("sistemi", "Sistemi integrali",
             "Piu\' esiti sulla stessa partita. Il sistema genera tutte le "
             "combinazioni prendendone uno per partita: per vincere "
             "qualcosa basta che in ogni partita almeno uno si avveri."),
            ("miste", "Miste",
             "Eventi sicuri piu\' uno rischioso, per alzare la quota senza "
             "affidarsi solo a quello.")):
        if proposte[chiave]:
            sezioni.append((titolo, spiegazione,
                            [scheda(s) for s in proposte[chiave]]))

    if not sezioni:
        corpo = ('<div class="vuoto">Nessuna proposta in questo momento: '
                 'servono partite in programma con dati sufficienti.</div>')
    else:
        corpo = "".join(
            f'<div class="sezione"><h2>{t}</h2>'
            f'<div class="spiegazione">{s}</div>{"".join(v)}</div>'
            for t, s, v in sezioni)

    html = f"""<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Giocate</title>
<style>
 body {{ font-family:-apple-system,system-ui,sans-serif; margin:0; padding:12px;
        background:#f4f5f7; color:#1c2733; }}
 h1 {{ font-size:18px; margin:0 0 4px; }}
 h2 {{ font-size:14px; margin:0 0 3px; }}
 .sottotitolo {{ font-size:11px; color:#5b6b7b; margin-bottom:14px; }}
 .sezione {{ margin-bottom:22px; }}
 .spiegazione {{ font-size:11px; color:#5b6b7b; margin-bottom:8px;
                 line-height:1.5; }}
 .giocata {{ background:#fff; border-radius:6px; padding:10px 11px;
             margin-bottom:8px; }}
 .g-top {{ display:flex; justify-content:space-between; align-items:baseline;
           border-bottom:1px solid #eef1f4; padding-bottom:6px; }}
 .g-tit {{ font-size:12px; font-weight:600; }}
 .g-q {{ font-size:19px; font-weight:600; color:#1e7d3c; }}
 .q-max {{ font-size:12px; color:#7b8794; font-weight:400; }}
 .g-nota {{ font-size:10px; color:#8b98a5; margin:5px 0 2px; line-height:1.5; }}
 .ev {{ display:flex; justify-content:space-between; align-items:center;
        padding:6px 0; border-bottom:1px solid #f6f8f9; }}
 .ev-p {{ font-size:12px; font-weight:500; }}
 .ev-l {{ font-size:9px; color:#97a3ae; margin-top:1px; }}
 .ev-dx {{ text-align:right; white-space:nowrap; padding-left:8px; }}
 .ev-e {{ font-size:12px; color:#1e7d3c; font-weight:600; }}
 .ev-q {{ font-size:10px; color:#7b8794; }}
 .eq {{ font-size:8px; color:#aeb8c2; margin-left:2px; }}
 .g-prob {{ font-size:11px; margin-top:7px; padding:5px 7px;
            border-radius:4px; }}
 .g-prob.alta {{ background:#e8f2e8; color:#2b6b3f; }}
 .g-prob.media {{ background:#fdf6e3; color:#8a6d1f; }}
 .g-prob.bassa {{ background:#fbebeb; color:#8f2626; }}
 .uff {{ background:#2c3e50; color:#fff; font-size:8px; padding:1px 4px;
         border-radius:2px; margin-left:5px; }}
 .vuoto {{ background:#fff; border-radius:6px; padding:18px; font-size:12px;
           color:#5b6b7b; }}
 .nota {{ margin-top:18px; font-size:10px; color:#7b8794; line-height:1.7; }}
 .riquadro {{ background:#fff; border-radius:6px; padding:12px;
              margin-bottom:18px; }}
 .tit {{ font-size:10px; text-transform:uppercase; letter-spacing:.5px;
         color:#7b8794; margin-bottom:6px; }}
 .grande {{ font-size:24px; font-weight:600; }}
 .grande.pos {{ color:#1e7d3c; }}
 .grande.neg {{ color:#b03030; }}
 .spiega {{ font-size:11px; color:#5b6b7b; margin-top:6px; line-height:1.6; }}
 .riep {{ width:100%; border-collapse:collapse; font-size:11px;
          margin-top:10px; }}
 .riep th {{ text-align:left; color:#8b98a5; font-weight:500;
             border-bottom:1px solid #eef1f4; padding:3px 2px; font-size:9px;
             text-transform:uppercase; }}
 .riep td {{ padding:4px 2px; border-bottom:1px solid #f6f8f9; }}
 .riep .pos {{ color:#1e7d3c; font-weight:600; }}
 .riep .neg {{ color:#b03030; font-weight:600; }}
 a {{ color:#2c3e50; }}
</style></head><body>
<h1>Giocate</h1>
<div class="sottotitolo">
Proposte costruite dal modello &middot;
aggiornate il {generato[:16].replace('T', ' alle ')} UTC
</div>

{riepilogo}

{corpo}

<div class="nota">
<b>Il margine si moltiplica.</b> Su una singola il bookmaker trattiene
circa il 7%. Su una doppia diventa il 14%, su una tripla il 22%, su una
quadrupla oltre il 28%. Ogni evento aggiunto peggiora la posizione di
chi gioca: le multiple sono il prodotto piu\' redditizio per i
bookmaker, non per chi le gioca.<br><br>
<b>Le quote segnate "eq"</b> sono quelle eque, cioe\' 1 diviso la nostra
probabilita\'. Il bookmaker ne offrira\' meno: quelle senza marchio sono
invece quote di mercato vere.<br><br>
<b>La probabilita\' mostrata e\' onesta.</b> Una tripla al 30% esce tre
volte su dieci: sette volte su dieci si perde tutto. Non e\' un difetto
delle proposte, e\' come funzionano le multiple.<br><br>
<b>Ricorda che il confronto col mercato dice ancora "non
distinguibile"</b>: non abbiamo dimostrato di essere migliori delle
quote.<br><br>
<a href="index.html">Tutte le partite</a> &middot;
<a href="selezione.html">Selezione</a> &middot;
<a href="verifica.html">Verifica</a>
</div>
</body></html>"""
    with open(USCITA_GIOCATE, "w", encoding="utf-8") as f:
        f.write(html)
    return sum(len(v) for v in proposte.values())



USCITA_ESATTI = "esatti.html"


def scrivi_esatti(previsioni, generato):
    """
    I cinque risultati esatti piu' probabili di TUTTO il palinsesto.

    E' la parte meno affidabile del modello: i parametri sono stimati
    sull'esito 1X2, non sui singoli punteggi, e non abbiamo mai
    verificato se i risultati esatti siano calibrati.

    L'ordinamento e' per probabilita' pura. Non e' il criterio piu'
    spettacolare - tende a pescare le partite chiuse, dove la
    probabilita' si concentra su pochi punteggi - ma e' l'unico
    verificabile: se diciamo 14% e succede il 14% delle volte, il
    numero e' onesto.
    """
    scelti, disponibili = esatti_correnti(previsioni)
    tutti = range(disponibili)          # serve solo a contare, piu' sotto

    voci = []
    for i, d in enumerate(scelti, 1):
        p = d["p"]
        classe = ("netto" if d["prob"] >= 0.15 else
                  ("medio" if d["prob"] >= 0.11 else "debole"))
        marchio = ('<span class="uff">uff</span>'
                   if p.get("formazioni") == "ufficiale" else '')
        alternativi = " &middot; ".join(
            f'{s["risultato"]} {s["prob"]*100:.1f}%' for s in d["alternativi"])
        voci.append(
            f'<div class="scelta">'
            f'<div class="s-top">'
            f'<span class="s-pos">{i}</span>'
            f'<span class="s-ris {classe}">{d["risultato"]}</span>'
            f'<span class="s-prob">{d["prob"]*100:.1f}%</span></div>'
            f'<div class="s-nome">{p["casa"]} - {p["fuori"]}{marchio}</div>'
            f'<div class="s-lega">{p["campionato"]} &middot; '
            f'{p["data"][8:10]}/{p["data"][5:7]} {p["data"][11:16]} &middot; '
            f'gol attesi {p.get("gol_attesi_casa", 0):.2f} - '
            f'{p.get("gol_attesi_fuori", 0):.2f}</div>'
            f'<div class="s-alt">stacca il secondo di '
            f'<b>{d["distacco"]*100:.1f}</b> punti &middot; '
            f'poi {alternativi}</div>'
            f'</div>')

    if not voci:
        voci = ['<div class="vuoto">Nessuna partita in programma.</div>']

    html = f"""<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Risultati esatti</title>
<style>
 body {{ font-family:-apple-system,system-ui,sans-serif; margin:0; padding:12px;
        background:#f4f5f7; color:#1c2733; }}
 h1 {{ font-size:18px; margin:0 0 4px; }}
 .sottotitolo {{ font-size:11px; color:#5b6b7b; margin-bottom:12px; }}
 .avviso {{ background:#fdf6e3; color:#8a6d1f; font-size:11px; padding:10px;
            border-radius:6px; margin-bottom:14px; line-height:1.6; }}
 .scelta {{ background:#fff; border-radius:6px; padding:11px 12px;
            margin-bottom:9px; }}
 .s-top {{ display:flex; align-items:center; gap:10px; }}
 .s-pos {{ width:20px; height:20px; border-radius:50%; background:#eef1f4;
           color:#7b8794; font-size:11px; text-align:center;
           line-height:20px; flex-shrink:0; }}
 .s-ris {{ font-size:22px; font-weight:600; padding:1px 10px;
           border-radius:5px; }}
 .s-ris.netto {{ background:#e3f2e3; color:#15642f; }}
 .s-ris.medio {{ background:#fdf6e3; color:#8a6d1f; }}
 .s-ris.debole {{ background:#f0f2f4; color:#5b6b7b; }}
 .s-prob {{ margin-left:auto; font-size:17px; font-weight:600; }}
 .s-nome {{ font-size:13px; font-weight:500; margin-top:7px; }}
 .s-lega {{ font-size:10px; color:#97a3ae; margin-top:1px; }}
 .s-alt {{ font-size:10px; color:#7b8794; margin-top:6px;
           padding-top:6px; border-top:1px solid #f2f4f6; }}
 .uff {{ background:#2c3e50; color:#fff; font-size:8px; padding:1px 4px;
         border-radius:2px; margin-left:5px; }}
 .vuoto {{ background:#fff; border-radius:6px; padding:18px; font-size:12px;
           color:#5b6b7b; }}
 .nota {{ margin-top:16px; font-size:10px; color:#7b8794; line-height:1.7; }}
 a {{ color:#2c3e50; }}
</style></head><body>
<h1>Risultati esatti</h1>
<div class="sottotitolo">
I cinque punteggi piu' probabili di tutto il palinsesto &middot;
scelti fra {len(tutti)} partite &middot;
aggiornati il {generato[:16].replace('T', ' alle ')} UTC
</div>

<div class="avviso">
<b>E\' la parte meno affidabile del sistema.</b> I parametri del modello
sono stimati sull\'esito 1X2, non sui singoli punteggi, e non abbiamo
mai verificato se i risultati esatti siano calibrati. Trattali come
un\'indicazione, non come gli altri numeri.
</div>

{''.join(voci)}

<div class="nota">
<b>Perche\' le percentuali sono cosi\' basse.</b> Anche in una partita
molto squilibrata il punteggio piu\' probabile supera raramente il 15%:
i modi in cui puo\' finire una partita sono troppi. Un risultato esatto
al 14% sbaglia quasi nove volte su dieci, ed e\' per questo che ha la
quota piu\' alta di tutti i mercati.<br><br>
<b>Il verde</b> segnala i casi oltre il 15%, rari. Il giallo sta fra
l\'11 e il 15%, il grigio sotto: li\' nessun punteggio emerge davvero.<br><br>
<b>L\'ordinamento e\' per probabilita\' pura</b>, quindi tende a pescare
le partite chiuse, dove pochi punteggi concentrano la probabilita\'. Il
distacco dal secondo dice quanto quel punteggio spicca davvero nella
sua partita.<br><br>
<a href="index.html">Tutte le partite</a> &middot;
<a href="giocate.html">Giocate</a> &middot;
<a href="selezione.html">Selezione</a> &middot;
<a href="verifica.html">Verifica</a>
</div>
</body></html>"""
    with open(USCITA_ESATTI, "w", encoding="utf-8") as f:
        f.write(html)
    return len(scelti)


def main():
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} non trovato.")
        sys.exit(1)
    if not os.path.exists(MODELLO):
        print(f"{MODELLO} non trovato: esegui prima addestra_modello.py")
        sys.exit(1)
    if not API_KEY:
        print("API_FOOTBALL_KEY mancante.")
        sys.exit(1)

    with open(MODELLO, encoding="utf-8") as f:
        mod = json.load(f)
    print(f"Modello addestrato su {mod['partite_addestramento']} partite "
          f"il {mod['generato'][:10]}")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, country || ' - ' || name FROM leagues")
    nomi_lega = dict(cur.fetchall())

    print("Calcolo lo stato attuale delle squadre...")
    stato, formazione = stato_squadre(conn, mod)
    print(f"  squadre pronte: {len(stato)}")

    coef = mod["coefficienti"]
    std = mod["standardizzazione"]
    indicatori = mod["indicatori"]

    def zeta(nome, valore):
        if valore is None or nome not in std:
            return 0.0
        m, s = std[nome]
        return (valore - m) / s if s > 0 else 0.0

    def valore_indicatore(nome, s, casa, form):
        if nome == "possesso":
            return s["possesso"]
        if nome == "falli":
            return s["falli"]
        if nome == "att_gol_ctx":
            return s["att_gol_ctx"].get(1 if casa else 0)
        if nome in ("volatilita_gol", "volatilita_dif",
                    "allenatore_nuovo", "partite_allenatore"):
            return s.get(nome)
        return (form or {}).get(nome)

    def restringi(v, n):
        return 1.0 + (v - 1.0) * n / (n + mod["shrink_k"])

    oggi = datetime.now(timezone.utc).date()
    previsioni = []
    con_formazioni = 0
    print(f"Cerco le partite dei prossimi {GIORNI_AVANTI} giorni...")

    extra = []      # partite dei campionati solo per valore.py, oggi e domani
    info_intervallo = {}   # tutte le altre, solo per l'intervallo (v21.0)
    info_stima = {}        # tutte quelle ammesse all'intervallo, per la stima senza quote (v22.5)
    tipi_lega = tipi_leghe() if CON_QUOTE else {}
    for scarto in range(GIORNI_AVANTI + 1):
        giorno = (oggi + timedelta(days=scarto)).isoformat()
        tutte = chiamata("fixtures", {"date": giorno})
        partite = [p for p in tutte if p["league"]["id"] in nomi_lega]
        if scarto <= 1:
            extra += [{"fixture_id": p["fixture"]["id"], "data": p["fixture"]["date"],
                       "campionato": CAMPIONATI_VALORE[p["league"]["id"]],
                       "casa": p["teams"]["home"]["name"], "fuori": p["teams"]["away"]["name"]}
                      for p in tutte if p["league"]["id"] in CAMPIONATI_VALORE
                      and p["fixture"]["status"]["short"] in ("NS", "TBD")]
            for p in tutte:
                lid = p["league"]["id"]
                if (p["fixture"]["status"]["short"] in ("NS", "TBD")
                        and (lid in nomi_lega or lid in CAMPIONATI_VALORE or ammessa_intervallo(lid, tipi_lega))):
                    info_stima[p["fixture"]["id"]] = {
                        "data": p["fixture"]["date"],
                        "campionato": f"{p['league'].get('country') or ''} - {p['league'].get('name') or ''}",
                        "casa": p["teams"]["home"]["name"], "fuori": p["teams"]["away"]["name"],
                        "lega": lid, "id_casa": p["teams"]["home"]["id"], "id_fuori": p["teams"]["away"]["id"]}
                if (lid in nomi_lega or lid in CAMPIONATI_VALORE
                        or p["fixture"]["status"]["short"] not in ("NS", "TBD")
                        or not ammessa_intervallo(lid, tipi_lega)):
                    continue
                info_intervallo[p["fixture"]["id"]] = {
                    "data": p["fixture"]["date"],
                    "campionato": f"{p['league'].get('country') or ''} - {p['league'].get('name') or ''}",
                    "casa": p["teams"]["home"]["name"], "fuori": p["teams"]["away"]["name"]}
        if not partite:
            continue
        print(f"  {giorno}: {len(partite)} partite")

        for p in partite:
            if p["fixture"]["status"]["short"] not in ("NS", "TBD"):
                continue
            idc, idf = p["teams"]["home"]["id"], p["teams"]["away"]["id"]
            sc, sf = stato.get(idc), stato.get(idf)
            if not sc or not sf:
                continue
            fid_p = p["fixture"]["id"]
            fc = formazione.get((fid_p, idc))
            ff = formazione.get((fid_p, idf))

            # ogni campionato ha il suo fattore campo
            f_lega = mod.get("fattore_campo_lega", {}).get(
                str(p["league"]["id"]), mod["fattore_campo"])
            lc = mod["media_gol"] * restringi(sc["att"], sc["n"]) * \
                 restringi(sf["dif"], sf["n"]) * math.sqrt(f_lega)
            lf = mod["media_gol"] * restringi(sf["att"], sf["n"]) * \
                 restringi(sc["dif"], sc["n"]) / math.sqrt(f_lega)

            ec = ef = 0.0
            usati = []
            for nome in indicatori:
                a, b = coef[nome]
                vc = zeta(nome, valore_indicatore(nome, sc, True, fc))
                vf = zeta(nome, valore_indicatore(nome, sf, False, ff))
                ec += a * vc + b * vf
                ef += a * vf + b * vc
                if vc != 0.0 or vf != 0.0:
                    usati.append(nome)

            ec, ef = max(-1.5, min(1.5, ec)), max(-1.5, min(1.5, ef))
            lc = max(0.15, min(5.0, lc * math.exp(ec)))
            lf = max(0.15, min(5.0, lf * math.exp(ef)))

            tipi = {v["tipo"] for v in (fc, ff) if v}
            if "ufficiale" in tipi:
                stato_form = "ufficiale"
            elif "probabile" in tipi:
                stato_form = "probabile"
            else:
                stato_form = "nessuna"
            if stato_form != "nessuna":
                con_formazioni += 1

            mk = mercati(matrice(lc, lf, mod["rho"]))
            aff, aff_etichetta = affidabilita(sc, sf, fc, ff)
            previsioni.append({
                "fixture_id": p["fixture"]["id"],
                "data": p["fixture"]["date"],
                "campionato": nomi_lega.get(p["league"]["id"], ""),
                "casa": p["teams"]["home"]["name"],
                "fuori": p["teams"]["away"]["name"],
                "gol_attesi_casa": round(lc, 2),
                "gol_attesi_fuori": round(lf, 2),
                "gol_attesi_totali": round(lc + lf, 2),
                "nettezza": nettezza(mk["1"], mk["X"], mk["2"]),
                "affidabilita": aff,
                "affidabilita_etichetta": aff_etichetta,
                "partite_storia_casa": sc["n"],
                "partite_storia_fuori": sf["n"],
                "formazioni": stato_form,
                "mercati": {k: (round(v, 4) if isinstance(v, float) else v)
                            for k, v in mk.items()},
            })

    # versione precedente: serve a mostrare quanto hanno spostato le
    # formazioni ufficiali rispetto alla stima del mattino
    try:
        conn2 = sqlite3.connect(DB_PATH)
        c2 = conn2.cursor()
        c2.execute("""SELECT fixture_id, p1, px, p2, gol_attesi_casa, gol_attesi_fuori
                      FROM archivio_versioni WHERE tipo IN ('probabile','nessuna')""")
        prima = {r[0]: r[1:] for r in c2.fetchall()}
        conn2.close()
        for p in previsioni:
            if p.get("formazioni") == "ufficiale" and p["fixture_id"] in prima:
                v = prima[p["fixture_id"]]
                m = p["mercati"]
                p["prima"] = {"1": round(v[0], 4), "X": round(v[1], 4),
                              "2": round(v[2], 4),
                              "attesi": f"{v[3]:.2f}-{v[4]:.2f}" if v[3] else None}
                p["spostamento"] = round(
                    max(abs(m[k] - v[i]) for i, k in enumerate(("1", "X", "2"))), 4)
    except sqlite3.OperationalError:
        pass

    if extra:
        print(f"  campionati solo per le giocate di valore: {len(extra)} partite fra oggi e domani")
    if CON_QUOTE and previsioni:
        print("Scarico le quote per il confronto...")
        if frena("le quote per il confronto"):
            quote = {}
        else:
            quote = scarica_quote(giorni_da_scaricare(previsioni)
                                  | {e["data"][:10] for e in extra},
                                  {p["fixture_id"] for p in previsioni}, extra, info_intervallo, info_stima)
        quote = ricorda_quote(quote, {p["fixture_id"] for p in previsioni})
        for p in previsioni:
            q = quote.get(p["fixture_id"])
            if not q:
                continue
            m = p["mercati"]
            p["mercato"] = {k: round(q[k], 4) for k in ("1", "X", "2")}
            p["mercato"]["margine"] = round(q["margine"], 4)
            p["mercato"]["bookmaker"] = q["bookmaker"]
            p["divergenza"] = {k: round(m[k] - q[k], 4) for k in ("1", "X", "2")}
            p["divergenza_max"] = round(
                max(abs(v) for v in p["divergenza"].values()), 4)

    previsioni.sort(key=lambda x: x["data"])
    conn.close()

    generato = datetime.now(timezone.utc).isoformat()
    with open(USCITA_JSON, "w", encoding="utf-8") as f:
        json.dump({"generato": generato, "modello": mod["generato"],
                   "previsioni": previsioni}, f, ensure_ascii=False, indent=1)
    scrivi_html(previsioni, generato, mod)
    n_scelte = scrivi_selezione(previsioni, generato) if VALORE_ATTIVO else 0
    n_giocate = scrivi_giocate(previsioni, generato, mod["rho"])
    n_esatti = scrivi_esatti(previsioni, generato)

    print(f"\nPrevisioni prodotte: {len(previsioni)}")
    print(f"  di cui con formazioni (probabili o ufficiali): {con_formazioni}")
    print(f"  {USCITA_JSON}\n  {USCITA_HTML}"
          f"\n  {USCITA_SELEZIONE} ({n_scelte} esiti selezionati)"
          f"\n  {USCITA_GIOCATE} ({n_giocate} proposte)"
          f"\n  {USCITA_ESATTI} ({n_esatti} risultati)")


def scrivi_html(previsioni, generato, mod):
    """
    Pagina leggibile dal telefono: tabella compatta, e ogni partita si
    apre con un tocco mostrando tutti i mercati e i due indici.
    """
    blocchi, lega_corrente = [], None
    for p in previsioni:
        if p["campionato"] != lega_corrente:
            lega_corrente = p["campionato"]
            blocchi.append(f'<div class="lega">{lega_corrente}</div>')

        m = p["mercati"]
        massimo = max(m["1"], m["X"], m["2"])
        def cella(v):
            return f'<span class="q{" forte" if v == massimo else ""}">{v*100:.0f}</span>'

        tipo = p.get("formazioni", "nessuna")
        if tipo == "ufficiale":
            marchio = '<span class="uff">UFF</span>'
        elif tipo == "probabile":
            marchio = '<span class="prob">prob</span>'
        else:
            marchio = ''

        aff = p.get("affidabilita_etichetta", "bassa")
        net = p.get("nettezza", 0)
        punteggi = " &middot; ".join(
            f'{s["risultato"]} <b>{s["prob"]*100:.0f}%</b>'
            for s in m.get("punteggi_probabili", []))

        def riga(etichetta, coppie):
            celle = "".join(
                f'<div class="m"><span class="e">{n}</span>'
                f'<span class="v">{v*100:.0f}%</span></div>' for n, v in coppie)
            return f'<div class="gruppo"><div class="tit">{etichetta}</div>{celle}</div>'

        prima = p.get("prima")
        if prima:
            def confronta(et):
                return (f'<div class="cf"><span class="e">{et}</span>'
                        f'<span class="n">{prima[et]*100:.0f}%</span>'
                        f'<span class="mk">{m[et]*100:.0f}%</span>'
                        f'<span class="dv {"piu" if m[et] > prima[et] else ("meno" if m[et] < prima[et] else "pari")}">'
                        f'{(m[et]-prima[et])*100:+.0f}</span></div>')
            blocco_prima = (
                '<div class="gruppo"><div class="tit">'
                'Prima e dopo le formazioni ufficiali</div>'
                '<div class="cf intestazione"><span class="e"></span>'
                '<span class="n">stimate</span><span class="mk">ufficiali</span>'
                '<span class="dv">scarto</span></div>'
                + "".join(confronta(x) for x in ("1", "X", "2")) +
                f'<div class="spiega">spostamento massimo '
                f'{p.get("spostamento", 0)*100:.0f} punti</div></div>')
        else:
            blocco_prima = ''

        mercato = p.get("mercato")
        div = p.get("divergenza") or {}
        if mercato:
            def confronto(et):
                n, mv, d = m[et], mercato[et], div.get(et, 0)
                segno = "piu" if d > 0 else ("meno" if d < 0 else "pari")
                return (f'<div class="cf"><span class="e">{et}</span>'
                        f'<span class="n">{n*100:.0f}%</span>'
                        f'<span class="mk">{mv*100:.0f}%</span>'
                        f'<span class="dv {segno}">{d*100:+.0f}</span></div>')
            dmax = p.get("divergenza_max", 0)
            if dmax >= 0.15:
                avviso = ('<div class="avviso">Divergenza forte. Nei nostri test '
                          'le grandi divergenze sono piu\' spesso un errore nostro '
                          'che un\'intuizione: il modello ha poco storico e '
                          'sottostima le squadre nettamente superiori.</div>')
            else:
                avviso = ''
            blocco_mercato = (
                f'<div class="gruppo"><div class="tit">Confronto con i bookmaker</div>'
                f'<div class="cf intestazione"><span class="e"></span>'
                f'<span class="n">noi</span><span class="mk">mercato</span>'
                f'<span class="dv">scarto</span></div>'
                + "".join(confronto(x) for x in ("1", "X", "2")) +
                f'<div class="spiega">medie di {mercato["bookmaker"]} bookmaker, '
                f'margine del {mercato["margine"]*100:.1f}% gia\' tolto</div>'
                f'{avviso}</div>')
        else:
            blocco_mercato = ''

        dettaglio = (
            riga("Esito finale", [("1", m["1"]), ("X", m["X"]), ("2", m["2"])]) +
            riga("Doppia chance", [("1X", m["1X"]), ("12", m["12"]), ("X2", m["X2"])]) +
            riga("Totale gol", [("Over 1.5", m["over15"]), ("Under 1.5", m["under15"]),
                                ("Over 2.5", m["over25"]), ("Under 2.5", m["under25"]),
                                ("Over 3.5", m["over35"]), ("Under 3.5", m["under35"])]) +
            riga("Entrambe a segno", [("Gol", m["gol_gol"]), ("NoGol", m["no_gol"])]) +
            f'<div class="gruppo"><div class="tit">Risultati piu\' probabili</div>'
            f'<div class="punteggi">{punteggi}</div></div>'
            + blocco_prima + blocco_mercato +
            f'<div class="indici">'
            f'<div><span class="e">Nettezza</span> <b>{net:.0f}</b>/100'
            f'<div class="spiega">quanto il pronostico e\' sbilanciato</div></div>'
            f'<div><span class="e">Affidabilita\'</span> <b>{p.get("affidabilita",0):.0f}</b>/100'
            f' <span class="tag {aff}">{aff}</span>'
            f'<div class="spiega">storico: {p.get("partite_storia_casa","?")} e '
            f'{p.get("partite_storia_fuori","?")} partite &middot; '
            f'formazioni: {tipo}</div></div>'
            f'<div><span class="e">Gol attesi</span> '
            f'<b>{p["gol_attesi_casa"]:.2f} - {p["gol_attesi_fuori"]:.2f}</b>'
            f'<div class="spiega">totale {p.get("gol_attesi_totali", 0):.2f}</div></div>'
            f'</div>')

        blocchi.append(
            f'<details><summary>'
            f'<span class="ora">{p["data"][11:16]}</span>'
            f'<span class="squadre">{p["casa"]} - {p["fuori"]}{marchio}</span>'
            f'<span class="quote">{cella(m["1"])}{cella(m["X"])}{cella(m["2"])}</span>'
            f'</summary><div class="dett">{dettaglio}</div></details>')

    html = f"""<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Previsioni calcistiche</title>
<style>
 * {{ box-sizing: border-box; }}
 body {{ font-family: -apple-system, system-ui, sans-serif; margin:0; padding:10px;
        background:#f4f5f7; color:#1c2733; }}
 h1 {{ font-size:18px; margin:0 0 3px; }}
 .info {{ font-size:11px; color:#5b6b7b; margin-bottom:12px; line-height:1.6; }}
 .lega {{ background:#2c3e50; color:#fff; padding:6px 10px; font-size:11px;
          font-weight:600; margin-top:10px; border-radius:4px 4px 0 0; }}
 details {{ background:#fff; border-bottom:1px solid #e4e8ec; }}
 summary {{ display:flex; align-items:center; gap:8px; padding:9px 10px;
            cursor:pointer; list-style:none; font-size:13px; }}
 summary::-webkit-details-marker {{ display:none; }}
 .ora {{ font-size:11px; color:#7b8794; min-width:38px; }}
 .squadre {{ flex:1; font-weight:500; }}
 .quote {{ display:flex; gap:3px; }}
 .q {{ min-width:30px; text-align:center; font-size:12px; padding:2px 3px;
       border-radius:3px; background:#eef1f4; }}
 .q.forte {{ background:#1e7d3c; color:#fff; font-weight:600; }}
 .dett {{ padding:4px 10px 12px; border-top:1px solid #eef1f4; }}
 .gruppo {{ margin-top:10px; }}
 .tit {{ font-size:10px; text-transform:uppercase; letter-spacing:.5px;
         color:#7b8794; margin-bottom:4px; }}
 .m {{ display:inline-block; background:#f4f6f8; border-radius:4px;
       padding:4px 8px; margin:0 4px 4px 0; font-size:12px; }}
 .m .e {{ color:#5b6b7b; margin-right:5px; }}
 .m .v {{ font-weight:600; }}
 .punteggi {{ font-size:12px; }}
 .cf {{ display:flex; align-items:center; font-size:12px; padding:3px 0;
        border-bottom:1px solid #f2f4f6; }}
 .cf .e {{ width:26px; color:#5b6b7b; font-weight:600; }}
 .cf .n, .cf .mk {{ width:56px; text-align:right; }}
 .cf .dv {{ width:52px; text-align:right; font-weight:600; }}
 .cf.intestazione {{ font-size:9px; text-transform:uppercase; color:#8b98a5;
                     border-bottom:1px solid #dde3e8; font-weight:400; }}
 .dv.piu {{ color:#1e7d3c; }}
 .dv.meno {{ color:#b03030; }}
 .dv.pari {{ color:#8b98a5; }}
 .avviso {{ margin-top:6px; font-size:10px; color:#8a6d1f; background:#fdf6e3;
            padding:6px 8px; border-radius:4px; line-height:1.5; }}
 .indici {{ margin-top:12px; padding-top:10px; border-top:1px dashed #dde3e8;
            display:flex; flex-wrap:wrap; gap:14px; font-size:12px; }}
 .indici .e {{ color:#5b6b7b; }}
 .spiega {{ font-size:10px; color:#8b98a5; margin-top:2px; }}
 .tag {{ font-size:9px; padding:1px 5px; border-radius:8px; color:#fff; }}
 .tag.alta {{ background:#1e7d3c; }}
 .tag.media {{ background:#b8860b; }}
 .tag.bassa {{ background:#94a3ae; }}
 .uff {{ background:#2c3e50; color:#fff; font-size:8px; padding:1px 4px;
         border-radius:2px; margin-left:5px; }}
 .prob {{ background:#aeb8c2; color:#fff; font-size:8px; padding:1px 4px;
          border-radius:2px; margin-left:5px; }}
 .nota {{ margin-top:16px; font-size:10px; color:#7b8794; line-height:1.7; }}
 .collegamento {{ display:inline-block; margin-top:6px; padding:5px 10px;
                  background:#2c3e50; color:#fff; border-radius:4px;
                  text-decoration:none; font-size:11px; }}
</style></head><body>
<h1>Previsioni calcistiche</h1>
<div class="info">
Aggiornate il {generato[:16].replace('T', ' alle ')} UTC &middot;
{len(previsioni)} partite &middot; modello su {mod['partite_addestramento']} partite<br>
<span class="uff">UFF</span> formazioni ufficiali &middot;
<span class="prob">prob</span> formazioni probabili &middot;
tocca una partita per il dettaglio<br>
<a href="selezione.html" class="collegamento">Selezione</a>
<a href="giocate.html" class="collegamento">Giocate</a>
<a href="esatti.html" class="collegamento">Risultati esatti</a>
</div>
{''.join(blocchi)}
<div class="nota">
Modello statistico (Poisson con correzione Dixon-Coles) su risultati,
expected goals e formazioni. Nella verifica storica ha battuto le
frequenze medie di circa il 4%: margine reale ma modesto, inferiore a
quello dei bookmaker.<br><br>
Sono probabilita', non pronostici. Un esito dato al 60% si verifica sei
volte su dieci, e quattro volte no. La <b>nettezza</b> dice quanto il
pronostico e' sbilanciato, l'<b>affidabilita'</b> quanto sono solidi i
dati che lo sostengono: sono cose diverse e vanno lette insieme.
</div>
</body></html>"""
    with open(USCITA_HTML, "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    main()
