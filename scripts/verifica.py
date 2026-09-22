"""
VERIFICA DELLE PREVISIONI NEL TEMPO
====================================
Finora sapevamo come si comporta il modello sul passato ricostruito.
Questo modulo misura come si comporta sulle partite VERE, quelle previste
prima che si giocassero.

E' anche l'unico modo rigoroso di rispondere alla domanda "abbiamo un
vantaggio sul mercato?": non si guardano le divergenze (quando divergiamo
di solito e' un nostro difetto), si confronta il log loss nostro con
quello delle quote, tolto il margine, sulle stesse partite.

DUE MODALITA'
-------------
    archivia   fotografa le previsioni correnti insieme alle quote del
               momento. Da lanciare dopo previsioni.py.
    report     confronta l'archivio con i risultati arrivati e produce
               le statistiche e la pagina verifica.html

PERCHE' ARCHIVIARE ANCHE LE QUOTE
---------------------------------
Le quote si muovono fino al fischio d'inizio. Confrontarsi con quelle di
oggi su una previsione di tre giorni fa non avrebbe senso: si fotografano
insieme.

IL MARGINE
----------
Le probabilita' implicite in una quota sommano a piu' di 1: la differenza
e' il guadagno del bookmaker (circa il 7%). Va tolto, altrimenti il
mercato sembrerebbe peggiore di quello che e'.

USO:
    python verifica.py archivia
    python verifica.py report
"""

import os
import sys
import json
import time
import math
import random
import sqlite3
import urllib.request
import urllib.parse
from datetime import datetime, timezone

# le quote di Over/Under e Gol/NoGol si leggono come fa previsioni.py
try:
    from previsioni import quote_mercati
except Exception:
    quote_mercati = None

DB_PATH = "calcio_dati.db"
PREVISIONI = "previsioni.json"
BASE_URL = "https://v3.football.api-sports.io"
API_KEY = os.environ.get("API_FOOTBALL_KEY", "").strip()

USCITA_HTML = "verifica.html"
USCITA_JSON = "verifica.json"
SEED = 404
N_BOOTSTRAP = 6000
MAX_CHIAMATE = 200


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


