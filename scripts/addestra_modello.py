"""
ADDESTRAMENTO DEL MODELLO
==========================
Stima tutti i parametri sui dati disponibili e li salva in modello.json,
che verra' poi usato da previsioni.py.

Separare addestramento e previsione serve a questo: l'addestramento e'
lento e basta una volta al giorno, mentre le previsioni vanno rigenerate
in pochi secondi ogni volta che escono nuove formazioni.

COSA VIENE SALVATO
------------------
    fattore campo, media gol            struttura di base
    peso xG, shrinkage, rho             parametri del Poisson
    coefficienti dei 6 indicatori       formazione + squadra
    medie e deviazioni                  per standardizzare i nuovi dati
    prestazione misurata                per sapere quanto vale il modello

La penalita' viene scelta sulla validazione, come nel modello v4: e' il
meccanismo che fa crescere da solo il peso degli indicatori man mano che
l'archivio si allarga.

USO:
    python addestra_modello.py
"""

import sqlite3
import os
import json
import math
from datetime import datetime, timezone

DB_PATH = "calcio_dati.db"
USCITA = "modello.json"

PESO_XG = 0.75
SHRINK_K = 8
RHO = -0.05
MAX_GOL = 8
QUOTA_VAL = 0.20          # ultima parte dei dati, per scegliere la penalita'
PENALITA_DA_PROVARE = [0, 25, 50, 100, 200, 400, 800, 1600, 3200]

INDICATORI = {
    "produzione_out": "attacco: produzione offensiva mancante",
    "difesa_out":     "difesa: reparto arretrato mancante",
    "portiere_nuovo": "portiere diverso dal solito",
    "possesso":       "possesso palla",
    "att_gol_ctx":    "attacco casa/trasferta",
    "falli":          "falli commessi",
    # Queste quattro su 479 partite di test non risultano ancora
    # dimostrate (volatilita' -0.0033, allenatore -0.0005, intervalli a
    # cavallo dello zero). Restano nel modello: la penalizzazione le tiene
    # a peso ridotto e lo alzera' da sola quando i dati le sosterranno.
    # Rilancia test_aggiunte.py ogni tanto per seguirne l'andamento.
    "volatilita_gol": "irregolarita' offensiva",
    "volatilita_dif": "irregolarita' difensiva",
    "allenatore_nuovo": "allenatore cambiato da poco",
    "partite_allenatore": "permanenza dell'allenatore",
}

# Fattore campo: quanto conviene giocare in casa. Non e' uguale ovunque
# (in Grecia o in Championship pesa piu' che in Eredivisie), quindi lo
# stimiamo per campionato. Con pochi dati la stima e' ballerina, percio'
# viene tirata verso il valore complessivo: con FORZA_LEGA=300 righe un
# campionato con 300 partite pesa per meta' sulla propria stima.
FORZA_LEGA = 300


def poisson(k, lam):
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def tau_dc(x, y, lc, lf):
    if x == 0 and y == 0: return 1.0 - lc * lf * RHO
    if x == 0 and y == 1: return 1.0 + lc * RHO
    if x == 1 and y == 0: return 1.0 + lf * RHO
    if x == 1 and y == 1: return 1.0 - RHO
    return 1.0


def esiti(lc, lf):
    p1 = px = p2 = 0.0
    tot = 0.0
    g = []
    for x in range(MAX_GOL + 1):
        pxx = poisson(x, lc)
        riga = [pxx * poisson(y, lf) * max(0.01, tau_dc(x, y, lc, lf))
                for y in range(MAX_GOL + 1)]
        g.append(riga)
        tot += sum(riga)
    for x in range(MAX_GOL + 1):
        for y in range(MAX_GOL + 1):
            p = g[x][y] / tot
            if x > y: p1 += p
            elif x == y: px += p
            else: p2 += p
    return p1, px, p2


def perdita(prev, esito):
    return -math.log(max({"W": prev[0], "D": prev[1], "L": prev[2]}[esito], 1e-15))


