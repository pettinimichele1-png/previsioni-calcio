#!/usr/bin/env python3
"""
GIOCATE DI VALORE

Si gioca solo quando un bookmaker paga un esito piu' di quanto vale.
Quanto vale lo dicono le quote di Pinnacle, le piu' precise del mondo,
tolto il loro margine. Se per Pinnacle un esito vale 1.80 e un
bookmaker lo paga 1.90, quella giocata guadagna in media piu' del 5%.
Tutte le altre si saltano. Niente modelli e niente pronostici: solo il
confronto fra due prezzi.

Ogni giorno:
  - le giocate di valore di oggi, al massimo una per partita sul
    risultato finale e una sul primo o secondo tempo, con bookmaker,
    quota e QUOTA MINIMA: se sul tuo sito la trovi almeno a quella
    quota, la giocata ha valore anche li';
  - la schedina del giorno, se su uno stesso bookmaker ci sono almeno
    due giocate di valore.

PROVA SULLA CARTA
Le prime quattro settimane non si mettono soldi: ogni giocata viene
registrata e poi verificata da sola. "bilancio" dice come sta andando e
soprattutto se le quote prese battono quelle finali di Pinnacle (il
CLV): e' il segnale piu' rapido e affidabile che il vantaggio e' vero.

Uso, dalla cartella del progetto:
    source ~/.previsioni_env
    python3 scripts/valore.py              le giocate di adesso
    python3 scripts/valore.py bilancio     come sta andando la prova
    python3 scripts/valore.py mercati      (controllo) i mercati che l'API passa

Per farlo girare da solo, una riga in crontab (crontab -e):
    20 * * * * cd ~/previsioni-calcio/previsioni-calcio && . ~/.previsioni_env && python3 scripts/valore.py auto >> ~/valore.log 2>&1
Parte ogni ora ma lavora solo alle 7, 10, 13, 16, 18 e 20 ora italiana.
Al primo giro del mattino chiude le giocate di ieri, sceglie quelle di
oggi e manda la notifica al telefono; i giri dopo aggiornano le quote
di Pinnacle, che servono per il CLV.

Primo e secondo tempo: dove Pinnacle quota il mercato vale la sua quota;
dove no (secondo tempo, Gol nei tempi, tempo con piu' gol...) il prezzo
giusto si ricava dalle sue quote finali con quote_giuste.py, corretto con
stato/calibrazione_mercati.json (lo crea, una volta, calibra_mercati.py).
Su questi si chiede piu' margine: 5% invece del 3%.

Costa una chiamata all'API per ogni partita ancora da giocare, a ogni giro.
Variabili facoltative: VANTAGGIO_MIN (0.03), VANTAGGIO_MIN_RICAVATI
(0.05), PUNTATA (10 euro a giocata, solo per contare il bilancio in euro).
"""

import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import quote_giuste as Q     # per i mercati dei tempi che Pinnacle non quota
except Exception:
    Q = None

BASE_URL = "https://v3.football.api-sports.io"
API_KEY = os.environ.get("API_FOOTBALL_KEY", "").strip()
VANTAGGIO_MIN = float(os.environ.get("VANTAGGIO_MIN", "0.03"))
VANTAGGIO_MAX = 0.15     # sopra e' quasi sempre una quota vecchia o sbagliata
# sui mercati ricavati il prezzo giusto e' una stima nostra: serve piu' margine
VANTAGGIO_MIN_RICAVATI = float(os.environ.get("VANTAGGIO_MIN_RICAVATI", "0.05"))
PUNTATA = float(os.environ.get("PUNTATA", "10"))
QUOTA_GIUSTA_MIN, QUOTA_GIUSTA_MAX = 1.25, 3.00   # niente sfavorite
MAX_SCHEDINA = 3
GIORNI_PROVA = 28
MIN_VERDETTO = 100
ORE_AUTO = (7, 10, 13, 16, 18, 20)
FUSO = ZoneInfo("Europe/Rome")

# Bookmaker con sito italiano (ADM) fra quelli che l'API passa, con la
# commissione sulla vincita. Betfair e' trattato come exchange: se fosse
# il suo sportsbook, perdiamo qualche giocata ma non ne inventiamo.
ITALIANI = {"Bet365": 0.0, "William Hill": 0.0, "Betfair": 0.05}

FILE_REGISTRO = os.path.join("stato", "valore_registro.json")
FILE_STATO = os.path.join("stato", "valore_stato.json")
GIORNI = ["dom", "lun", "mar", "mer", "gio", "ven", "sab"]

# I mercati confrontati, col nome dell'API:
# (di chi sono i gol, tipo di mercato, tempo)
MERCATI = {
    "match winner": ("partita", "1x2", "finale"),
    "double chance": ("partita", "doppia", "finale"),
    "goals over/under": ("partita", "linea", "finale"),
    "both teams score": ("partita", "sino", "finale"),
    "total - home": ("casa", "linea", "finale"),
    "total - away": ("fuori", "linea", "finale"),
    "first half winner": ("partita", "1x2", "primo"),
    "goals over/under first half": ("partita", "linea", "primo"),
}


