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
import json
import time
import math
import sqlite3
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from nucleo import media_pesata, carica_conservazione, indicatori_squadra

DB_PATH = "calcio_dati.db"
MODELLO = "modello.json"
BASE_URL = "https://v3.football.api-sports.io"
API_KEY = os.environ.get("API_FOOTBALL_KEY", "").strip()

GIORNI_AVANTI = int(os.environ.get("GIORNI_AVANTI", "4"))
# scarica anche le quote per il confronto (circa 90 chiamate in piu')
CON_QUOTE = os.environ.get("CON_QUOTE", "1") != "0"
MAX_CHIAMATE_QUOTE = 200
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


def mercati(M):
    """Tutte le probabilita' derivate dalla matrice dei punteggi."""
    n = len(M)
    p1 = sum(M[x][y] for x in range(n) for y in range(n) if x > y)
    px = sum(M[x][x] for x in range(n))
    p2 = sum(M[x][y] for x in range(n) for y in range(n) if x < y)

    def over(soglia):
        """Probabilita' che i gol totali superino la soglia."""
        return sum(M[x][y] for x in range(n) for y in range(n) if x + y > soglia)

    gg = sum(M[x][y] for x in range(1, n) for y in range(1, n))

    def congiunta(esito, soglia_gol=None, entrambe=None):
        """
        Probabilita' che DUE condizioni si verifichino insieme nella
        stessa partita. Si calcola sommando le celle della matrice che
        soddisfano entrambe: moltiplicare le due probabilita' separate
        sarebbe sbagliato, perche' gli eventi non sono indipendenti.
        """
        tot = 0.0
        for x in range(n):
            for y in range(n):
                if esito == "1" and not x > y: continue
                if esito == "X" and not x == y: continue
                if esito == "2" and not x < y: continue
                if esito == "1X" and x < y: continue
                if esito == "X2" and x > y: continue
                if esito == "12" and x == y: continue
                if soglia_gol is not None:
                    sopra = (x + y) > abs(soglia_gol)
                    if (soglia_gol > 0) != sopra: continue
                if entrambe is not None:
                    segnano = x > 0 and y > 0
                    if entrambe != segnano: continue
                tot += M[x][y]
        return tot
    punteggi = sorted(((M[x][y], f"{x}-{y}") for x in range(6) for y in range(6)),
                      reverse=True)[:5]

    return {
        # esito finale
        "1": p1, "X": px, "2": p2,
        # doppia chance: due esiti su tre
        "1X": p1 + px, "12": p1 + p2, "X2": px + p2,
        # totale gol
        "over15": over(1.5), "under15": 1 - over(1.5),
        "over25": over(2.5), "under25": 1 - over(2.5),
        "over35": over(3.5), "under35": 1 - over(3.5),
        # entrambe le squadre a segno
        "gol_gol": gg, "no_gol": 1 - gg,
        # combinazioni nella stessa partita, calcolate correttamente
        "1+over25": congiunta("1", 2.5), "1+under25": congiunta("1", -2.5),
        "2+over25": congiunta("2", 2.5), "2+under25": congiunta("2", -2.5),
        "1X+over25": congiunta("1X", 2.5), "1X+under25": congiunta("1X", -2.5),
        "X2+over25": congiunta("X2", 2.5), "X2+under25": congiunta("X2", -2.5),
        "12+over25": congiunta("12", 2.5),
        "1+gol": congiunta("1", None, True), "1+nogol": congiunta("1", None, False),
        "2+gol": congiunta("2", None, True),
        "1X+nogol": congiunta("1X", None, False),
        "punteggi_probabili": [{"risultato": r, "prob": round(p, 4)}
                               for p, r in punteggi],
    }


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


def scarica_quote(giorni, id_ammessi):
    """Quote per giornata: poche chiamate invece di una per partita."""
    quote = {}
    chiamate = 0
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
                break
            for voce in risposta:
                fid = (voce.get("fixture") or {}).get("id")
                if fid in id_ammessi:
                    est = quote_mercati(voce)
                    if est:
                        quote[fid] = est
            if pagina >= (dati.get("paging") or {}).get("total", 1):
                break
            pagina += 1
    print(f"  quote trovate: {len(quote)} (chiamate {chiamate})")
    return quote


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
FUSO_GIOCATE = ZoneInfo("Europe/Rome")
GIORNI_GIOCATE = 2