def stima(dati, indicatori, penalita, iterazioni=600, passo=0.03):
    par = {n: [0.0, 0.0] for n in indicatori}
    N = len(dati)
    for _ in range(iterazioni):
        grad = {n: [0.0, 0.0] for n in indicatori}
        for d in dati:
            ec = sum(par[m][0] * d["z"][m][0] + par[m][1] * d["z"][m][1] for m in indicatori)
            ef = sum(par[m][0] * d["z"][m][1] + par[m][1] * d["z"][m][0] for m in indicatori)
            ec, ef = max(-1.5, min(1.5, ec)), max(-1.5, min(1.5, ef))
            lc, lf = d["bc"] * math.exp(ec), d["bf"] * math.exp(ef)
            rc, rf = d["gc"] - lc, d["gf"] - lf
            for m in indicatori:
                zc, zf = d["z"][m]
                grad[m][0] += rc * zc + rf * zf
                grad[m][1] += rc * zf + rf * zc
        for m in indicatori:
            for j in (0, 1):
                g = (grad[m][j] - 2.0 * penalita * par[m][j]) / N
                par[m][j] = max(-0.6, min(0.6, par[m][j] + passo * g))
    return par


def lambdas(d, indicatori, par):
    ec = sum(par[m][0] * d["z"][m][0] + par[m][1] * d["z"][m][1] for m in indicatori)
    ef = sum(par[m][0] * d["z"][m][1] + par[m][1] * d["z"][m][0] for m in indicatori)
    ec, ef = max(-1.5, min(1.5, ec)), max(-1.5, min(1.5, ef))
    return (max(0.15, min(5.0, d["bc"] * math.exp(ec))),
            max(0.15, min(5.0, d["bf"] * math.exp(ef))))


