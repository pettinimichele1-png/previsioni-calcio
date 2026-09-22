"""
DATI PER L'APP
==============
Raccoglie in un unico file, app.json, tutto quello che l'app mostra:

    partite     le previsioni con tutti i mercati
    giocate     le proposte del giorno, divise per categoria
    esatti      i cinque risultati esatti piu' probabili
    verifica    le statistiche storiche delle partite
    schedine    le statistiche storiche delle schedine e quelle
                concluse il giorno precedente

Non ricalcola le previsioni: legge previsioni.json e verifica.json,
prodotti poco prima dallo stesso pipeline, e il database per lo storico
delle schedine.

LE SCHEDINE CONCLUSE
--------------------
Una schedina e' conclusa quando TUTTE le sue partite hanno un risultato.
Il giorno di conclusione e' quello dell'ultima partita, in ora italiana.

Vale una regola diversa per i sistemi integrali: si vince se in OGNI
partita si avvera almeno uno degli esiti scelti. Per le altre schedine
devono avverarsi tutti.

Nessun dato viene cancellato: l'app mostra solo il giorno precedente,
ma i totali contano tutto lo storico.

USO:
    python scripts/esporta_app.py
"""

import os
import sys
import json
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

CARTELLA = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CARTELLA)
import previsioni as P

DB_PATH = "calcio_dati.db"
PREVISIONI = "previsioni.json"
VERIFICA = "verifica.json"
MODELLO = "modello.json"
USCITA = "app.json"
FUSO = ZoneInfo("Europe/Rome")
SITO = os.environ.get("SITO_DIR", "docs")


def trova(nome):
    """
    Un file prodotto in questo giro sta nella cartella di lavoro; uno
    prodotto in un giro precedente e' gia' stato spostato nel sito. Le
    esecuzioni live, per esempio, non rigenerano la verifica.
    """
    for percorso in (nome, os.path.join(SITO, nome)):
        if os.path.exists(percorso):
            return percorso
    return None

CATEGORIE = ("alta", "valore", "sistemi", "miste")


def giorno_locale(iso):
    """Data italiana di un orario ISO in UTC."""
    try:
        return datetime.fromisoformat(iso).astimezone(FUSO).date()
    except (TypeError, ValueError):
        return None


def titolo_pulito(t):
    """I titoli interni usano l'apostrofo al posto della vocale accentata."""
    return (str(t or "").replace("probabilita'", "probabilità")
            .replace(" - ", " · "))


def nome_esito(e):
    """Da codice interno (under25) a nome leggibile (Under 2.5)."""
    return P.NOMI.get(e, e)


def info_partita(v):
    """Campionato e orario in forma breve, per le righe delle schedine."""
    lega = (v.get("campionato") or "").split(" - ")[-1]
    try:
        quando = datetime.fromisoformat(v["data"]).astimezone(FUSO)
        giorni = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
        return f"{lega} · {giorni[quando.weekday()]} {quando.day}, {quando:%H:%M}"
    except (KeyError, TypeError, ValueError):
        return lega


# ---------------------------------------------------------------
#  PARTITE
# ---------------------------------------------------------------

def partita_per_app(p):
    m = p["mercati"]
    mk = p.get("mercato")
    esatti = [{"r": s["risultato"], "p": s["prob"]}
              for s in (m.get("punteggi_probabili") or [])[:3]]
    fuori = {
        "id": p["fixture_id"],
        "data": p["data"],
        "campionato": p.get("campionato", ""),
        "casa": p["casa"],
        "fuori": p["fuori"],
        "formazioni": p.get("formazioni", "nessuna"),
        "p": {k: m[k] for k in ("1", "X", "2")},
        "dc": {k: m[k] for k in ("1X", "12", "X2")},
        "gol": {k: m[k] for k in ("over15", "under15", "over25",
                                  "under25", "over35", "under35")},
        "gg": {"gol": m["gol_gol"], "nogol": m["no_gol"]},
        "esatti": esatti,
        "attesi": [p.get("gol_attesi_casa"), p.get("gol_attesi_fuori")],
        "affidabilita": p.get("affidabilita"),
        "affidabilita_etichetta": p.get("affidabilita_etichetta"),
        "nettezza": p.get("nettezza"),
        "storia": [p.get("partite_storia_casa"), p.get("partite_storia_fuori")],
        "mercato": None,
        "prima": p.get("prima"),
    }
    if mk and all(k in mk for k in ("1", "X", "2")):
        fuori["mercato"] = {"1": mk["1"], "X": mk["X"], "2": mk["2"],
                            "margine": mk.get("margine"),
                            "bookmaker": mk.get("bookmaker")}
    return fuori