NOMI = {
    "1": "1", "X": "X", "2": "2",
    "1X": "1X", "12": "12", "X2": "X2",
    "over15": "Over 1.5", "under15": "Under 1.5",
    "over25": "Over 2.5", "under25": "Under 2.5",
    "over35": "Over 3.5", "under35": "Under 3.5",
    "gol_gol": "Gol", "no_gol": "NoGol",
    "1+over25": "1 + Over 2.5", "1+under25": "1 + Under 2.5",
    "2+over25": "2 + Over 2.5", "2+under25": "2 + Under 2.5",
    "1X+over25": "1X + Over 2.5", "1X+under25": "1X + Under 2.5",
    "X2+over25": "X2 + Over 2.5", "X2+under25": "X2 + Under 2.5",
    "12+over25": "12 + Over 2.5",
    "1+gol": "1 + Gol", "1+nogol": "1 + NoGol",
    "2+gol": "2 + Gol", "1X+nogol": "1X + NoGol",
}

SEMPLICI = ["1", "X", "2", "over25", "under25", "gol_gol", "no_gol"]
COMBO = ["1+over25", "1+under25", "2+over25", "2+under25",
         "1X+over25", "1X+under25", "X2+over25", "X2+under25",
         "12+over25", "1+gol", "1+nogol", "2+gol", "1X+nogol"]
SICURI = ["1X", "12", "X2", "over15", "under35"]


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
    return {"titolo": titolo, "nota": nota, "voci": voci,
            "prob": prob, "quota": quota}


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


FILE_GIOCATE = "giocate_giorno.json"


def giocate_correnti(previsioni):
    """
    Le proposte del giorno. Si costruiscono UNA volta, al primo giro
    della mattina, e restano quelle fino al giorno dopo: se cambiassero
    a ogni aggiornamento, una schedina vista alle nove potrebbe sparire
    dopo che l'hai giocata.

    Restituisce (proposte, quando sono state fatte).
    """
    oggi = datetime.now(FUSO_GIOCATE).date().isoformat()
    if os.path.exists(FILE_GIOCATE):
        try:
            with open(FILE_GIOCATE, encoding="utf-8") as f:
                salvate = json.load(f)
            if salvate.get("giorno") == oggi and salvate.get("giocate"):
                return salvate["giocate"], salvate.get("generato")
        except (ValueError, OSError):
            pass

    proposte = costruisci_giocate(previsioni)
    quando = datetime.now(timezone.utc).isoformat(timespec="minutes")
    try:
        with open(FILE_GIOCATE, "w", encoding="utf-8") as f:
            json.dump({"giorno": oggi, "generato": quando, "giocate": proposte},
                      f, ensure_ascii=False)
    except OSError:
        pass
    return proposte, quando