def main():
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} non trovato.")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(features)")
    presenti = {r[1] for r in cur.fetchall()}
    usabili = [c for c in INDICATORI if c in presenti]
    if not usabili:
        print("Nessun indicatore disponibile: esegui prima indicatori.py")
        conn.close()
        return
    saltati = [c for c in INDICATORI if c not in presenti]
    if saltati:
        print(f"Indicatori assenti, saltati: {', '.join(saltati)}")

    sel_c = ", ".join(f"c.{x}" for x in usabili)
    sel_f = ", ".join(f"f.{x}" for x in usabili)
    cur.execute(f"""
        SELECT c.date, c.goals_for, c.goals_against, c.result,
               c.att_xg, c.dif_xg, c.att_gol, c.dif_gol, c.n_precedenti,
               f.att_xg, f.dif_xg, f.att_gol, f.dif_gol, f.n_precedenti,
               {sel_c}, {sel_f}, c.league_id
        FROM features c
        JOIN features f ON f.fixture_id = c.fixture_id AND f.team_id <> c.team_id
        WHERE c.is_home = 1
          AND c.att_xg IS NOT NULL AND f.att_xg IS NOT NULL
          AND c.dif_xg IS NOT NULL AND f.dif_xg IS NOT NULL
        ORDER BY c.date
    """)
    righe = cur.fetchall()
    print(f"Partite per l'addestramento: {len(righe)}")
    if len(righe) < 300:
        print("Troppo poche.")
        conn.close()
        return

    cur.execute("SELECT AVG(goals_for) FROM team_match WHERE goals_for IS NOT NULL")
    media_gol = cur.fetchone()[0]
    cur.execute("""SELECT AVG(CASE WHEN is_home=1 THEN goals_for END),
                          AVG(CASE WHEN is_home=0 THEN goals_for END)
                   FROM team_match WHERE goals_for IS NOT NULL""")
    gc_, gf_ = cur.fetchone()
    fattore = gc_ / gf_
    print(f"  media gol {media_gol:.3f}   fattore campo complessivo {fattore:.3f}")

    # fattore campo per singolo campionato, tirato verso quello complessivo
    cur.execute("""
        SELECT league_id,
               AVG(CASE WHEN is_home=1 THEN goals_for END),
               AVG(CASE WHEN is_home=0 THEN goals_for END),
               COUNT(*)
        FROM team_match WHERE goals_for IS NOT NULL
        GROUP BY league_id
    """)
    fattore_lega = {}
    grezzi = []
    for lid, gc_l, gf_l, n in cur.fetchall():
        if not gc_l or not gf_l or gf_l <= 0 or n < 40:
            continue
        f_grezzo = gc_l / gf_l
        peso = n / (n + FORZA_LEGA)
        fattore_lega[str(lid)] = fattore + (f_grezzo - fattore) * peso
        grezzi.append((lid, f_grezzo, fattore_lega[str(lid)], n))

    cur.execute("SELECT id, country || ' - ' || name FROM leagues")
    nomi_lega = dict(cur.fetchall())
    print(f"\n  Fattore campo per campionato ({len(fattore_lega)} stimati):")
    print(f"  {'campionato':<34} {'grezzo':>8} {'usato':>8} {'righe':>7}")
    print("  " + "-" * 60)
    for lid, gr, us, n in sorted(grezzi, key=lambda x: -x[2])[:6]:
        print(f"  {nomi_lega.get(lid, str(lid))[:33]:<34} {gr:>8.3f} {us:>8.3f} {n:>7}")
    print("  ...")
    for lid, gr, us, n in sorted(grezzi, key=lambda x: x[2])[:3]:
        print(f"  {nomi_lega.get(lid, str(lid))[:33]:<34} {gr:>8.3f} {us:>8.3f} {n:>7}")
    print()

    def restringi(v, n):
        return 1.0 if v is None else 1.0 + (v - 1.0) * n / (n + SHRINK_K)

    off, ne = 14, len(usabili)
    dati = []
    for r in righe:
        f_lega = fattore_lega.get(str(r[-1]), fattore)
        ac = restringi(PESO_XG * r[4] + (1 - PESO_XG) * r[6], r[8])
        dc = restringi(PESO_XG * r[5] + (1 - PESO_XG) * r[7], r[8])
        af = restringi(PESO_XG * r[9] + (1 - PESO_XG) * r[11], r[13])
        df = restringi(PESO_XG * r[10] + (1 - PESO_XG) * r[12], r[13])
        dati.append({
            "gc": r[1], "gf": r[2], "esito": r[3],
            "bc": max(0.15, min(5.0, media_gol * ac * df * math.sqrt(f_lega))),
            "bf": max(0.15, min(5.0, media_gol * af * dc / math.sqrt(f_lega))),
            "grezzi": {n: (r[off + i], r[off + ne + i]) for i, n in enumerate(usabili)},
        })

    # standardizzazione su tutti i dati disponibili
    stat = {}
    for nome in usabili:
        vals = [v for d in dati for v in d["grezzi"][nome] if v is not None]
        if len(vals) < 50:
            continue
        m = sum(vals) / len(vals)
        s = math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))
        if s > 1e-9:
            stat[nome] = (m, s)
    indicatori = list(stat.keys())

    for d in dati:
        d["z"] = {}
        for nome in indicatori:
            m, s = stat[nome]
            a, b = d["grezzi"][nome]
            d["z"][nome] = (0.0 if a is None else (a - m) / s,
                            0.0 if b is None else (b - m) / s)

    n_val = int(len(dati) * QUOTA_VAL)
    train, val = dati[:-n_val], dati[-n_val:]
    print(f"  train {len(train)} / validazione {len(val)}\n")

    print("Scelgo la penalita'...")
    migliore = None
    for pen in PENALITA_DA_PROVARE:
        par = stima(train, indicatori, pen)
        ll = sum(perdita(esiti(*lambdas(d, indicatori, par)), d["esito"])
                 for d in val) / len(val)
        print(f"  penalita {pen:>6}: log loss {ll:.4f}")
        if migliore is None or ll < migliore[0]:
            migliore = (ll, pen)
    ll_val, penalita = migliore
    print(f"\nPenalita' scelta: {penalita}")

    # stima definitiva su TUTTI i dati
    print("Stima finale su tutti i dati...")
    par_fin = stima(dati, indicatori, penalita)

    # riferimento: frequenze storiche
    n = len(dati)
    f1 = sum(1 for d in dati if d["esito"] == "W") / n
    fx = sum(1 for d in dati if d["esito"] == "D") / n
    f2 = sum(1 for d in dati if d["esito"] == "L") / n

    modello = {
        "generato": datetime.now(timezone.utc).isoformat(),
        "partite_addestramento": len(dati),
        "media_gol": media_gol,
        "fattore_campo": fattore,
        "fattore_campo_lega": fattore_lega,
        "peso_xg": PESO_XG,
        "shrink_k": SHRINK_K,
        "rho": RHO,
        "penalita": penalita,
        "log_loss_validazione": ll_val,
        "frequenze_storiche": {"1": f1, "X": fx, "2": f2},
        "indicatori": indicatori,
        "coefficienti": {n: par_fin[n] for n in indicatori},
        "standardizzazione": {n: list(stat[n]) for n in indicatori},
        "descrizioni": {n: INDICATORI[n] for n in indicatori},
    }
    with open(USCITA, "w", encoding="utf-8") as f:
        json.dump(modello, f, ensure_ascii=False, indent=1)

    print(f"\nParametri salvati in {USCITA}")
    print(f"\n{'indicatore':<42} {'proprio':>9} {'avversario':>12}")
    print("-" * 66)
    for nome in indicatori:
        a, b = par_fin[nome]
        print(f"{INDICATORI[nome]:<42} {a:>+9.3f} {b:>+12.3f}")

    conn.close()


if __name__ == "__main__":
    main()
