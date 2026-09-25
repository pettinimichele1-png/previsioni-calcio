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

# Una partita senza risultato dopo tre giorni e' rinviata o persa per
# strada: la schedina che la contiene viene annullata, invece di restare
# in sospeso per sempre e bloccare i conteggi.
GIORNI_ANNULLAMENTO = 3


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

# I gruppi cosi' come li vede chi apre la scheda della partita.
# Lasciano fuori quello che l'app mostra gia' piu' in alto: esito
# finale, Over/Under principali, Gol/NoGol.
#
# La forma dice come disegnarli, riusando quello che c'e' gia':
#   "coppie"  -> la barra a due teste della sezione Gol. Gli esiti
#                vanno a due a due e devono essere l'uno il contrario
#                dell'altro, cosi' la barra e' piena e le due
#                percentuali fanno cento.
#   "tessere" -> i riquadri dei risultati esatti, per gli esiti che
#                non hanno un contrario (i multigol si sovrappongono
#                fra loro, le combinazioni pure).
#
# Nei titoli {casa} e {fuori} vengono sostituiti dai nomi veri delle
# due squadre, cosi' dentro al gruppo le etichette restano corte.
GRUPPI_APP = [
    ("Multigol", "tessere",
     [("mg_0_1", "0-1"), ("mg_1_2", "1-2"), ("mg_1_3", "1-3"),
      ("mg_1_4", "1-4"), ("mg_2_3", "2-3"), ("mg_2_4", "2-4"),
      ("mg_2_5", "2-5"), ("mg_3_4", "3-4"), ("mg_3_5", "3-5"),
      ("mg_3_6", "3-6")]),

    ("Gol di {casa}", "coppie",
     [("casa_segna", "Segna"), ("casa_nonsegna", "Non segna"),
      ("casa_over15", "Over 1.5"), ("casa_under15", "Under 1.5"),
      ("casa_over25", "Over 2.5"), ("casa_under25", "Under 2.5")]),

    ("Gol di {fuori}", "coppie",
     [("fuori_segna", "Segna"), ("fuori_nonsegna", "Non segna"),
      ("fuori_over15", "Over 1.5"), ("fuori_under15", "Under 1.5"),
      ("fuori_over25", "Over 2.5"), ("fuori_under25", "Under 2.5")]),

    ("Multigol di squadra", "tessere",
     [("casa_mg_1_2", "Casa 1-2"), ("casa_mg_1_3", "Casa 1-3"),
      ("fuori_mg_1_2", "Osp. 1-2"), ("fuori_mg_1_3", "Osp. 1-3")]),

    ("Scarto e handicap", "tessere",
     [("casa_2plus", "Casa -2"), ("casa_3plus", "Casa -3"),
      ("casa_h1", "Casa +1"), ("fuori_2plus", "Osp. -2"),
      ("fuori_3plus", "Osp. -3"), ("fuori_h1", "Osp. +1")]),

    ("Combinazioni", "tessere",
     [("1+over25", "1+O2.5"), ("1+under25", "1+U2.5"),
      ("X+over25", "X+O2.5"), ("X+under25", "X+U2.5"),
      ("2+over25", "2+O2.5"), ("2+under25", "2+U2.5"),
      ("1X+over25", "1X+O2.5"), ("1X+under25", "1X+U2.5"),
      ("X2+over25", "X2+O2.5"), ("X2+under25", "X2+U2.5"),
      ("12+over25", "12+O2.5"), ("12+under25", "12+U2.5"),
      ("1+over15", "1+O1.5"), ("2+over15", "2+O1.5"),
      ("1X+over15", "1X+O1.5"), ("X2+over15", "X2+O1.5"),
      ("1+over35", "1+O3.5"), ("2+over35", "2+O3.5"),
      ("1+gol", "1+Gol"), ("1+nogol", "1+NoGol"),
      ("X+gol", "X+Gol"), ("2+gol", "2+Gol"), ("2+nogol", "2+NoGol"),
      ("1X+gol", "1X+Gol"), ("1X+nogol", "1X+NoGol"),
      ("X2+gol", "X2+Gol"), ("X2+nogol", "X2+NoGol"),
      ("12+gol", "12+Gol"), ("12+nogol", "12+NoGol"),
      ("1+mg_1_3", "1+MG1-3"), ("2+mg_1_3", "2+MG1-3"),
      ("1X+mg_1_3", "1X+MG1-3"), ("X2+mg_1_3", "X2+MG1-3"),
      ("1+mg_2_4", "1+MG2-4"), ("2+mg_2_4", "2+MG2-4")]),

    ("Altri totali", "coppie",
     [("over05", "Over 0.5"), ("under05", "Under 0.5"),
      ("over45", "Over 4.5"), ("under45", "Under 4.5"),
      ("pari", "Pari"), ("dispari", "Dispari")]),
]


