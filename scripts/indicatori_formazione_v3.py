"""
INDICATORI DI FORMAZIONE - VERSIONE 3
======================================
La v2 non ha trovato segnale: i punti non calavano al crescere delle
assenze. Il motivo probabile e' che trattava tutti i titolari allo
stesso modo.

L'IDEA DELLA v3
---------------
Perdere il capocannoniere non e' come perdere il terzino di riserva.
Invece di contare QUANTI titolari mancano, misuriamo QUANTO pesano.

E questo si stima bene anche con poche partite, perche' i gol si
concentrano: chi ne ha fatti 5 su 14 lo riconosci subito, non serve
una stagione intera. La titolarita' invece e' diffusa su 20 giocatori
ed e' proprio li' che la v2 annegava nel rumore.

INDICATORI PRODOTTI
-------------------
    produzione_out    quota di gol+assist recenti appartenente a chi
                      NON parte titolare oggi (l'indicatore principale)
    produzione_rel    scostamento dall'abitudine di quella squadra
    portiere_nuovo    1 se il portiere abituale non gioca
    difesa_out        quota di presenze difensive abituali mancanti
    quota_titolari_out, assenze_relative   (dalla v2, per confronto)

NIENTE LEAKAGE
--------------
Gol, assist e presenze sono presi SOLO dalle partite precedenti.
Dei titolari di oggi si usa unicamente la lista annunciata.

USO:
    python indicatori_formazione_v3.py
"""

import sqlite3
import json
import os

DB_PATH = "calcio_dati.db"

DECADIMENTO = 0.85
MIN_PARTITE_SQUADRA = 4
MIN_PARTITE_PER_MEDIA = 3
# quota di produzione attribuita d'ufficio a tutta la rosa: evita che
# una squadra con un solo marcatore abbia indicatori estremi
DILUIZIONE = 0.25