# ---------------------------------------------------------------
#  GIOCATE
# ---------------------------------------------------------------

def evento_per_app(v):
    return {"partita": f"{v['casa']} – {v['fuori']}", "info": info_partita(v),
            "esito": v.get("nome") or v["esito"], "p": v["prob"]}


def giocate_per_app(previsioni, rho):
    P.RHO_SISTEMI = rho
    proposte = P.costruisci_giocate(previsioni)
    per_id = {p["fixture_id"]: p for p in previsioni}
    fuori = {k: [] for k in ("singole",) + CATEGORIE}

    for v in proposte.get("singole", []):
        fuori["singole"].append({
            "titolo": "Singola", "quota": v["quota"], "prob": v["prob"],
            "mercato": v.get("mercato"), "vantaggio": v.get("vantaggio"),
            "eventi": [evento_per_app(v)]})

    for categoria in ("alta", "valore", "miste"):
        for s in proposte.get(categoria, []):
            fuori[categoria].append({
                "titolo": titolo_pulito(s["titolo"]), "quota": s["quota"], "prob": s["prob"],
                "eventi": [evento_per_app(v) for v in s["voci"]]})

    # nei sistemi gli esiti di una stessa partita si raggruppano, e per
    # ognuna si mostra la probabilita' che se ne avveri almeno uno
    for s in proposte.get("sistemi", []):
        gruppi = {}
        for v in s["voci"]:
            gruppi.setdefault(v["fixture_id"], []).append(v)
        eventi = []
        for fid, voci in gruppi.items():
            p = per_id.get(fid)
            unione = None
            if p:
                M = P.matrice(p["gol_attesi_casa"], p["gol_attesi_fuori"], rho)
                unione = P.prob_unione(M, [x["esito"] for x in voci])
            eventi.append({
                "partita": f"{voci[0]['casa']} – {voci[0]['fuori']}",
                "info": info_partita(voci[0]),
                "esito": " · ".join(x.get("nome") or x["esito"] for x in voci),
                "p": unione, "almeno_uno": True})
        fuori["sistemi"].append({
            "titolo": titolo_pulito(s["titolo"]), "quota": s["quota"],
            "quota_max": s.get("quota_max"), "prob": s["prob"],
            "combinazioni": s.get("combinazioni"), "eventi": eventi})
    return fuori


# ---------------------------------------------------------------
#  RISULTATI ESATTI
# ---------------------------------------------------------------

def esatti_per_app(previsioni):
    tutti = []
    for p in previsioni:
        punteggi = p["mercati"].get("punteggi_probabili") or []
        if len(punteggi) < 2:
            continue
        tutti.append({
            "ris": punteggi[0]["risultato"], "p": punteggi[0]["prob"],
            "partita": f"{p['casa']} – {p['fuori']}",
            "info": info_partita(p),
            "distacco": punteggi[0]["prob"] - punteggi[1]["prob"]})
    tutti.sort(key=lambda d: -d["p"])
    return tutti[:5]


# ---------------------------------------------------------------
#  SCHEDINE CONCLUSE
# ---------------------------------------------------------------