def vocabolario_mercati():
    """
    Titoli, forma ed etichette dei gruppi, scritti una volta sola in
    cima al file invece che dentro ogni partita. Con 300 partite e 70
    mercati l'uno, ripeterli costerebbe quasi un mega di traffico a
    ogni aggiornamento: il telefono scarica app.json ogni mezz'ora.
    """
    return [{"t": titolo, "f": forma, "n": [e for _, e in voci],
             "lunghi": [P.NOMI.get(k, k) for k, _ in voci],
             # i codici interni: servono all'app per chiudere da sola le
             # giocate registrate su questi mercati
             "k": [k for k, _ in voci]}
            for titolo, forma, voci in GRUPPI_APP]


def altri_mercati(m):
    """
    Le probabilita', nello stesso ordine del vocabolario. Sono numeri
    interi per mille (264 vuol dire 26,4%): bastano per la percentuale
    e per la quota equa, e occupano un decimo dello spazio. Sotto lo
    0,5% si lascia perdere: sono esiti che nessun bookmaker quota.
    """
    return [[(round(m[k] * 1000) if m.get(k, 0) >= 0.005 else 0)
             for k, _ in voci]
            for _, _, voci in GRUPPI_APP]


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
        # Gli altri mercati giocabili, gia' raggruppati e con
        # l'etichetta pronta: l'app li mostra e basta.
        "altri": altri_mercati(m),
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
    # quote medie dei bookmaker (1X2, Over/Under 2.5, Gol/NoGol), con i
    # nomi degli esiti usati dall'app: il registro le propone gia' scritte
    q = (mk or {}).get("quote") or {}
    rinomina = {"gol_gol": "gol", "no_gol": "nogol"}
    fuori["quote"] = {rinomina.get(k, k): v for k, v in q.items()} or None
    return fuori


# ---------------------------------------------------------------
#  RISULTATI RECENTI, per chiudere da sole le giocate registrate
# ---------------------------------------------------------------

GIORNI_RISULTATI = 21
FINITE = ("FT", "AET", "PEN")
ANNULLATE = ("PST", "CANC", "ABD", "AWD", "WO")


def risultati_per_app(conn):
    """
    Le partite delle ultime tre settimane gia' concluse, come
    {id: [gol casa, gol ospiti]}, e quelle rinviate o annullate come
    {id: null}. Il registro delle giocate vive sul telefono: con questi
    numeri l'app capisce da sola se una giocata e' vinta o persa.
    Si usano i gol dei 90 minuti piu' eventuali supplementari, come nel
    resto del sistema; per i campionati non cambia nulla.
    """
    limite = (datetime.now(FUSO) - timedelta(days=GIORNI_RISULTATI)).date().isoformat()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, goals_home, goals_away, status FROM fixtures "
                    "WHERE date >= ?", (limite,))
        righe = cur.fetchall()
    except sqlite3.OperationalError:
        return {}
    fuori = {}
    for fid, gc, ga, stato in righe:
        if stato in FINITE and gc is not None and ga is not None:
            fuori[str(fid)] = [gc, ga]
        elif stato in ANNULLATE:
            fuori[str(fid)] = None
    return fuori