def crea_tabella(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS archivio_previsioni (
            fixture_id INTEGER PRIMARY KEY,
            data TEXT, campionato TEXT, casa TEXT, fuori TEXT,
            p1 REAL, px REAL, p2 REAL,
            over25 REAL, gol_gol REAL,
            gol_attesi_casa REAL, gol_attesi_fuori REAL,
            nettezza REAL, affidabilita REAL, affidabilita_etichetta TEXT,
            formazioni TEXT,
            q1 REAL, qx REAL, q2 REAL, margine REAL, n_bookmaker INTEGER,
            archiviato_il TEXT)
    """)
    # quote degli altri mercati, archiviate da settembre 2026: le partite
    # archiviate prima restano senza e vengono escluse da quel confronto
    colonne = {r[1] for r in conn.execute("PRAGMA table_info(archivio_previsioni)")}
    for nome in ("q_over25", "q_gol", "margine_ou", "margine_gg"):
        if nome not in colonne:
            conn.execute(f"ALTER TABLE archivio_previsioni ADD COLUMN {nome} REAL")
    conn.commit()


def crea_tabella_versioni(conn):
    """
    Conserva una fotografia per ogni VERSIONE della previsione: quella
    fatta al mattino con le formazioni stimate e quella rifatta quando
    escono le ufficiali. Serve a vedere quanto le formazioni vere
    spostano il pronostico, e se lo migliorano.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS archivio_versioni (
            fixture_id INTEGER,
            tipo TEXT,              -- 'nessuna', 'probabile', 'ufficiale'
            data TEXT, campionato TEXT, casa TEXT, fuori TEXT,
            p1 REAL, px REAL, p2 REAL,
            over25 REAL, gol_gol REAL,
            gol_attesi_casa REAL, gol_attesi_fuori REAL,
            nettezza REAL, archiviato_il TEXT,
            PRIMARY KEY (fixture_id, tipo))
    """)
    conn.commit()


def quote_1x2(voce):
    """Probabilita' 1X2 medie tra i bookmaker, tolto il margine."""
    raccolte = []
    for book in voce.get("bookmakers", []) or []:
        for scommessa in book.get("bets", []) or []:
            if (scommessa.get("name") or "").lower() not in (
                    "match winner", "1x2", "fulltime result"):
                continue
            v = {}
            for voce_val in scommessa.get("values", []) or []:
                et = str(voce_val.get("value", "")).strip().lower()
                try:
                    q = float(voce_val.get("odd"))
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
    return (sum(r[0][0] for r in raccolte) / n,
            sum(r[0][1] for r in raccolte) / n,
            sum(r[0][2] for r in raccolte) / n,
            sum(r[1] for r in raccolte) / n, n)


# ---------------------------------------------------------------
def archivia(conn):
    if not os.path.exists(PREVISIONI):
        print(f"{PREVISIONI} non trovato: esegui prima previsioni.py")
        return
    with open(PREVISIONI, encoding="utf-8") as f:
        previsioni = json.load(f)["previsioni"]
    print(f"Previsioni da archiviare: {len(previsioni)}")

    crea_tabella(conn)
    crea_tabella_versioni(conn)
    cur = conn.cursor()

    # ogni versione viene conservata: la prima con le formazioni stimate,
    # e quella rifatta quando escono le ufficiali
    adesso_iso = datetime.now(timezone.utc).isoformat()
    versioni = 0
    for p in previsioni:
        m = p["mercati"]
        tipo = p.get("formazioni", "nessuna")
        cur.execute("SELECT 1 FROM archivio_versioni WHERE fixture_id=? AND tipo=?",
                    (p["fixture_id"], tipo))
        if cur.fetchone() and tipo != "ufficiale":
            continue          # la prima stima non si sovrascrive
        cur.execute("""INSERT OR REPLACE INTO archivio_versioni
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (p["fixture_id"], tipo, p["data"], p["campionato"],
                     p["casa"], p["fuori"], m["1"], m["X"], m["2"],
                     m.get("over25"), m.get("gol_gol"),
                     p.get("gol_attesi_casa"), p.get("gol_attesi_fuori"),
                     p.get("nettezza"), adesso_iso))
        versioni += 1
    conn.commit()
    print(f"  versioni salvate in questo giro: {versioni}")

    cur.execute("SELECT fixture_id FROM archivio_previsioni")
    gia = {r[0] for r in cur.fetchall()}
    nuove = [p for p in previsioni if p["fixture_id"] not in gia]
    print(f"  gia' archiviate: {len(previsioni) - len(nuove)}")
    print(f"  da archiviare:   {len(nuove)}")
    if not nuove:
        return

    quote = {}
    altre = {}
    if API_KEY:
        print("Scarico le quote del momento...")
        chiamate = 0
        for giorno in sorted({p["data"][:10] for p in nuove}):
            pagina = 1
            while chiamate < MAX_CHIAMATE:
                risposta, paging = chiamata("odds", {"date": giorno, "page": pagina})
                chiamate += 1
                if not risposta:
                    break
                for voce in risposta:
                    fid = (voce.get("fixture") or {}).get("id")
                    est = quote_1x2(voce) if fid else None
                    if est:
                        quote[fid] = est
                    if fid and quote_mercati:
                        tutte = quote_mercati(voce) or {}
                        if "over25" in tutte or "gol_gol" in tutte:
                            altre[fid] = tutte
                if pagina >= (paging or {}).get("total", 1):
                    break
                pagina += 1
        print(f"  quote trovate: {len(quote)} (chiamate {chiamate}), "
              f"con Over/Under o Gol/NoGol: {len(altre)}")
    else:
        print("API_FOOTBALL_KEY assente: archivio senza quote.")

    adesso = datetime.now(timezone.utc).isoformat()
    for p in nuove:
        m = p["mercati"]
        q = quote.get(p["fixture_id"])
        a = altre.get(p["fixture_id"]) or {}
        cur.execute("""
            INSERT OR REPLACE INTO archivio_previsioni
            (fixture_id, data, campionato, casa, fuori, p1, px, p2,
             over25, gol_gol, gol_attesi_casa, gol_attesi_fuori,
             nettezza, affidabilita, affidabilita_etichetta, formazioni,
             q1, qx, q2, margine, n_bookmaker, archiviato_il,
             q_over25, q_gol, margine_ou, margine_gg)
            VALUES (?,?,?,?,?, ?,?,?, ?,?, ?,?, ?,?,?, ?, ?,?,?,?,?, ?, ?,?,?,?)
        """, (p["fixture_id"], p["data"], p["campionato"], p["casa"], p["fuori"],
              m["1"], m["X"], m["2"], m.get("over25"), m.get("gol_gol"),
              p.get("gol_attesi_casa"), p.get("gol_attesi_fuori"),
              p.get("nettezza"), p.get("affidabilita"), p.get("affidabilita_etichetta"),
              p.get("formazioni"),
              q[0] if q else None, q[1] if q else None, q[2] if q else None,
              q[3] if q else None, q[4] if q else None, adesso,
              a.get("over25"), a.get("gol_gol"),
              a.get("margine_ou25"), a.get("margine_gg")))
    conn.commit()
    print(f"\nArchiviate {len(nuove)} previsioni.")
    cur.execute("SELECT COUNT(*), COUNT(q1) FROM archivio_previsioni")
    tot, con_quote = cur.fetchone()
    print(f"Archivio: {tot} previsioni, {con_quote} con quote")


# ---------------------------------------------------------------
def perdita(p1, px, p2, esito):
    p = {"1": p1, "X": px, "2": p2}[esito]
    return -math.log(max(p, 1e-15))


def intervallo(v, livello=0.95):
    v = sorted(v)
    return v[int(len(v) * (1 - livello) / 2)], v[int(len(v) * (1 - (1 - livello) / 2))]


def confronto_binario(righe, chiave_noi, chiave_mercato, avvenuto):
    """Log loss nostro e del mercato su un mercato a due esiti (si'/no)."""
    g = [r for r in righe
         if r.get(chiave_mercato) is not None and r.get(chiave_noi) is not None]
    if len(g) < 30:
        return {"partite": len(g)}

    def ll(p, si):
        return -math.log(max(p if si else 1 - p, 1e-15))

    n_q = [ll(r[chiave_noi], avvenuto(r)) for r in g]
    m_q = [ll(r[chiave_mercato], avvenuto(r)) for r in g]
    random.seed(SEED)
    idx = list(range(len(g)))
    diffs = []
    for _ in range(N_BOOTSTRAP):
        c = [random.choice(idx) for _ in idx]
        diffs.append(sum(m_q[i] - n_q[i] for i in c) / len(c))
    lo, hi = intervallo(diffs)
    return {"partite": len(g), "log_loss": sum(n_q) / len(g),
            "log_loss_mercato": sum(m_q) / len(g),
            "vantaggio": sum(diffs) / len(diffs), "intervallo": [lo, hi]}


def report(conn):
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='archivio_previsioni'")
    if not cur.fetchone():
        print("Archivio vuoto: esegui prima 'python verifica.py archivia'")
        return

    cur.execute("""
        SELECT a.*, f.goals_home, f.goals_away
        FROM archivio_previsioni a
        JOIN fixtures f ON f.id = a.fixture_id
        WHERE f.goals_home IS NOT NULL AND f.goals_away IS NOT NULL
          AND f.status IN ('FT','AET','PEN')
        ORDER BY a.data
    """)
    campi = [d[0] for d in cur.description]
    righe = [dict(zip(campi, r)) for r in cur.fetchall()]

    cur.execute("SELECT COUNT(*) FROM archivio_previsioni")
    archiviate = cur.fetchone()[0]
    print(f"Previsioni archiviate: {archiviate}")
    print(f"Con risultato disponibile: {len(righe)}")

    if not righe:
        print("\nNessuna partita verificabile ancora. Servono due passaggi:")
        print("  1. lanciare aggiorna_dati.py per portare i risultati in archivio")
        print("  2. rilanciare questo report")
        return

    for r in righe:
        gc, gf = r["goals_home"], r["goals_away"]
        r["esito"] = "1" if gc > gf else ("X" if gc == gf else "2")

    # ---- confronto con il mercato --------------------------------
    print("\n" + "=" * 70)
    print("ACCURATEZZA COMPLESSIVA")
    print("=" * 70)

    nostre = [perdita(r["p1"], r["px"], r["p2"], r["esito"]) for r in righe]
    azzeccate = sum(1 for r in righe
                    if max((("1", r["p1"]), ("X", r["px"]), ("2", r["p2"])),
                           key=lambda t: t[1])[0] == r["esito"])
    print(f"Partite verificate:      {len(righe)}")
    print(f"Esito piu' probabile azzeccato: {azzeccate} ({azzeccate/len(righe):.1%})")
    print(f"Log loss del modello:    {sum(nostre)/len(nostre):.4f}")

    con_quote = [r for r in righe if r["q1"] is not None]
    if con_quote:
        n_q = [perdita(r["p1"], r["px"], r["p2"], r["esito"]) for r in con_quote]
        m_q = [perdita(r["q1"], r["qx"], r["q2"], r["esito"]) for r in con_quote]
        mn, mm = sum(n_q) / len(n_q), sum(m_q) / len(m_q)
        print("\n" + "=" * 70)
        print("ABBIAMO UN VANTAGGIO SUL MERCATO?")
        print("=" * 70)
        print(f"Partite con quote:  {len(con_quote)}")
        print(f"  log loss nostro:  {mn:.4f}")
        print(f"  log loss mercato: {mm:.4f}   (margine medio tolto: "
              f"{sum(r['margine'] for r in con_quote)/len(con_quote)*100:.1f}%)")

        random.seed(SEED)
        idx = list(range(len(con_quote)))
        diffs = []
        for _ in range(N_BOOTSTRAP):
            c = [random.choice(idx) for _ in idx]
            diffs.append(sum(m_q[i] - n_q[i] for i in c) / len(c))
        media = sum(diffs) / len(diffs)
        lo, hi = intervallo(diffs)
        print(f"\n  vantaggio nostro:  {media:+.4f}")
        print(f"  intervallo 95%:    [{lo:+.4f}, {hi:+.4f}]")
        if lo > 0:
            print("  ESITO: battiamo il mercato in modo dimostrato.")
        elif hi < 0:
            print("  ESITO: il mercato e' migliore, in modo dimostrato.")
        else:
            print("  ESITO: non distinguibile. Con questo numero di partite")
            print("         non possiamo dire chi e' migliore.")
            dev = math.sqrt(sum((m_q[i]-n_q[i]-media)**2 for i in idx)/max(len(idx)-1,1))
            if abs(media) > 1e-6:
                serve = (1.96 * dev / abs(media)) ** 2
                print(f"         Servirebbero circa {int(serve)} partite "
                      f"(ora {len(con_quote)}).")

    # ---- il vantaggio e' sfruttabile? ----------------------------
    if con_quote:
        print("\n" + "=" * 70)
        print("QUANDO DIVERGIAMO DAL MERCATO, CHI HA RAGIONE?")
        print("=" * 70)
        print("Il log loss dice chi e' piu' accurato in generale. Qui guardiamo")
        print("i casi in cui diamo a un esito una probabilita' PIU' ALTA del")
        print("mercato: se in quei casi l'esito si verifica alla frequenza che")
        print("diciamo noi, vediamo qualcosa che il mercato non vede. Se si")
        print("verifica alla frequenza del mercato, la divergenza era rumore.\n")

        casi = []
        for r in con_quote:
            for etichetta, nostro, mercato_p in (
                    ("1", r["p1"], r["q1"]), ("X", r["px"], r["qx"]),
                    ("2", r["p2"], r["q2"])):
                casi.append({"scarto": nostro - mercato_p, "nostro": nostro,
                             "mercato": mercato_p,
                             "avvenuto": 1 if r["esito"] == etichetta else 0})

        print(f"{'divergenza':<22} {'casi':>6} {'noi':>8} {'mercato':>9} "
              f"{'reale':>8} {'chi vince':>12}")
        print("-" * 70)
        for lo_s, hi_s, etichetta in [
                (0.05, 1.0, "diamo molto di piu'"),
                (0.02, 0.05, "diamo un po' di piu'"),
                (-0.02, 0.02, "siamo d'accordo"),
                (-0.05, -0.02, "diamo un po' di meno"),
                (-1.0, -0.05, "diamo molto di meno")]:
            g = [c for c in casi if lo_s <= c["scarto"] < hi_s]
            if len(g) < 20:
                continue
            n_medio = sum(c["nostro"] for c in g) / len(g)
            m_medio = sum(c["mercato"] for c in g) / len(g)
            reale = sum(c["avvenuto"] for c in g) / len(g)
            vicino = "noi" if abs(reale - n_medio) < abs(reale - m_medio) else "mercato"
            print(f"{etichetta:<22} {len(g):>6} {n_medio:>7.1%} {m_medio:>8.1%} "
                  f"{reale:>7.1%} {vicino:>12}")

        # il conto che conta davvero
        sopra = [c for c in casi if c["scarto"] >= 0.05]
        if len(sopra) >= 30:
            reale = sum(c["avvenuto"] for c in sopra) / len(sopra)
            m_medio = sum(c["mercato"] for c in sopra) / len(sopra)
            n_medio = sum(c["nostro"] for c in sopra) / len(sopra)
            print(f"\nSui {len(sopra)} casi in cui diamo almeno 5 punti in piu':")
            print(f"  noi dicevamo {n_medio:.1%}, il mercato {m_medio:.1%}, "
                  f"e' successo il {reale:.1%}")
            # intervallo sulla frequenza reale
            dev = math.sqrt(max(reale * (1 - reale), 1e-9) / len(sopra))
            lo_f, hi_f = reale - 1.96 * dev, reale + 1.96 * dev
            print(f"  intervallo 95% sulla frequenza: [{lo_f:.1%}, {hi_f:.1%}]")
            if lo_f > m_medio:
                print("  -> la frequenza reale supera il mercato: VANTAGGIO REALE")
            elif hi_f < m_medio:
                print("  -> la frequenza reale sta sotto il mercato: la nostra")
                print("     divergenza e' sistematicamente sbagliata")
            else:
                print("  -> l'intervallo contiene la stima del mercato: con questi")
                print("     dati non si dimostra alcun vantaggio")
            margine_medio = sum(r["margine"] for r in con_quote) / len(con_quote)
            print(f"\n  Nota: il margine dei bookmaker e' {margine_medio*100:.1f}%.")
            print("  Per un vantaggio utilizzabile la frequenza reale dovrebbe")
            print(f"  superare il mercato di piu' di quella soglia, non solo di poco.")
        else:
            print("\nTroppo pochi casi di forte divergenza per concludere.")

    # ---- formazioni probabili contro ufficiali --------------------
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='archivio_versioni'")
    if cur.fetchone():
        cur.execute("""
            SELECT pr.fixture_id, pr.casa, pr.fuori,
                   pr.p1, pr.px, pr.p2, uf.p1, uf.px, uf.p2,
                   f.goals_home, f.goals_away
            FROM archivio_versioni pr
            JOIN archivio_versioni uf
              ON uf.fixture_id = pr.fixture_id AND uf.tipo = 'ufficiale'
            JOIN fixtures f ON f.id = pr.fixture_id
            WHERE pr.tipo IN ('probabile', 'nessuna')
              AND f.goals_home IS NOT NULL AND f.status IN ('FT','AET','PEN')
        """)
        coppie_v = cur.fetchall()

        print("\n" + "=" * 70)
        print("LE FORMAZIONI UFFICIALI CAMBIANO LE PREVISIONI?")
        print("=" * 70)

        if len(coppie_v) < 10:
            print(f"Solo {len(coppie_v)} partite hanno entrambe le versioni.")
            print("Servono piu' giornate: la fascia live deve intercettare le")
            print("formazioni ufficiali prima del fischio d'inizio.")
        else:
            spostamenti = []
            prima_p, dopo_p = [], []
            for (fid, casa, fuori, a1, ax, a2, b1, bx, b2, gc, ga) in coppie_v:
                esito = "1" if gc > ga else ("X" if gc == ga else "2")
                spostamenti.append((max(abs(a1-b1), abs(ax-bx), abs(a2-b2)),
                                    casa, fuori, (a1, ax, a2), (b1, bx, b2), esito))
                prima_p.append(perdita(a1, ax, a2, esito))
                dopo_p.append(perdita(b1, bx, b2, esito))

            medio = sum(s[0] for s in spostamenti) / len(spostamenti)
            grandi = sum(1 for s in spostamenti if s[0] >= 0.05)
            print(f"Partite con entrambe le versioni: {len(coppie_v)}")
            print(f"Spostamento medio della probabilita': {medio*100:.1f} punti")
            print(f"Partite spostate di 5 punti o piu': {grandi} "
                  f"({grandi/len(coppie_v):.0%})")

            mp, md = sum(prima_p)/len(prima_p), sum(dopo_p)/len(dopo_p)
            print(f"\n  log loss con formazioni stimate:  {mp:.4f}")
            print(f"  log loss con formazioni ufficiali: {md:.4f}")

            random.seed(SEED + 1)
            idx = list(range(len(coppie_v)))
            diffs = []
            for _ in range(N_BOOTSTRAP):
                c = [random.choice(idx) for _ in idx]
                diffs.append(sum(prima_p[i] - dopo_p[i] for i in c) / len(c))
            media_d = sum(diffs) / len(diffs)
            lo_d, hi_d = intervallo(diffs)
            print(f"  guadagno delle ufficiali: {media_d:+.4f}")
            print(f"  intervallo 95%: [{lo_d:+.4f}, {hi_d:+.4f}]")
            if lo_d > 0:
                print("  ESITO: le formazioni ufficiali MIGLIORANO le previsioni.")
                print("  E' la prova che gli indicatori di formazione servono.")
            elif hi_d < 0:
                print("  ESITO: le previsioni peggiorano usando le ufficiali.")
                print("  Andrebbe rivisto il modo in cui pesiamo le assenze.")
            else:
                print("  ESITO: non distinguibile con questo numero di partite.")

            print("\n  Dove le formazioni hanno spostato di piu':")
            print(f"  {'partita':<34} {'prima':>14} {'dopo':>14} {'esito':>6}")
            print("  " + "-" * 70)
            for s in sorted(spostamenti, reverse=True)[:8]:
                _, casa, fuori, prima, dopo, esito = s
                pa = "/".join(f"{x*100:.0f}" for x in prima)
                do = "/".join(f"{x*100:.0f}" for x in dopo)
                print(f"  {(casa + ' - ' + fuori)[:33]:<34} {pa:>14} {do:>14} {esito:>6}")

    # ---- calibrazione --------------------------------------------
    print("\n" + "=" * 70)
    print("CALIBRAZIONE (quando diciamo X%, succede X%?)")
    print("=" * 70)
    print(f"{'fascia':<14} {'casi':>6} {'previsto':>10} {'reale':>9} {'scarto':>9}")
    print("-" * 70)
    coppie = []
    for r in righe:
        for etichetta, p in (("1", r["p1"]), ("X", r["px"]), ("2", r["p2"])):
            coppie.append((p, 1 if r["esito"] == etichetta else 0))
    for lo_f, hi_f in [(0, .15), (.15, .25), (.25, .35), (.35, .45),
                       (.45, .55), (.55, .70), (.70, 1.01)]:
        g = [(p, e) for p, e in coppie if lo_f <= p < hi_f]
        if len(g) < 15:
            continue
        att = sum(p for p, _ in g) / len(g)
        rea = sum(e for _, e in g) / len(g)
        print(f"{lo_f:.0%}-{hi_f:.0%}{'':<8} {len(g):>6} {att:>9.1%} {rea:>8.1%} "
              f"{att-rea:>+8.1%}")

    # ---- gli altri mercati ----------------------------------------
    print("\n" + "=" * 70)
    print("ALTRI MERCATI")
    print("=" * 70)
    print("Finora guardavamo solo 1X2. Questi mercati derivano dalla stessa")
    print("matrice dei punteggi, ma possono essere calibrati diversamente.\n")

    mercati_extra = [
        ("Over 2.5", "over25",
         lambda r: 1 if (r["goals_home"] + r["goals_away"]) >= 3 else 0),
        ("Gol/Gol", "gol_gol",
         lambda r: 1 if (r["goals_home"] > 0 and r["goals_away"] > 0) else 0),
    ]

    print(f"{'mercato':<12} {'casi':>6} {'previsto':>10} {'reale':>9} "
          f"{'scarto':>9} {'log loss':>10} {'vs fisso':>10}")
    print("-" * 70)

    for etichetta, campo, avvenuto_di in mercati_extra:
        g = [r for r in righe if r.get(campo) is not None]
        if len(g) < 20:
            print(f"{etichetta:<12} {len(g):>6}  troppo poche partite")
            continue
        previsto = sum(r[campo] for r in g) / len(g)
        reale = sum(avvenuto_di(r) for r in g) / len(g)

        # log loss del mercato binario
        ll = 0.0
        for r in g:
            p = r[campo] if avvenuto_di(r) else 1 - r[campo]
            ll -= math.log(max(p, 1e-15))
        ll /= len(g)

        # riferimento: prevedere sempre la frequenza media
        base = 0.0
        for r in g:
            p = reale if avvenuto_di(r) else 1 - reale
            base -= math.log(max(p, 1e-15))
        base /= len(g)
        guadagno = (1 - ll / base) * 100 if base > 0 else 0

        print(f"{etichetta:<12} {len(g):>6} {previsto:>9.1%} {reale:>8.1%} "
              f"{previsto-reale:>+8.1%} {ll:>10.4f} {guadagno:>+9.2f}%")

    print("\nLo scarto dice se il mercato e' calibrato: vicino a zero significa")
    print("che in media prevediamo la frequenza giusta. Il confronto con il")
    print("riferimento fisso dice se distinguiamo le partite fra loro.")

    # ---- scomposizione -------------------------------------------
    print("\n" + "=" * 70)
    print("DOVE FUNZIONA MEGLIO")
    print("=" * 70)
    for titolo, campo, valori in [
        ("per affidabilita'", "affidabilita_etichetta", ["alta", "media", "bassa"]),
        ("per formazioni", "formazioni", ["ufficiale", "probabile", "nessuna"]),
    ]:
        print(f"\n{titolo}:")
        print(f"  {'gruppo':<14} {'casi':>6} {'log loss':>10} {'azzeccate':>11}")
        for v in valori:
            g = [r for r in righe if r.get(campo) == v]
            if len(g) < 10:
                continue
            ll = sum(perdita(r["p1"], r["px"], r["p2"], r["esito"]) for r in g) / len(g)
            ok = sum(1 for r in g
                     if max((("1", r["p1"]), ("X", r["px"]), ("2", r["p2"])),
                            key=lambda t: t[1])[0] == r["esito"])
            print(f"  {v:<14} {len(g):>6} {ll:>10.4f} {ok/len(g):>10.1%}")

    # ---- altri mercati contro il mercato ---------------------------
    print("\n" + "=" * 70)
    print("OVER/UNDER 2.5 E GOL/NOGOL CONTRO IL MERCATO")
    print("=" * 70)
    altri = {
        "over25": confronto_binario(
            righe, "over25", "q_over25",
            lambda r: r["goals_home"] + r["goals_away"] > 2.5),
        "gol": confronto_binario(
            righe, "gol_gol", "q_gol",
            lambda r: r["goals_home"] > 0 and r["goals_away"] > 0),
    }
    for nome, etichetta in (("over25", "Over/Under 2.5"), ("gol", "Gol/NoGol")):
        c = altri[nome]
        if "vantaggio" not in c:
            print(f"  {etichetta}: {c['partite']} partite con quote, ne servono "
                  f"almeno 30 (le quote si archiviano da settembre 2026)")
            continue
        lo, hi = c["intervallo"]
        esito = ("battiamo il mercato" if lo > 0 else
                 ("il mercato e' migliore" if hi < 0 else "non distinguibile"))
        print(f"  {etichetta}: {c['partite']} partite, noi {c['log_loss']:.4f}, "
              f"mercato {c['log_loss_mercato']:.4f}, vantaggio {c['vantaggio']:+.4f} "
              f"[{lo:+.4f}, {hi:+.4f}] -> {esito}")

    # ---- salvataggio ---------------------------------------------
    riepilogo = {
        "generato": datetime.now(timezone.utc).isoformat(),
        "verificate": len(righe),
        "azzeccate": azzeccate,
        "log_loss": sum(nostre) / len(nostre),
    }
    if con_quote:
        riepilogo["con_quote"] = len(con_quote)
        riepilogo["log_loss_mercato"] = mm
        riepilogo["vantaggio"] = media
        riepilogo["intervallo"] = [lo, hi]
    riepilogo["altri_mercati"] = altri
    with open(USCITA_JSON, "w", encoding="utf-8") as f:
        json.dump(riepilogo, f, ensure_ascii=False, indent=1)

    scrivi_html(riepilogo, righe, con_quote if con_quote else [])
    print(f"\nSalvati {USCITA_JSON} e {USCITA_HTML}")


def scrivi_html(riepilogo, righe, con_quote):
    """
    Pagina di verifica: riquadri di sintesi e la tabella COMPLETA delle
    partite verificate, con l'esito di tutti i mercati che seguiamo.
    """
    tutte = sorted(righe, key=lambda r: r["data"], reverse=True)

    def esito_over(r):
        return (r["goals_home"] + r["goals_away"]) >= 3

    def esito_gg(r):
        return r["goals_home"] > 0 and r["goals_away"] > 0

    def prob_avvenuto(p, avvenuto):
        """
        Probabilita' che avevamo dato a cio' che e' POI successo.
        Se l'evento non si e' verificato, quella giusta e' il complemento:
        un Over al 33% significa che all'Under davamo il 67%.
        """
        return p if avvenuto else 1 - p

    def colore(p_avvenuto):
        """Verde se avevamo visto giusto, rosso se avevamo sbagliato."""
        return "bene" if p_avvenuto >= 0.55 else (
            "male" if p_avvenuto < 0.35 else "medio")

    voci, mese_corrente = [], None
    for r in tutte:
        mese = r["data"][:7]
        if mese != mese_corrente:
            mese_corrente = mese
            voci.append(f'<tr class="mese"><td colspan="6">{mese}</td></tr>')

        esito = r["esito"]
        p_esito = {"1": r["p1"], "X": r["px"], "2": r["p2"]}[esito]

        if r.get("over25") is not None:
            ov = esito_over(r)
            p_ov = prob_avvenuto(r["over25"], ov)
            cella_ov = (f'<td class="{colore(p_ov)}">'
                        f'{p_ov*100:.0f}%<br>'
                        f'<span class="reale">{"Over" if ov else "Under"}</span></td>')
        else:
            cella_ov = '<td class="vuoto">-</td>'

        if r.get("gol_gol") is not None:
            gg = esito_gg(r)
            p_gg = prob_avvenuto(r["gol_gol"], gg)
            cella_gg = (f'<td class="{colore(p_gg)}">'
                        f'{p_gg*100:.0f}%<br>'
                        f'<span class="reale">{"Gol" if gg else "NoGol"}</span></td>')
        else:
            cella_gg = '<td class="vuoto">-</td>'

        voci.append(
            f'<tr><td class="d">{r["data"][8:10]}/{r["data"][5:7]}</td>'
            f'<td class="s">{r["casa"]} - {r["fuori"]}'
            f'<br><span class="lega">{r.get("campionato","")}</span></td>'
            f'<td class="ris">{r["goals_home"]}-{r["goals_away"]}</td>'
            f'<td class="{colore(p_esito)}">{p_esito*100:.0f}%<br>'
            f'<span class="reale">{esito}</span></td>'
            f'{cella_ov}{cella_gg}</tr>')

    blocco_mercato = ""
    if con_quote:
        v = riepilogo.get("vantaggio", 0)
        lo, hi = riepilogo.get("intervallo", [0, 0])
        esito = ("Battiamo il mercato" if lo > 0 else
                 ("Il mercato e' migliore" if hi < 0 else "Non distinguibile"))
        blocco_mercato = f"""
<div class="riquadro">
 <div class="tit">Confronto con i bookmaker</div>
 <div class="grande">{esito}</div>
 <div class="spiega">
  su {riepilogo['con_quote']} partite con quote &middot;
  differenza {v:+.4f} di log loss, intervallo 95% [{lo:+.4f}, {hi:+.4f}]<br>
  Il confronto usa le probabilita' dei bookmaker tolto il loro margine,
  sulle stesse partite, con le quote fotografate quando abbiamo previsto.
 </div>
</div>"""

    html = f"""<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Verifica delle previsioni</title>
<style>
 body {{ font-family:-apple-system,system-ui,sans-serif; margin:0; padding:12px;
        background:#f4f5f7; color:#1c2733; }}
 h1 {{ font-size:18px; margin:0 0 12px; }}
 .riquadro {{ background:#fff; border-radius:6px; padding:12px; margin-bottom:12px; }}
 .tit {{ font-size:10px; text-transform:uppercase; letter-spacing:.5px;
         color:#7b8794; margin-bottom:6px; }}
 .grande {{ font-size:22px; font-weight:600; }}
 .spiega {{ font-size:11px; color:#5b6b7b; margin-top:6px; line-height:1.6; }}
 table {{ width:100%; border-collapse:collapse; font-size:12px; background:#fff; }}
 th {{ background:#2c3e50; color:#fff; padding:6px 3px; font-size:10px;
       position:sticky; top:0; }}
 td {{ padding:6px 3px; border-bottom:1px solid #eef1f4; text-align:center;
       vertical-align:middle; }}
 .mese td {{ background:#eef1f4; font-weight:600; text-align:left;
             font-size:11px; padding:5px 8px; }}
 .d {{ font-size:10px; color:#7b8794; white-space:nowrap; }}
 .s {{ text-align:left; font-size:12px; }}
 .lega {{ font-size:9px; color:#97a3ae; }}
 .ris {{ font-weight:600; white-space:nowrap; }}
 .reale {{ font-size:9px; color:#5b6b7b; }}
 .bene {{ background:#e3f2e3; color:#15642f; font-weight:600; }}
 .medio {{ background:#fdf6e3; color:#8a6d1f; }}
 .male {{ background:#fbebeb; color:#8f2626; }}
 .vuoto {{ color:#c4ccd3; }}
 .nota {{ margin-top:14px; font-size:10px; color:#7b8794; line-height:1.7; }}
</style></head><body>
<h1>Verifica delle previsioni</h1>
<div class="riquadro">
 <div class="tit">Partite verificate</div>
 <div class="grande">{riepilogo['verificate']}</div>
 <div class="spiega">esito piu' probabile azzeccato
  {riepilogo['azzeccate']} volte
  ({riepilogo['azzeccate']/max(riepilogo['verificate'],1):.0%}) &middot;
  log loss {riepilogo['log_loss']:.4f}</div>
</div>
{blocco_mercato}
<div class="riquadro" style="padding:0;overflow:hidden">
 <table>
  <tr><th>Data</th><th>Partita</th><th>Ris.</th>
      <th>Esito<br>1X2</th><th>Over<br>2.5</th><th>Gol<br>Gol</th></tr>
  {''.join(voci)}
 </table>
</div>
<div class="nota">
In ogni colonna dei mercati la percentuale e' quella che avevamo
assegnato a cio' che poi e' successo, e sotto c'e' l'esito reale.
Verde quando avevamo dato oltre il 55%, rosso sotto il 35%.<br><br>
Un modello ben calibrato sbaglia spesso: quello che conta non e'
azzeccare il singolo pronostico, ma che le probabilita' corrispondano
alle frequenze reali nel lungo periodo.
</div>
</body></html>"""
    with open(USCITA_HTML, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} non trovato.")
        sys.exit(1)
    modalita = sys.argv[1] if len(sys.argv) > 1 else "report"
    conn = sqlite3.connect(DB_PATH)
    if modalita == "archivia":
        archivia(conn)
    elif modalita == "report":
        report(conn)
    else:
        print("Modalita' ammesse: archivia, report")
    conn.close()


if __name__ == "__main__":
    main()