def schedine_per_app(conn):
    vuoto = {"totale": {"n": 0, "vinte": 0, "attesa": 0.0},
             "categorie": {}, "ieri": []}
    cur = conn.cursor()
    try:
        cur.execute("SELECT codice, categoria, titolo, quota, prob FROM schedine")
        schedine = cur.fetchall()
        cur.execute("""
            SELECT e.codice, e.fixture_id, e.esito, e.casa, e.fuori,
                   f.goals_home, f.goals_away, f.date, f.status
            FROM schedine_eventi e
            LEFT JOIN fixtures f ON f.id = e.fixture_id
        """)
        righe = cur.fetchall()
    except sqlite3.OperationalError:
        return vuoto

    eventi = {}
    for codice, fid, esito, casa, fuori, gc, ga, data, stato in righe:
        eventi.setdefault(codice, []).append({
            "fid": fid, "esito": esito, "partita": f"{casa} – {fuori}",
            "gc": gc, "ga": ga, "data": data,
            "giocata": gc is not None and stato in ("FT", "AET", "PEN")})

    ieri = datetime.now(FUSO).date() - timedelta(days=1)
    totale = {"n": 0, "vinte": 0, "attesa": 0.0}
    categorie = {}
    di_ieri = []

    for codice, categoria, titolo, quota, prob in schedine:
        ev = eventi.get(codice, [])
        if not ev or not all(e["giocata"] for e in ev):
            continue                       # non ancora conclusa
        for e in ev:
            e["preso"] = bool(P.esito_avvenuto(e["esito"], e["gc"], e["ga"]))
        if categoria == "sistemi":
            per_partita = {}
            for e in ev:
                per_partita.setdefault(e["fid"], []).append(e["preso"])
            vinta = all(any(x) for x in per_partita.values())
        else:
            vinta = all(e["preso"] for e in ev)

        for d in (totale, categorie.setdefault(
                categoria, {"n": 0, "vinte": 0, "attesa": 0.0})):
            d["n"] += 1
            d["vinte"] += 1 if vinta else 0
            d["attesa"] += prob or 0.0

        conclusa = max((giorno_locale(e["data"]) for e in ev
                        if giorno_locale(e["data"])), default=None)
        if conclusa == ieri:
            di_ieri.append({
                "titolo": titolo_pulito(titolo), "categoria": categoria, "quota": quota,
                "prob": prob, "vinta": vinta,
                "eventi": [{"partita": e["partita"], "esito": nome_esito(e["esito"]),
                            "ris": f"{e['gc']}-{e['ga']}", "preso": e["preso"]}
                           for e in ev]})

    for d in [totale] + list(categorie.values()):
        d["attesa"] = d["attesa"] / d["n"] if d["n"] else 0.0
    return {"totale": totale, "categorie": categorie, "ieri": di_ieri}


# ---------------------------------------------------------------

def main():
    file_previsioni = trova(PREVISIONI)
    if not file_previsioni:
        print(f"{PREVISIONI} non trovato: esegui prima previsioni.py")
        sys.exit(1)
    with open(file_previsioni, encoding="utf-8") as f:
        dati = json.load(f)
    previsioni = dati.get("previsioni", [])

    rho = -0.05
    if os.path.exists(MODELLO):
        with open(MODELLO, encoding="utf-8") as f:
            rho = json.load(f).get("rho", rho)

    verifica = None
    file_verifica = trova(VERIFICA)
    if file_verifica:
        with open(file_verifica, encoding="utf-8") as f:
            verifica = json.load(f)

    schedine = {"totale": {"n": 0, "vinte": 0, "attesa": 0.0},
                "categorie": {}, "ieri": []}
    if os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        schedine = schedine_per_app(conn)
        conn.close()

    app = {
        "generato": datetime.now(FUSO).isoformat(timespec="minutes"),
        "partite": sorted((partita_per_app(p) for p in previsioni),
                          key=lambda x: x["data"]),
        "giocate": giocate_per_app(previsioni, rho),
        "esatti": esatti_per_app(previsioni),
        "verifica": verifica,
        "schedine": schedine,
    }
    with open(USCITA, "w", encoding="utf-8") as f:
        json.dump(app, f, ensure_ascii=False, separators=(",", ":"))

    kb = os.path.getsize(USCITA) / 1024
    print(f"{USCITA}: {len(app['partite'])} partite, "
          f"{sum(len(v) for v in app['giocate'].values())} proposte, "
          f"{schedine['totale']['n']} schedine concluse "
          f"({len(schedine['ieri'])} ieri) - {kb:.0f} KB")


if __name__ == "__main__":
    main()