def main():
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} non trovato.")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lineup_players'")
    if not cur.fetchone():
        print("Formazioni assenti: esegui prima raccolta_formazioni.py")
        conn.close()
        return

    # ---------------------------------------------------------------
    print("Carico le formazioni...")
    cur.execute("""
        SELECT lp.fixture_id, lp.team_id, lp.player_id, lp.position, f.date
        FROM lineup_players lp JOIN fixtures f ON f.id = lp.fixture_id
        WHERE lp.is_starter = 1 ORDER BY f.date
    """)
    formazioni = {}
    per_squadra = {}
    for fid, tid, pid, pos, data in cur.fetchall():
        k = (fid, tid)
        if k not in formazioni:
            formazioni[k] = {"data": data, "undici": set(), "ruoli": {}}
            per_squadra.setdefault(tid, []).append((data, fid, formazioni[k]))
        formazioni[k]["undici"].add(pid)
        formazioni[k]["ruoli"][pid] = pos
    print(f"  formazioni: {len(formazioni)}")

    # ---------------------------------------------------------------
    print("Carico la produzione dei giocatori...")
    cur.execute("""
        SELECT ps.team_id, ps.player_id, ps.raw_json, f.date
        FROM player_stats ps JOIN fixtures f ON f.id = ps.fixture_id
        ORDER BY f.date
    """)
    # (team, player) -> lista (data, gol, assist, ha_giocato, ruolo)
    produzione = {}
    for tid, pid, raw, data in cur.fetchall():
        try:
            blocco = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for st in blocco.get("statistics", []):
            g = st.get("games") or {}
            if g.get("minutes") is None:
                continue
            gol = (st.get("goals") or {}).get("total") or 0
            assist = (st.get("goals") or {}).get("assists") or 0
            produzione.setdefault((tid, pid), []).append(
                (data, float(gol), float(assist), g.get("position")))
    print(f"  giocatori: {len(produzione)}")

    # ---------------------------------------------------------------
    print("Calcolo gli indicatori...")
    grezzi = []

    for tid, partite in per_squadra.items():
        partite.sort(key=lambda x: x[0])
        if len(partite) < MIN_PARTITE_SQUADRA:
            continue

        for i, (data, fid, voce) in enumerate(partite):
            precedenti = [p for p in partite[:i] if p[0] < data]
            if len(precedenti) < MIN_PARTITE_SQUADRA - 1:
                continue
            precedenti = list(reversed(precedenti))
            undici = voce["undici"]
            ruoli_oggi = voce["ruoli"]

            # --- titolarita' abituale (come nella v2) -----------------
            titolarita = {}
            peso_tot = 0.0
            for k, (_, _, v) in enumerate(precedenti):
                peso = DECADIMENTO ** k
                peso_tot += peso
                for pid in v["undici"]:
                    titolarita[pid] = titolarita.get(pid, 0.0) + peso
            if peso_tot <= 0 or not titolarita:
                continue
            for pid in titolarita:
                titolarita[pid] /= peso_tot
            somma_tit = sum(titolarita.values())
            quota_tit_out = (sum(t for pid, t in titolarita.items() if pid not in undici)
                             / somma_tit) if somma_tit > 0 else None

            # --- produzione offensiva abituale ------------------------
            contributo = {}
            for (t, pid), storia in produzione.items():
                if t != tid:
                    continue
                passate = [s for s in storia if s[0] < data]
                if not passate:
                    continue
                passate = list(reversed(passate))
                valore = sum((gol + 0.7 * assist) * (DECADIMENTO ** k)
                             for k, (_, gol, assist, _) in enumerate(passate))
                if valore > 0:
                    contributo[pid] = valore

            produzione_out = None
            if contributo:
                totale = sum(contributo.values())
                fuori = sum(v for pid, v in contributo.items() if pid not in undici)
                # diluizione: una parte della produzione e' merito di tutta
                # la squadra, non del singolo che segna
                produzione_out = (1 - DILUIZIONE) * (fuori / totale) + DILUIZIONE * (
                    quota_tit_out if quota_tit_out is not None else 0.0)

            # --- portiere --------------------------------------------
            portiere_nuovo = None
            portieri_abituali = {}
            for k, (_, _, v) in enumerate(precedenti):
                for pid, pos in v["ruoli"].items():
                    if pos == "G":
                        portieri_abituali[pid] = portieri_abituali.get(pid, 0.0) + DECADIMENTO ** k
            if portieri_abituali:
                titolare_atteso = max(portieri_abituali, key=portieri_abituali.get)
                portiere_oggi = next((p for p, pos in ruoli_oggi.items() if pos == "G"), None)
                portiere_nuovo = 0 if portiere_oggi == titolare_atteso else 1

            # --- reparto difensivo ------------------------------------
            difesa_out = None
            dif_abituali = {}
            for k, (_, _, v) in enumerate(precedenti):
                for pid, pos in v["ruoli"].items():
                    if pos == "D":
                        dif_abituali[pid] = dif_abituali.get(pid, 0.0) + DECADIMENTO ** k
            if dif_abituali:
                tot_d = sum(dif_abituali.values())
                difesa_out = sum(v for pid, v in dif_abituali.items()
                                 if pid not in undici) / tot_d

            grezzi.append((fid, tid, quota_tit_out, produzione_out,
                           portiere_nuovo, difesa_out))

    print(f"  righe: {len(grezzi)}")

    # --- scostamento dall'abitudine di ciascuna squadra ---------------
    medie_q, medie_p = {}, {}
    for fid, tid, q, p, pg, d in grezzi:
        if q is not None:
            medie_q.setdefault(tid, []).append(q)
        if p is not None:
            medie_p.setdefault(tid, []).append(p)
    abit_q = {t: sum(v)/len(v) for t, v in medie_q.items() if len(v) >= MIN_PARTITE_PER_MEDIA}
    abit_p = {t: sum(v)/len(v) for t, v in medie_p.items() if len(v) >= MIN_PARTITE_PER_MEDIA}

    finali = []
    for fid, tid, q, p, pg, d in grezzi:
        rel_q = (q - abit_q[tid]) if (q is not None and tid in abit_q) else None
        rel_p = (p - abit_p[tid]) if (p is not None and tid in abit_p) else None
        finali.append((q, rel_q, p, rel_p, pg, d, fid, tid))

    # ---------------------------------------------------------------
    cur.execute("PRAGMA table_info(features)")
    colonne = {r[1] for r in cur.fetchall()}
    if not colonne:
        print("Tabella 'features' assente: esegui prima indicatori.py")
        conn.close()
        return
    for nome, tipo in [("quota_titolari_out", "REAL"), ("assenze_relative", "REAL"),
                       ("produzione_out", "REAL"), ("produzione_rel", "REAL"),
                       ("portiere_nuovo", "INTEGER"), ("difesa_out", "REAL"),
                       ("has_lineup", "INTEGER")]:
        if nome not in colonne:
            cur.execute(f"ALTER TABLE features ADD COLUMN {nome} {tipo}")

    cur.execute("UPDATE features SET has_lineup = 0")
    cur.executemany("""
        UPDATE features SET quota_titolari_out = ?, assenze_relative = ?,
               produzione_out = ?, produzione_rel = ?, portiere_nuovo = ?,
               difesa_out = ?, has_lineup = 1
        WHERE fixture_id = ? AND team_id = ?
    """, finali)
    conn.commit()

    # ---------------------------------------------------------------
    print("\n" + "=" * 66)
    print("CONTROLLI")
    print("=" * 66)
    cur.execute("SELECT COUNT(*) FROM features WHERE has_lineup = 1")
    print(f"Righe con indicatori: {cur.fetchone()[0]}\n")
    for col in ["produzione_out", "produzione_rel", "difesa_out", "portiere_nuovo"]:
        cur.execute(f"SELECT AVG({col}), MIN({col}), MAX({col}) FROM features "
                    f"WHERE {col} IS NOT NULL")
        m, mn, mx = cur.fetchone()
        if m is not None:
            print(f"  {col:<20} media {m:>7.3f}   da {mn:>7.3f} a {mx:>7.3f}")

    def riquadro(colonna, titolo, fasce):
        print("\n" + "=" * 66)
        print(titolo)
        print("=" * 66)
        print(f"{'fascia':<28} {'partite':>8} {'punti':>8} {'gol':>7} {'subiti':>8}")
        print("-" * 66)
        for lo, hi, etichetta in fasce:
            cur.execute(f"""
                SELECT COUNT(*),
                       AVG(CASE result WHEN 'W' THEN 3 WHEN 'D' THEN 1 ELSE 0 END),
                       AVG(goals_for), AVG(goals_against)
                FROM features WHERE {colonna} >= ? AND {colonna} < ?
            """, (lo, hi))
            n, punti, gf, ga = cur.fetchone()
            if n and n >= 50:
                print(f"{etichetta:<28} {n:>8} {punti:>8.2f} {gf:>7.2f} {ga:>8.2f}")

    riquadro("produzione_rel", "PRODUZIONE OFFENSIVA MANCANTE (il test principale)",
             [(-1, -.10, "molta meno del solito"), (-.10, -.03, "un po' meno"),
              (-.03, .03, "come al solito"), (.03, .10, "un po' piu'"),
              (.10, 2, "molta piu' del solito")])

    riquadro("difesa_out", "REPARTO DIFENSIVO MANCANTE",
             [(0, .20, "difesa titolare"), (.20, .35, "un cambio"),
              (.35, .55, "due cambi"), (.55, 1.01, "difesa rivoluzionata")])

    print("\n" + "=" * 66)
    print("PORTIERE")
    print("=" * 66)
    for valore, etichetta in [(0, "portiere abituale"), (1, "portiere diverso")]:
        cur.execute("""
            SELECT COUNT(*),
                   AVG(CASE result WHEN 'W' THEN 3 WHEN 'D' THEN 1 ELSE 0 END),
                   AVG(goals_against)
            FROM features WHERE portiere_nuovo = ?
        """, (valore,))
        n, punti, ga = cur.fetchone()
        if n and n >= 50:
            print(f"  {etichetta:<24} {n:>6} partite   punti {punti:.2f}   subiti {ga:.2f}")

    print("\nCerchiamo un andamento coerente: meno produzione disponibile")
    print("-> meno gol fatti; difesa rimaneggiata -> piu' gol subiti.")

    conn.close()


if __name__ == "__main__":
    main()