def costruisci_giocate(previsioni):
    """Le proposte, divise per logica."""
    proposte = {"singole": [], "alta": [], "valore": [],
                "sistemi": [], "miste": []}
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

    # --- alta probabilita': quota utile ----------------------------
    # Una doppia chance al 93% da' quota 1.07: vincerla non cambia
    # niente. Si usano anche esiti un po' meno scontati, cosi' bastano
    # due o tre eventi per superare la soglia invece di otto.
    # si scartano gli esiti oltre il 90%: danno quote intorno a 1.05 e
    # servirebbero otto eventi per arrivare alla soglia
    sicuri = [v for v in _raccogli(previsioni, SEMPLICI + SICURI,
                                   prob_min=0.60)
              if v["prob"] <= 0.90]
    sicuri.sort(key=lambda v: -v["prob"])
    usate, sicuri_unici = set(), []
    for v in sicuri:
        if v["fixture_id"] in usate:
            continue
        usate.add(v["fixture_id"])
        sicuri_unici.append(v)

    for partenza in range(min(4, len(sicuri_unici))):
        voci, quota = [], 1.0
        for v in sicuri_unici[partenza:]:
            voci.append(v)
            quota *= v["quota"] or _quota_equa(v["prob"])
            if quota >= QUOTA_MINIMA_ALTA and len(voci) >= 2:
                break
            if len(voci) >= 5:
                break
        if quota >= QUOTA_MINIMA_ALTA and 2 <= len(voci) <= 5:
            gia = {tuple(sorted(x["fixture_id"] for x in s["voci"]))
                   for s in proposte["alta"]}
            chiave = tuple(sorted(x["fixture_id"] for x in voci))
            if chiave not in gia:
                proposte["alta"].append(_schedina(
                    voci, f"Alta probabilita' - {len(voci)} eventi",
                    "Esiti molto probabili combinati fino a superare "
                    f"quota {QUOTA_MINIMA_ALTA:.2f}, la soglia sotto la "
                    "quale vincere non cambierebbe nulla."))
        if len(proposte["alta"]) >= 3:
            break

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
    proposte["sistemi"] = _costruisci_sistemi(previsioni)

    # --- miste a quota mirata ---------------------------------------
    # Tre proposte con quote crescenti. Per ognuna si aggiungono eventi
    # finche' la quota non si avvicina al bersaglio, partendo da punti
    # diversi della lista per non produrre tre schedine identiche.
    candidati = _raccogli(previsioni, SEMPLICI + SICURI + COMBO,
                          prob_min=PROB_MINIMA_MISTA)
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
    for bersaglio in QUOTE_MISTE:
        migliore = None
        for partenza in range(len(pool)):
            voci, quota = [], 1.0
            for v in pool[partenza:]:
                q = v["quota"] or _quota_equa(v["prob"])
                if quota * q > bersaglio * 1.35 and len(voci) >= 2:
                    break
                voci.append(v)
                quota *= q
                if quota >= bersaglio * 0.9:
                    break
            if len(voci) < 2 or quota < bersaglio * 0.6:
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
                f"Costruita attorno a quota {bersaglio:.0f}, usando solo "
                f"esiti sopra il {PROB_MINIMA_MISTA:.0%}."))

    if not VALORE_ATTIVO:
        proposte["singole"] = []
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

        prudenti = [k for k in ("1X", "X2", "12", "over15", "under35")
                    if abbastanza(k)]
        prudente = max(prudenti, key=lambda k: m[k]) if prudenti else None
        intermedi = [k for k in ("1", "2", "over25", "under25",
                                 "gol_gol", "no_gol")
                     if m.get(k, 0) >= 0.35 and abbastanza(k)]
        intermedio = max(intermedi, key=lambda k: m[k]) if intermedi else None
        combo = [k for k in COMBO if m.get(k, 0) >= 0.30 and abbastanza(k)]
        combo_scelta = max(combo, key=lambda k: m[k]) if combo else None
        esatto = (m.get("punteggi_probabili") or [{}])[0].get("risultato")

        scelta = [e for e in (prudente, intermedio or combo_scelta, esatto)
                  if e]
        if len(scelta) < 2:
            continue
        unione = prob_unione(M, scelta)
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
    tabella = {
        "1": gc > ga, "X": gc == ga, "2": gc < ga,
        "1X": gc >= ga, "12": gc != ga, "X2": gc <= ga,
        "over15": totale >= 2, "under15": totale < 2,
        "over25": totale >= 3, "under25": totale < 3,
        "over35": totale >= 4, "under35": totale < 4,
        "gol_gol": gc > 0 and ga > 0, "gol": gc > 0 and ga > 0,
        "no_gol": not (gc > 0 and ga > 0), "nogol": not (gc > 0 and ga > 0),
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
            SELECT e.codice, e.esito, f.goals_home, f.goals_away
            FROM schedine_eventi e
            JOIN fixtures f ON f.id = e.fixture_id
            WHERE f.goals_home IS NOT NULL AND f.status IN ('FT','AET','PEN')
        """)
        esiti = {}
        for codice, esito, gc, ga in cur.fetchall():
            esiti.setdefault(codice, []).append(esito_avvenuto(esito, gc, ga))
        conn.close()
    except sqlite3.OperationalError:
        return None

    per_categoria = {}
    for codice, categoria, quota, prob, n_eventi in schedine:
        avvenuti = esiti.get(codice, [])
        if len(avvenuti) < n_eventi or any(a is None for a in avvenuti):
            continue        # non tutte le partite sono state giocate
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


def scrivi_giocate(previsioni, generato, rho=-0.05):
    global RHO_SISTEMI
    RHO_SISTEMI = rho
    proposte, _ = giocate_correnti(previsioni)
    try:
        conn = sqlite3.connect(DB_PATH)
        salva_schedine(proposte, conn)
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
    tutti = []
    for p in previsioni:
        punteggi = p["mercati"].get("punteggi_probabili") or []
        if not punteggi:
            continue
        migliore = punteggi[0]
        secondo = punteggi[1]["prob"] if len(punteggi) > 1 else 0.0
        tutti.append({
            "p": p, "risultato": migliore["risultato"],
            "prob": migliore["prob"],
            "distacco": migliore["prob"] - secondo,
            "alternativi": punteggi[1:3],
        })
    tutti.sort(key=lambda d: -d["prob"])
    scelti = tutti[:5]

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

    for scarto in range(GIORNI_AVANTI + 1):
        giorno = (oggi + timedelta(days=scarto)).isoformat()
        partite = [p for p in chiamata("fixtures", {"date": giorno})
                   if p["league"]["id"] in nomi_lega]
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

    if CON_QUOTE and previsioni:
        print("Scarico le quote per il confronto...")
        quote = scarica_quote({p["data"][:10] for p in previsioni},
                              {p["fixture_id"] for p in previsioni})
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