# ---------------------------------------------------------------
#  GIOCATE
# ---------------------------------------------------------------

def evento_per_app(v):
    return {"partita": f"{v['casa']} – {v['fuori']}", "info": info_partita(v),
            "esito": v.get("nome") or v["esito"], "p": v["prob"]}


def giocate_per_app(previsioni, rho):
    P.RHO_SISTEMI = rho
    proposte, generato = P.giocate_correnti(previsioni)
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
            # la probabilita' "almeno uno" e' quella calcolata al mattino,
            # quando la schedina e' stata proposta
            unione = (s.get("unioni") or {}).get(str(fid))
            if unione is None and per_id.get(fid):
                p = per_id[fid]
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
    fuori["generato"] = generato
    fuori["note"] = proposte.get("note", {})
    return fuori


# ---------------------------------------------------------------
#  RISULTATI ESATTI
# ---------------------------------------------------------------

def esatti_per_app(previsioni):
    """Gli stessi risultati esatti congelati al mattino."""
    scelti, _ = P.esatti_correnti(previsioni)
    return [{"ris": d["risultato"], "p": d["prob"], "distacco": d["distacco"],
             "partita": f"{d['p']['casa']} – {d['p']['fuori']}",
             "info": info_partita(d["p"])} for d in scelti]


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

    limite = datetime.now(FUSO) - timedelta(days=GIORNI_ANNULLAMENTO)
    eventi = {}
    for codice, fid, esito, casa, fuori, gc, ga, data, stato in righe:
        giocata = gc is not None and stato in ("FT", "AET", "PEN")
        try:
            passata = datetime.fromisoformat(data).astimezone(FUSO) < limite
        except (TypeError, ValueError):
            passata = False
        eventi.setdefault(codice, []).append({
            "fid": fid, "esito": esito, "partita": f"{casa} – {fuori}",
            "gc": gc, "ga": ga, "data": data, "giocata": giocata,
            "persa": not giocata and passata})

    ieri = datetime.now(FUSO).date() - timedelta(days=1)
    totale = {"n": 0, "vinte": 0, "attesa": 0.0}
    categorie = {}
    di_ieri = []

    annullate = 0
    for codice, categoria, titolo, quota, prob in schedine:
        ev = eventi.get(codice, [])
        if not ev:
            continue
        if any(e["persa"] for e in ev):
            annullate += 1                 # partita rinviata o mai arrivata
            continue
        if not all(e["giocata"] for e in ev):
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
    totale["annullate"] = annullate
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
    risultati = {}
    if os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        schedine = schedine_per_app(conn)
        risultati = risultati_per_app(conn)
        conn.close()

    app = {
        "generato": datetime.now(FUSO).isoformat(timespec="minutes"),
        "mercati": vocabolario_mercati(),
        "partite": sorted((partita_per_app(p) for p in previsioni),
                          key=lambda x: x["data"]),
        "giocate": giocate_per_app(previsioni, rho),
        "esatti": esatti_per_app(previsioni),
        "verifica": verifica,
        "schedine": schedine,
        "risultati": risultati,
    }
    with open(USCITA, "w", encoding="utf-8") as f:
        json.dump(app, f, ensure_ascii=False, separators=(",", ":"))

    kb = os.path.getsize(USCITA) / 1024
    print(f"{USCITA}: {len(app['partite'])} partite, "
          f"{sum(len(v) for v in app['giocate'].values() if isinstance(v, list))} proposte, "
          f"{schedine['totale']['n']} schedine concluse "
          f"({len(schedine['ieri'])} ieri) - {kb:.0f} KB")


if __name__ == "__main__":
    main()