# Mercati dei tempi. Se Pinnacle li quota (di solito solo il primo
# tempo) vale la sua quota; se no, il prezzo giusto si ricava dai gol
# attesi delle sue quote finali, con la correzione misurata su 35.000
# partite da calibra_mercati.py. Nome dell'API -> (tipo, tempo).
TEMPI_API = {
    "first half winner": ("1x2", "_pt"),
    "second half winner": ("1x2", "_st"),
    "double chance - first half": ("doppia", "_pt"),
    "double chance - second half": ("doppia", "_st"),
    "goals over/under first half": ("linea", "_pt"),
    "goals over/under - second half": ("linea", "_st"),
    "both teams score - first half": ("sino", "_pt"),
    "both teams to score - second half": ("sino", "_st"),
    "highest scoring half": ("tempo", ""),
}


# ---------------------------------------------------------------
#  utilita'
# ---------------------------------------------------------------

def chiama(endpoint, parametri):
    url = f"{BASE_URL}/{endpoint}?" + urllib.parse.urlencode(parametri)
    req = urllib.request.Request(url, headers={"x-apisports-key": API_KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def carica(percorso, vuoto):
    try:
        with open(percorso, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return vuoto


def salva(percorso, dati):
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    tmp = percorso + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dati, f, ensure_ascii=False, indent=1)
    os.replace(tmp, percorso)


def leggi_data(testo):
    d = datetime.fromisoformat(testo)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def quando(info):
    try:
        return leggi_data(info["data"]).astimezone(FUSO)
    except (KeyError, TypeError, ValueError):
        return None


def trova_previsioni():
    """Il palinsesto con i nomi delle squadre: sta in previsioni.json."""
    sito = os.environ.get("SITO_DIR", "/var/www/previsioni")
    for percorso in ("previsioni.json", os.path.join(sito, "previsioni.json"),
                     "/var/www/previsioni/previsioni.json",
                     os.path.join("docs", "previsioni.json")):
        if os.path.exists(percorso):
            with open(percorso, encoding="utf-8") as f:
                return json.load(f).get("previsioni", [])
    return []


def norm(s):
    return " ".join(str(s or "").lower().split())


def prodotto(valori):
    p = 1.0
    for v in valori:
        p *= v
    return p


def media_ic(valori):
    n = len(valori)
    m = sum(valori) / n
    if n < 2:
        return m, None, None
    es = math.sqrt(sum((v - m) ** 2 for v in valori) / (n - 1) / n)
    return m, m - 1.96 * es, m + 1.96 * es


# ---------------------------------------------------------------
#  quote e prezzo giusto
# ---------------------------------------------------------------

def leggi_quote(voce):
    """{bookmaker: {mercato: {esito: quota}}}, nomi in minuscolo."""
    out = {}
    for b in voce.get("bookmakers") or []:
        libro = {}
        for s in b.get("bets") or []:
            m = norm(s.get("name"))
            if m not in MERCATI and m not in TEMPI_API:
                continue
            for v in s.get("values") or []:
                try:
                    q = float(v.get("odd"))
                except (TypeError, ValueError):
                    continue
                if q > 1.0:
                    libro.setdefault(m, {})[norm(v.get("value"))] = q
        if libro:
            out[b.get("name") or "?"] = libro
    return out


def potenza(quote):
    """
    Toglie il margine: si cerca l'esponente per cui le probabilita'
    implicite elevate a quell'esponente sommano a uno. Il margine pesa
    cosi' di piu' sulle quote alte, come fanno davvero i bookmaker.
    """
    grezze = {k: 1.0 / q for k, q in quote.items()}
    lo, hi = 1.0, 4.0
    for _ in range(45):
        e = (lo + hi) / 2
        if sum(v ** e for v in grezze.values()) > 1.0:
            lo = e
        else:
            hi = e
    e = (lo + hi) / 2
    p = {k: v ** e for k, v in grezze.items()}
    s = sum(p.values())
    return {k: v / s for k, v in p.items()}


def linea_mezza(testo):
    try:
        x = float(testo)
    except ValueError:
        return False
    return abs(x - math.floor(x) - 0.5) < 1e-9


def giuste(pin):
    """{(mercato, esito): probabilita' giusta} per quello che Pinnacle quota."""
    out = {}
    for m, (_, tipo, _) in MERCATI.items():
        v = pin.get(m) or {}
        if tipo == "1x2" and all(k in v for k in ("home", "draw", "away")):
            p = potenza({k: v[k] for k in ("home", "draw", "away")})
            out.update({(m, k): x for k, x in p.items()})
            if m == "match winner":
                out[("double chance", "home/draw")] = p["home"] + p["draw"]
                out[("double chance", "home/away")] = p["home"] + p["away"]
                out[("double chance", "draw/away")] = p["draw"] + p["away"]
        elif tipo == "sino" and "yes" in v and "no" in v:
            p = potenza({"yes": v["yes"], "no": v["no"]})
            out.update({(m, k): x for k, x in p.items()})
        elif tipo == "linea":
            for esito, q in v.items():
                parti = esito.split()
                if len(parti) != 2 or parti[0] != "over" or not linea_mezza(parti[1]):
                    continue
                sotto = v.get("under " + parti[1])
                if sotto:
                    p = potenza({"o": q, "u": sotto})
                    out[(m, esito)] = p["o"]
                    out[(m, "under " + parti[1])] = p["u"]
    return out


def quota_netta(q, commissione):
    return 1 + (q - 1) * (1 - commissione)


def chiave_tempi(m, esito):
    """La nostra chiave (quote_giuste.py) per un esito di un mercato dei tempi."""
    tipo, tempo = TEMPI_API[m]
    if tipo == "tempo":
        return {"1st half": "pt_piu_gol", "first half": "pt_piu_gol",
                "2nd half": "st_piu_gol", "second half": "st_piu_gol",
                "draw": "tempi_pari_gol", "equal": "tempi_pari_gol"}.get(esito)
    k = None
    if tipo == "1x2":
        k = {"home": "1", "draw": "X", "away": "2"}.get(esito)
    elif tipo == "doppia":
        k = {"home/draw": "1X", "home/away": "12", "draw/away": "X2"}.get(esito)
    elif tipo == "sino":
        k = {"yes": "gol_gol", "no": "no_gol"}.get(esito)
    else:
        parti = esito.split()
        if len(parti) == 2 and parti[0] in ("over", "under") and linea_mezza(parti[1]):
            k = f"{parti[0]}{int(float(parti[1]))}5"
    return k + tempo if k else None


def ricavate(pin, cal):
    """Le probabilita' giuste, corrette, di tutti i mercati dei tempi,
    ricavate dall'esito e dall'Over/Under di Pinnacle. {} se non si puo'."""
    if Q is None or not cal:
        return {}
    mw, ou = pin.get("match winner") or {}, pin.get("goals over/under") or {}
    if not all(k in mw for k in ("home", "draw", "away")):
        return {}
    for linea in ("2.5", "3.5", "1.5"):
        if f"over {linea}" in ou and f"under {linea}" in ou:
            break
    else:
        return {}
    f = potenza({k: mw[k] for k in ("home", "draw", "away")})
    fo = potenza({"o": ou[f"over {linea}"], "u": ou[f"under {linea}"]})["o"]
    grezze, _ = Q.tutte(f["home"], f["draw"], f["away"], fo, linea=float(linea),
                        quota_pt=cal.get("quota_primo_tempo"))
    prob = Q.correggi(grezze, cal)
    return {k: prob[k] for k in Q.TEMPI
            if (cal.get("mercati", {}).get(k) or {}).get("affidabile")}


def valuta(libri, m, esito, p, soglia):
    """Chi paga l'esito almeno la soglia sopra il giusto: {bookmaker: quota}."""
    pagano = {}
    for nome, comm in ITALIANI.items():
        q = ((libri.get(nome) or {}).get(m) or {}).get(esito)
        if q and soglia <= p * quota_netta(q, comm) - 1 <= VANTAGGIO_MAX:
            pagano[nome] = q
    return pagano


def candidate(voce, cal=None):
    """
    Il prezzo giusto di ogni esito e le giocate di valore di una
    partita. (None, {}, []) se Pinnacle non la quota.
    """
    libri = leggi_quote(voce)
    pin = libri.get("Pinnacle")
    if not pin:
        return None, {}, []
    g = giuste(pin)
    rp = ricavate(pin, cal)
    trovate = []
    # mercati dei tempi che Pinnacle non quota: prezzo ricavato
    visti = set()
    for nome in ITALIANI:
        for m in TEMPI_API:
            for esito in ((libri.get(nome) or {}).get(m) or {}):
                if (m, esito) in g or (m, esito) in visti:
                    continue
                visti.add((m, esito))
                k = chiave_tempi(m, esito)
                p = rp.get(k)
                if not p or not QUOTA_GIUSTA_MIN <= 1 / p <= QUOTA_GIUSTA_MAX:
                    continue
                pagano = valuta(libri, m, esito, p, VANTAGGIO_MIN_RICAVATI)
                if pagano:
                    libro = max(pagano, key=lambda n: quota_netta(pagano[n], ITALIANI[n]))
                    q = pagano[libro]
                    trovate.append({
                        "mercato": m, "scelta": esito, "chiave": k, "prob": p, "giusta": 1 / p,
                        "book": libro, "quota": q, "commissione": ITALIANI[libro],
                        "vantaggio": p * quota_netta(q, ITALIANI[libro]) - 1,
                        "minima": math.ceil((1 + VANTAGGIO_MIN_RICAVATI) / p * 100 - 1e-9) / 100,
                        "soglia": VANTAGGIO_MIN_RICAVATI, "pagano": pagano})
    for (m, esito), p in g.items():
        if not QUOTA_GIUSTA_MIN <= 1 / p <= QUOTA_GIUSTA_MAX:
            continue
        pagano = valuta(libri, m, esito, p, VANTAGGIO_MIN)
        if not pagano:
            continue
        libro = max(pagano, key=lambda n: quota_netta(pagano[n], ITALIANI[n]))
        q = pagano[libro]
        trovate.append({
            "mercato": m, "scelta": esito, "prob": p, "giusta": 1 / p,
            "book": libro, "quota": q, "commissione": ITALIANI[libro],
            "vantaggio": p * quota_netta(q, ITALIANI[libro]) - 1,
            "minima": math.ceil((1 + VANTAGGIO_MIN) / p * 100 - 1e-9) / 100,
            "soglia": VANTAGGIO_MIN, "pagano": pagano})
    return g, rp, trovate


# ---------------------------------------------------------------
#  nomi e verifica
# ---------------------------------------------------------------

def etichetta(m, esito, casa="Casa", fuori="Ospite", breve=False):
    chi, tipo, tempo = MERCATI[m]
    pt = " 1T" if (breve and tempo == "primo") else (" primo tempo" if tempo == "primo" else "")
    if tipo == "1x2":
        if breve:
            return {"home": "1", "draw": "X", "away": "2"}[esito] + pt
        return {"home": f"1 ({casa})", "draw": "X", "away": f"2 ({fuori})"}[esito] + pt
    if tipo == "doppia":
        return {"home/draw": "1X", "home/away": "12", "draw/away": "X2"}[esito]
    if tipo == "sino":
        return "Gol" if esito == "yes" else "NoGol"
    verso, linea = esito.split()
    di = {"casa": f"{casa} ", "fuori": f"{fuori} "}.get(chi, "")
    return f"{di}{'Over' if verso == 'over' else 'Under'} {linea}{pt}"


def nome_giocata(r, casa="Casa", fuori="Ospite", breve=False):
    """Il nome di una giocata, diretta o ricavata."""
    if r.get("chiave") and Q is not None:
        n = Q.nome(r["chiave"])
        return n.replace(" primo tempo", " 1T").replace(" secondo tempo", " 2T") if breve else n
    return etichetta(r["mercato"], r["scelta"], casa, fuori, breve)


def verifica(r, gc, ga, htc, hta):
    if r.get("chiave"):
        return Q.esito(r["chiave"], gc, ga, htc, hta) if Q is not None else None
    return avvenuto(r["mercato"], r["scelta"], gc, ga, htc, hta)


def avvenuto(m, esito, gc, ga, htc=None, hta=None):
    """Se la giocata e' vinta, dato il risultato. None se non si sa."""
    chi, tipo, tempo = MERCATI[m]
    if tempo == "primo":
        if htc is None or hta is None:
            return None
        x, y = htc, hta
    else:
        x, y = gc, ga
    if tipo == "1x2":
        return {"home": x > y, "draw": x == y, "away": x < y}.get(esito)
    if tipo == "doppia":
        return {"home/draw": x >= y, "home/away": x != y, "draw/away": x <= y}.get(esito)
    if tipo == "sino":
        return (x > 0 and y > 0) == (esito == "yes")
    verso, linea = esito.split()
    n = {"casa": x, "fuori": y}.get(chi, x + y)
    return n > float(linea) if verso == "over" else n < float(linea)


def sul_tempo(r):
    return bool(r.get("chiave")) or r["mercato"] in TEMPI_API


def famiglia(r):
    if sul_tempo(r):
        return "primo e secondo tempo"
    tipo = MERCATI[r["mercato"]][1]
    return {"1x2": "esito e doppia chance", "doppia": "esito e doppia chance",
            "sino": "Gol/NoGol"}.get(tipo, "Under/Over")


# ---------------------------------------------------------------
#  chiusura delle giocate finite
# ---------------------------------------------------------------

def chiudi(registro, conta):
    adesso = datetime.now(timezone.utc)
    aperte = [r for r in registro if r["tipo"] == "singola" and r["esito"] is None
              and leggi_data(r["data"]) < adesso - timedelta(hours=2, minutes=30)]
    ids = sorted({r["fixture_id"] for r in aperte})
    risultati = {}
    for i in range(0, len(ids), 20):
        try:
            dati = chiama("fixtures", {"ids": "-".join(str(x) for x in ids[i:i + 20])})
        except Exception as e:
            print(f"  errore dall'API: {e}")
            break
        conta[0] += 1
        for f in dati.get("response") or []:
            stato = ((f.get("fixture") or {}).get("status") or {}).get("short")
            punt = f.get("score") or {}
            ft, ht = punt.get("fulltime") or {}, punt.get("halftime") or {}
            if stato in ("FT", "AET", "PEN") and ft.get("home") is not None:
                risultati[f["fixture"]["id"]] = (ft["home"], ft["away"],
                                                 ht.get("home"), ht.get("away"))
            elif stato in ("PST", "CANC", "ABD", "AWD", "WO"):
                risultati[f["fixture"]["id"]] = "annullata"
        time.sleep(0.3)
    for r in aperte:
        x = risultati.get(r["fixture_id"])
        if x == "annullata":
            r["esito"] = "annullata"
        elif x:
            e = verifica(r, *x)
            if e is not None:
                r["esito"] = "vinta" if e else "persa"
                r["risultato"] = f"{x[0]}-{x[1]}" + (f" ({x[2]}-{x[3]})" if x[2] is not None else "")

    singole = {r["id"]: r for r in registro if r["tipo"] == "singola"}
    for s in registro:
        if s["tipo"] != "schedina" or s["esito"] is not None:
            continue
        voci = [(singole.get(v["id"]), v["quota"]) for v in s["voci"]]
        esiti = [g["esito"] if g else "annullata" for g, _ in voci]
        if "persa" in esiti:
            s["esito"] = "persa"
        elif None not in esiti:
            valide = [q for (g, q), e in zip(voci, esiti) if e == "vinta"]
            s["esito"] = "vinta" if valide else "annullata"
            s["quota_finale"] = round(prodotto(valide), 2) if valide else None


# ---------------------------------------------------------------
#  il giro: quote, giocate, registro, schedina
# ---------------------------------------------------------------

def schedina_del_giorno(giocate):
    """Le giocate di valore giocabili insieme sullo stesso bookmaker (non
    sull'exchange): si sceglie quello che ne ha di piu', e fra quelle le
    piu' probabili."""
    migliore = None
    for libro, comm in ITALIANI.items():
        if comm:
            continue
        voci, partite = [], set()
        for c in sorted((c for c in giocate if libro in c["pagano"]), key=lambda c: -c["prob"]):
            if c["info"]["fixture_id"] not in partite and len(voci) < MAX_SCHEDINA:
                voci.append(c)
                partite.add(c["info"]["fixture_id"])
        if len(voci) < 2:
            continue
        quota = prodotto(c["pagano"][libro] for c in voci)
        prob = prodotto(c["prob"] for c in voci)
        if migliore is None or (len(voci), prob * quota) > (len(migliore[1]), migliore[3] * migliore[2]):
            migliore = (libro, voci, quota, prob)
    return migliore


def giro(notifica=False):
    adesso = datetime.now(FUSO)
    oggi = adesso.date()
    registro = carica(FILE_REGISTRO, [])
    stato = carica(FILE_STATO, {})
    stato.setdefault("inizio", oggi.isoformat())
    conta = [0]
    cal = Q.carica_calibrazione() if Q is not None else None

    chiudi(registro, conta)

    tutte = trova_previsioni()
    if not tutte:
        print("  previsioni.json non trovato: lancialo dalla cartella del progetto.")
        return
    palinsesto = {p["fixture_id"]: p for p in tutte
                  if quando(p) and quando(p).date() == oggi
                  and quando(p) > adesso + timedelta(minutes=5)}
    in_attesa = {r["fixture_id"] for r in registro if r["tipo"] == "singola"
                 and r["esito"] is None and leggi_data(r["data"]) > adesso}
    giocate, senza_pinnacle, guasto = [], 0, False
    for fid in sorted(set(palinsesto) | in_attesa):
        try:
            dati = chiama("odds", {"fixture": fid})
        except Exception as e:
            print(f"  errore dall'API: {e}")
            guasto = True
            break
        conta[0] += 1
        time.sleep(0.3)
        if dati.get("errors"):
            print(f"  l'API risponde: {dati['errors']}")
            guasto = True
            break
        voci = dati.get("response") or []
        if not voci:
            continue
        g, rp, trovate = candidate(voci[0], cal)
        if g is None:
            senza_pinnacle += fid in palinsesto
            continue
        # il prezzo giusto piu' recente, per il CLV delle giocate gia' fatte
        for r in registro:
            if r["tipo"] != "singola" or r["fixture_id"] != fid or r["esito"] is not None:
                continue
            p = rp.get(r["chiave"]) if r.get("chiave") else g.get((r["mercato"], r["scelta"]))
            if p:
                r["pinnacle_ultima"] = round(1 / p, 3)
                r["aggiornata"] = adesso.isoformat(timespec="minutes")
        # per ogni partita al massimo una giocata sulla partita intera e una
        # sui tempi: la migliore, contando il margine che ognuna richiede
        if fid in palinsesto:
            for sui_tempi in (False, True):
                scelte = [t for t in trovate if sul_tempo(t) == sui_tempi]
                if not scelte:
                    continue
                c = max(scelte, key=lambda t: t["vantaggio"] - t["soglia"])
                c["info"] = palinsesto[fid]
                c["id"] = f"{fid}|{c['mercato']}|{c['scelta']}"
                giocate.append(c)
    giocate.sort(key=lambda c: (quando(c["info"]), c["info"]["fixture_id"], sul_tempo(c)))

    # registro: ogni giocata una volta sola, alla quota del momento in cui e' comparsa
    gia = {r["id"] for r in registro}
    nuove = 0
    for c in giocate:
        if c["id"] in gia:
            continue
        info = c["info"]
        registro.append({
            "id": c["id"], "tipo": "singola", "fixture_id": info["fixture_id"],
            "data": info["data"], "partita": f"{info.get('casa', '?')} - {info.get('fuori', '?')}",
            "campionato": info.get("campionato", ""),
            "mercato": c["mercato"], "scelta": c["scelta"], "chiave": c.get("chiave"),
            "nome": nome_giocata(c, info.get("casa"), info.get("fuori")),
            "book": c["book"], "quota": c["quota"], "commissione": c["commissione"],
            "giusta": round(c["giusta"], 3), "minima": c["minima"],
            "vantaggio": round(c["vantaggio"], 4), "prob": round(c["prob"], 4),
            "registrata": adesso.isoformat(timespec="minutes"),
            "pinnacle_ultima": None, "esito": None})
        nuove += 1

    chiave = f"schedina|{oggi.isoformat()}"
    schedina = next((r for r in registro if r["id"] == chiave), None)
    if schedina is None and adesso.hour >= 7:
        s = schedina_del_giorno(giocate)
        if s:
            libro, voci, quota, prob = s
            schedina = {"id": chiave, "tipo": "schedina", "giorno": oggi.isoformat(),
                        "book": libro, "quota": round(quota, 2), "prob": round(prob, 4),
                        "voci": [{"id": c["id"], "quota": c["pagano"][libro],
                                  "nome": f"{c['info'].get('casa', '?')} - {c['info'].get('fuori', '?')}: "
                                          + nome_giocata(c, c["info"].get("casa"),
                                                         c["info"].get("fuori"), breve=True)}
                                 for c in voci],
                        "data": max(c["info"]["data"] for c in voci), "esito": None}
            registro.append(schedina)

    salva(FILE_REGISTRO, registro)
    stampa_giro(adesso, stato, giocate, schedina, nuove, senza_pinnacle, len(palinsesto), conta[0])
    if not cal:
        print("  Mercati dei tempi ricavati spenti: manca stato/calibrazione_mercati.json.")
        print("  Si crea una volta con: python3 scripts/calibra_mercati.py")

    # la notifica del mattino, una volta al giorno; se l'API ha dato problemi
    # non si manda un "nessuna giocata" falso: ci riprova il giro dopo
    if notifica and not guasto and stato.get("notificato") != oggi.isoformat():
        print("  " + manda_notifica(giocate, schedina, riassunto_ieri(registro, oggi)))
        stato["notificato"] = oggi.isoformat()
    stato["ultimo_giro"] = adesso.isoformat(timespec="minutes")
    salva(FILE_STATO, stato)
    print("  " + scrivi_app(registro, stato))


def giorno_prova(stato):
    inizio = datetime.fromisoformat(stato.get("inizio")).date()
    return (datetime.now(FUSO).date() - inizio).days + 1


def stampa_giro(adesso, stato, giocate, schedina, nuove, senza_pinnacle, n_partite, chiamate):
    g = giorno_prova(stato)
    prova = f"prova sulla carta: giorno {g} di {GIORNI_PROVA}" if g <= GIORNI_PROVA else ""
    print("=" * 92)
    print(f"  GIOCATE DI VALORE   {GIORNI[(adesso.weekday() + 1) % 7]} {adesso:%d/%m ore %H:%M}"
          f"{'':>10}{prova}")
    print("=" * 92)
    print("  Solo dove un bookmaker paga piu' di quanto l'esito vale secondo Pinnacle.")
    print("  QUOTA MINIMA: gioca solo se sul tuo sito la trovi almeno a quella quota.\n")
    if not giocate:
        print("  Adesso nessuna giocata di valore: oggi, per ora, si salta.\n")
    ultima = None
    for c in giocate:
        info, d = c["info"], quando(c["info"])
        extra = " (exchange, al netto della commissione)" if c["commissione"] else ""
        if info["fixture_id"] != ultima:
            print(f"  {d:%H:%M}  {info.get('casa', '?')} - {info.get('fuori', '?')}"
                  f"   [{info.get('campionato', '')}]")
            ultima = info["fixture_id"]
        print(f"         {nome_giocata(c, info.get('casa'), info.get('fuori')):<30}"
              f" {c['book']} {c['quota']:.2f}{extra}   giusta {c['giusta']:.2f}"
              f"   vantaggio {c['vantaggio']:+.1%}   QUOTA MINIMA {c['minima']:.2f}")
    if schedina:
        print("\n  SCHEDINA DEL GIORNO, da giocare su " + schedina["book"])
        for v in schedina["voci"]:
            print(f"         {v['nome']:<52} {v['quota']:.2f}")
        print(f"         quota {schedina['quota']:.2f}   vince {schedina['prob']:.0%} delle volte"
              f"   vantaggio {schedina['prob'] * schedina['quota'] - 1:+.1%}")
    print("-" * 92)
    print(f"  partite di oggi ancora da giocare: {n_partite}   senza quote di Pinnacle: "
          f"{senza_pinnacle}   chiamate all'API: {chiamate}")
    print(f"  registrate sulla carta: {nuove} nuove.   Come va: python3 scripts/valore.py bilancio")
    print("  Le quote dell'API sono dei siti internazionali e possono essere vecchie di")
    print("  qualche ora: sul sito italiano conta solo la quota minima.")


# ---------------------------------------------------------------
#  notifica al telefono (usa quelle gia' attive dell'app)
# ---------------------------------------------------------------

def riassunto_ieri(registro, oggi):
    ieri = oggi - timedelta(days=1)
    chiuse = [r for r in registro if r["tipo"] == "singola" and r["esito"] in ("vinta", "persa")
              and leggi_data(r["data"]).astimezone(FUSO).date() == ieri]
    if not chiuse:
        return ""
    utile = sum(utile_singola(r) for r in chiuse) * PUNTATA
    vinte = sum(1 for r in chiuse if r["esito"] == "vinta")
    return f"Ieri {vinte} su {len(chiuse)}, {utile:+.2f} euro sulla carta"


def manda_notifica(giocate, schedina, ieri):
    if giocate:
        n = len(giocate)
        titolo = f"Valore oggi: {n} giocat{'a' if n == 1 else 'e'}"
        pezzi = [f"{c['info'].get('casa', '?')}-{c['info'].get('fuori', '?')}: "
                 f"{nome_giocata(c, c['info'].get('casa'), c['info'].get('fuori'), breve=True)}"
                 f" ({c['book']} {c['quota']:.2f}, minima {c['minima']:.2f})"
                 for c in giocate[:3]]
        testo = "; ".join(pezzi) + (f" e altre {n - 3}" if n > 3 else "")
        if schedina:
            testo += f". Schedina {schedina['quota']:.2f} su {schedina['book']}"
    else:
        titolo = "Valore oggi: nessuna giocata"
        testo = "Nessun bookmaker paga piu' del giusto: oggi si salta"
    if ieri:
        testo += f". {ieri}"
    dati = {"titolo": titolo, "testo": testo + ".", "url": "./#/valore", "tag": "valore"}
    try:
        import notifiche as N
        if not os.path.exists(N.CHIAVE_PRIVATA):
            return "notifica non inviata: notifiche non configurate sul server"
        conn = N.db()
        ok, _, errori = N.invia_a_tutti(conn, N.carica_chiave(), dati)
        conn.close()
        return f"notifica consegnata a {ok} telefon{'o' if ok == 1 else 'i'}" + (
            f", errori {errori}" if errori else "")
    except Exception as e:
        return f"notifica non inviata ({e})"


# ---------------------------------------------------------------
#  bilancio
# ---------------------------------------------------------------

def utile_singola(r):
    if r["esito"] == "vinta":
        return quota_netta(r["quota"], r.get("commissione", 0)) - 1
    return -1.0 if r["esito"] == "persa" else 0.0


FAMIGLIE = ("esito e doppia chance", "Under/Over", "Gol/NoGol", "primo e secondo tempo")


def utile_schedina(r):
    return (r["quota_finale"] - 1) if r["esito"] == "vinta" else -1.0


def riassunto(gruppo, utile=utile_singola):
    if not gruppo:
        return {"n": 0}
    utili = [utile(r) for r in gruppo]
    m, lo, hi = media_ic(utili)
    return {"n": len(gruppo), "vinte": sum(1 for r in gruppo if r["esito"] == "vinta"),
            "attese": round(sum(r["prob"] for r in gruppo), 1), "utile": round(sum(utili), 2),
            "rendimento": round(m, 4), "lo": None if lo is None else round(lo, 4),
            "hi": None if hi is None else round(hi, 4)}


def statistiche(registro):
    """Tutti i numeri della prova: per il bilancio a terminale e per l'app."""
    adesso = datetime.now(timezone.utc)
    singole = [r for r in registro if r["tipo"] == "singola" and r["esito"] in ("vinta", "persa")]
    schedine = [r for r in registro if r["tipo"] == "schedina" and r["esito"] in ("vinta", "persa")]
    # il CLV si conta solo a partita iniziata: prima la quota di Pinnacle puo' ancora muoversi
    con_clv = [r for r in registro if r["tipo"] == "singola" and r.get("pinnacle_ultima")
               and r["esito"] != "annullata" and leggi_data(r["data"]) < adesso]
    clv, verdetto = {"n": 0}, "presto"
    if con_clv:
        valori = [quota_netta(r["quota"], r.get("commissione", 0)) / r["pinnacle_ultima"] - 1
                  for r in con_clv]
        m, lo, hi = media_ic(valori)
        clv = {"n": len(valori), "media": round(m, 4),
               "lo": None if lo is None else round(lo, 4), "hi": None if hi is None else round(hi, 4)}
        if len(valori) >= MIN_VERDETTO:
            verdetto = "vero" if lo > 0 else ("no" if hi < 0 else "incerto")
    return {"singole": riassunto(singole),
            "famiglie": {f: riassunto([r for r in singole if famiglia(r) == f]) for f in FAMIGLIE},
            "schedine": riassunto(schedine, utile_schedina),
            "clv": clv, "verdetto": verdetto, "min_verdetto": MIN_VERDETTO,
            "aperte": sum(1 for r in registro if r["tipo"] == "singola" and r["esito"] is None)}


def bilancio():
    registro = carica(FILE_REGISTRO, [])
    stato = carica(FILE_STATO, {})
    if not registro:
        print("  Registro vuoto: prima lancia python3 scripts/valore.py, per qualche giorno.")
        return
    conta = [0]
    chiudi(registro, conta)
    salva(FILE_REGISTRO, registro)
    st = statistiche(registro)

    g = giorno_prova(stato) if stato.get("inizio") else 0
    print("=" * 92)
    print(f"  BILANCIO GIOCATE DI VALORE   ({PUNTATA:.0f} euro a giocata)"
          + (f"          prova sulla carta: giorno {g} di {GIORNI_PROVA}" if 0 < g <= GIORNI_PROVA else ""))
    print("=" * 92)

    def riga(titolo, r):
        if not r["n"]:
            return
        ic = f"  (fra {r['lo']:+.0%} e {r['hi']:+.0%})" if r["lo"] is not None else ""
        print(f"  {titolo:<24} {r['n']:>4} giocat{'a' if r['n'] == 1 else 'e'}  vinte {r['vinte']:>3}"
              f" (attese {r['attese']:>5.1f})  utile {r['utile'] * PUNTATA:>+8.2f} euro"
              f"  rendimento {r['rendimento']:>+6.1%}{ic}")

    if not st["singole"]["n"]:
        print("  Nessuna giocata ancora chiusa.")
    riga("singole", st["singole"])
    for f in FAMIGLIE:
        riga("  " + f, st["famiglie"][f])
    riga("schedine del giorno", st["schedine"])

    c = st["clv"]
    print()
    if c["n"]:
        ic = f" (fra {c['lo']:+.1%} e {c['hi']:+.1%})" if c["lo"] is not None else ""
        print("  CLV, cioe' quanto le quote prese battono l'ultima quota giusta di Pinnacle:")
        print(f"      {c['media']:+.1%}{ic} su {c['n']} giocate")
        print("  Il CLV dice prima del rendimento se il vantaggio e' vero: il rendimento")
        print("  dipende molto dalla fortuna per mesi, il CLV no.\n")
    else:
        print("  CLV: non ancora misurato. Serve che lo script giri piu' volte al giorno")
        print("  (con la riga di crontab), per vedere l'ultima quota di Pinnacle prima della partita.\n")
    print("  VERDETTO: " + {
        "presto": f"ancora presto. Servono almeno {MIN_VERDETTO} giocate con il CLV, ora {c['n']}.",
        "vero": "il vantaggio e' vero. Si puo' passare ai soldi veri, puntata fissa, solo alle\n"
                "  quote minime o sopra.",
        "no": "il vantaggio non c'e'. Con questi bookmaker il metodo non funziona.",
        "incerto": "non ancora chiaro. Si continua sulla carta."}[st["verdetto"]])

    ultime = sorted((r for r in registro if r["tipo"] == "singola" and r["esito"]),
                    key=lambda r: r["data"])[-10:]
    if ultime:
        print("\n  Ultime chiuse:")
        for r in ultime:
            print(f"    {quando(r):%d/%m}  {r['partita'][:30]:<30} {r['nome'][:24]:<24} "
                  f"{r['book']} {r['quota']:.2f}  {r.get('risultato', ''):<11} {r['esito']}")
    print(f"\n  ancora da giocare: {st['aperte']}   chiamate all'API: {conta[0]}")
    print("  " + scrivi_app(registro, stato))


# ---------------------------------------------------------------
#  i dati per l'app (scheda Valore dentro Giocate)
# ---------------------------------------------------------------

def cartella_app():
    sito = os.environ.get("SITO_DIR") or (
        "/var/www/previsioni" if os.path.isdir("/var/www/previsioni") else "docs")
    return os.path.join(sito, "app")


def voce_app(r):
    return {"data": r["data"], "partita": r["partita"], "campionato": r.get("campionato", ""),
            "nome": r["nome"], "book": r["book"], "quota": r["quota"],
            "commissione": r.get("commissione", 0), "giusta": r["giusta"], "minima": r["minima"],
            "vantaggio": r["vantaggio"], "prob": r["prob"], "stimata": bool(r.get("chiave")),
            "esito": r["esito"], "risultato": r.get("risultato")}


def scrivi_app(registro, stato):
    """Scrive valore.json accanto all'app, che lo mostra nella scheda Valore."""
    cartella = cartella_app()
    if not os.path.isdir(cartella):
        return f"app non trovata in {cartella}: valore.json non scritto"
    try:
        oggi = datetime.now(FUSO).date()
        singole = {r["id"]: r for r in registro if r["tipo"] == "singola"}
        di_oggi = sorted((r for r in singole.values()
                          if leggi_data(r["data"]).astimezone(FUSO).date() == oggi),
                         key=lambda r: r["data"])
        s = next((r for r in registro if r["id"] == f"schedina|{oggi.isoformat()}"), None)
        schedina = None
        if s:
            voci = []
            for v in s["voci"]:
                r = singole.get(v["id"]) or {}
                voci.append({"partita": r.get("partita", ""), "nome": r.get("nome", v.get("nome", "")),
                             "quota": v["quota"], "esito": r.get("esito")})
            schedina = {"book": s["book"], "quota": s["quota"], "prob": s["prob"],
                        "esito": s["esito"], "voci": voci}
        chiuse = sorted((r for r in singole.values() if r["esito"] in ("vinta", "persa", "annullata")),
                        key=lambda r: r["data"], reverse=True)[:20]
        salva(os.path.join(cartella, "valore.json"), {
            "generato": datetime.now(FUSO).isoformat(timespec="minutes"),
            "prova": {"giorno": giorno_prova(stato) if stato.get("inizio") else 1, "di": GIORNI_PROVA},
            "puntata": PUNTATA,
            "oggi": [voce_app(r) for r in di_oggi],
            "schedina": schedina,
            "bilancio": statistiche(registro),
            "ultime": [voce_app(r) for r in chiuse]})
        return "app aggiornata: " + os.path.join(cartella, "valore.json")
    except Exception as e:
        return f"valore.json non scritto ({e})"


# ---------------------------------------------------------------

def mercati():
    """Controllo: i mercati che l'API passa per una partita di oggi."""
    oggi = datetime.now(FUSO).date()
    partite = [p for p in trova_previsioni() if quando(p) and quando(p).date() >= oggi]
    for p in sorted(partite, key=quando)[:8]:
        dati = chiama("odds", {"fixture": p["fixture_id"]})
        voci = dati.get("response") or []
        if not voci:
            continue
        print(f"  {p.get('casa')} - {p.get('fuori')}   [{p.get('campionato', '')}]\n")
        for b in voci[0].get("bookmakers") or []:
            nomi = [s.get("name") or "?" for s in b.get("bets") or []]
            letti = [n for n in nomi if norm(n) in MERCATI or norm(n) in TEMPI_API]
            print(f"  {b.get('name')}: {len(nomi)} mercati, confrontati {len(letti)}: {', '.join(letti)}")
        return
    print("  Nessuna partita di oggi o domani con quote.")


def main():
    if not API_KEY:
        print("  Chiave API assente. Prima: source ~/.previsioni_env")
        sys.exit(1)
    comando = sys.argv[1] if len(sys.argv) > 1 else ""
    if comando == "bilancio":
        bilancio()
    elif comando == "mercati":
        mercati()
    elif comando == "auto":
        adesso = datetime.now(FUSO)
        ultimo = carica(FILE_STATO, {}).get("ultimo_giro", "")
        if adesso.hour in ORE_AUTO and not ultimo.startswith(adesso.strftime("%Y-%m-%dT%H")):
            giro(notifica=True)
    else:
        giro()


if __name__ == "__main__":
    main()
