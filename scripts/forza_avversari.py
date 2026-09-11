"""
FORZA CORRETTA PER GLI AVVERSARI
=================================
IL PROBLEMA
-----------
Finora attacco e difesa erano medie grezze: gol fatti diviso la media del
campionato. Ma non tengono conto di CHI si e' affrontato.

Il caso greco lo ha reso evidente. Il Panathinaikos risultava la squadra
peggiore del campionato (attacco 0.80, difesa 1.27) mentre il mercato gli
dava il 73%. La Super League greca si divide in playoff: nella fase finale
le prime sei giocano solo fra loro. Chi ha affrontato solo Olympiacos,
PAOK e AEK segna poco e subisce molto, ma non perche' sia debole.

C'era anche un'incoerenza interna: quando PREVEDIAMO moltiplichiamo il
nostro attacco per la difesa avversaria, quindi l'avversario conta. Ma
quando STIMAVAMO attacco e difesa, no.

IL METODO
---------
Attacco e difesa si stimano insieme, per approssimazioni successive:

    gol attesi di i contro j = media * attacco_i * difesa_j

Si parte da tutti a 1 e si ripete:
    attacco_i = gol fatti da i / (somma delle difese affrontate)
    difesa_i  = gol subiti da i / (somma degli attacchi affrontati)

finche' i valori si stabilizzano. Chi segna 1 gol contro difese fortissime
finisce con un attacco piu' alto di chi ne segna 1 contro difese scarse.

QUANDO FUNZIONA E QUANDO NO
---------------------------
Verificato su simulazioni con forze note (errore medio nello stimarle):

    struttura del calendario      grezzo   corretto   riconosce la piu' forte
    girone completo                0.105     0.093    entrambi si'
    sbilanciato ma collegato       0.165     0.135    solo il corretto
    playoff (gruppi separati)      0.424     0.270    nessuno dei due

Il metodo aiuta ovunque, ma nei campionati con playoff (come la Grecia)
non risolve: quando due gruppi di squadre si incontrano poco, la loro
forza relativa non e' ricavabile dai risultati. Non e' un limite del
calcolo, e' un'informazione che nei dati non c'e'.

NIENTE LEAKAGE
--------------
Per ogni giornata la stima usa solo le partite PRECEDENTI. Le stime si
aggiornano giornata per giornata, mai guardando avanti.

COSA PRODUCE
------------
    nella tabella features:  att_adj, dif_adj (versioni corrette)
    nella tabella forza_corrente: i valori aggiornati a oggi, per le
                                  previsioni delle partite future

USO:
    python forza_avversari.py
"""

import sqlite3
import os
import math

DB_PATH = "calcio_dati.db"
MIN_PARTITE_STIMA = 20      # partite minime nel campionato per stimare
ITERAZIONI = 60
LIMITE = (0.25, 4.0)        # valori oltre i quali si taglia, per stabilita'

# REGOLARIZZAZIONE: partite fittizie contro un avversario medio, aggiunte
# a ogni squadra. Senza, la stima insegue il rumore e con pochi dati
# risulta PEGGIORE della media grezza (verificato: serve superare le 300
# partite per campionato perche' convenga). Con queste partite fittizie
# le squadre con poco storico restano vicine a 1 e la correzione agisce
# solo dove i dati la sostengono.
PARTITE_FITTIZIE = 4.0
# Il valore e' stato scelto misurando su simulazioni con forze note:
#     fittizie   errore medio vs stima grezza   caso calendario estremo
#            0            15% PEGGIO                  1.36 (vero 1.53)
#            2            14% meglio                  1.18
#            4            20% meglio                  1.13
#            6            23% meglio                  1.10
#           10            24% meglio                  1.08
# Piu' regolarizzazione rende le stime piu' accurate in media ma attenua
# la correzione proprio sui casi estremi. A 4 il guadagno medio e' quasi
# al massimo e la correzione sui calendari sbilanciati resta apprezzabile.


def stima_attacco_difesa(partite):
    """
    partite: lista di (casa_id, fuori_id, gol_casa, gol_fuori).
    Restituisce (attacco, difesa) come dizionari, normalizzati a media 1.
    """
    squadre = set()
    for c, f, _, _ in partite:
        squadre.add(c)
        squadre.add(f)
    if len(squadre) < 4 or len(partite) < MIN_PARTITE_STIMA:
        return None, None

    media_casa = sum(g for _, _, g, _ in partite) / len(partite)
    media_fuori = sum(g for _, _, _, g in partite) / len(partite)
    if media_casa <= 0 or media_fuori <= 0:
        return None, None

    att = {s: 1.0 for s in squadre}
    dif = {s: 1.0 for s in squadre}

    # per ogni squadra: gol fatti, gol subiti, e le partite in cui compare
    fatti = {s: 0.0 for s in squadre}
    subiti = {s: 0.0 for s in squadre}
    coinvolta = {s: [] for s in squadre}
    for c, f, gc, gf in partite:
        fatti[c] += gc; subiti[c] += gf
        fatti[f] += gf; subiti[f] += gc
        coinvolta[c].append((f, media_casa))    # c gioca in casa
        coinvolta[f].append((c, media_fuori))   # f gioca fuori

    # le partite fittizie contano come rendimento perfettamente medio
    media_generale = (media_casa + media_fuori) / 2
    fittizie_gol = PARTITE_FITTIZIE * media_generale

    for _ in range(ITERAZIONI):
        nuovo_att = {}
        for s in squadre:
            denominatore = sum(media * dif[avv] for avv, media in coinvolta[s])
            nuovo_att[s] = ((fatti[s] + fittizie_gol) /
                            (denominatore + fittizie_gol)) if denominatore > 1e-9 else 1.0
        att = {s: max(LIMITE[0], min(LIMITE[1], v)) for s, v in nuovo_att.items()}

        nuovo_dif = {}
        for s in squadre:
            denominatore = sum(media * att[avv] for avv, media in coinvolta[s])
            nuovo_dif[s] = ((subiti[s] + fittizie_gol) /
                            (denominatore + fittizie_gol)) if denominatore > 1e-9 else 1.0
        dif = {s: max(LIMITE[0], min(LIMITE[1], v)) for s, v in nuovo_dif.items()}

    # normalizzo a media 1: contano i rapporti, non i valori assoluti
    for valori in (att, dif):
        m = sum(valori.values()) / len(valori)
        if m > 0:
            for s in valori:
                valori[s] /= m
    return att, dif


def main():
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} non trovato.")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, league_id, season, date, home_team_id, away_team_id,
               goals_home, goals_away
        FROM fixtures
        WHERE goals_home IS NOT NULL AND goals_away IS NOT NULL
          AND status IN ('FT', 'AET', 'PEN')
        ORDER BY date
    """)
    tutte = cur.fetchall()
    print(f"Partite disponibili: {len(tutte)}")

    per_campionato = {}
    for fid, lid, sea, data, casa, fuori, gc, gf in tutte:
        per_campionato.setdefault((lid, sea), []).append(
            (data, casa, fuori, gc, gf))

    # ---------------------------------------------------------------
    print("Stimo attacco e difesa giornata per giornata...")
    valori = {}       # (data, team) -> (att, dif) validi PRIMA di quella data
    correnti = {}     # team -> (att, dif, lega, stagione) aggiornati a oggi

    for (lid, sea), lista in per_campionato.items():
        lista.sort(key=lambda x: x[0])
        date = sorted({d for d, *_ in lista})
        for data in date:
            precedenti = [(c, f, gc, gf) for d, c, f, gc, gf in lista if d < data]
            if len(precedenti) < MIN_PARTITE_STIMA:
                continue
            att, dif = stima_attacco_difesa(precedenti)
            if not att:
                continue
            for s in att:
                valori[(data, s)] = (att[s], dif[s])

        # stima finale con tutte le partite: serve per le previsioni future
        tutte_lega = [(c, f, gc, gf) for _, c, f, gc, gf in lista]
        att, dif = stima_attacco_difesa(tutte_lega)
        if att:
            for s in att:
                correnti[s] = (att[s], dif[s], lid, sea)

    print(f"  stime prodotte: {len(valori)}")
    print(f"  squadre con forza corrente: {len(correnti)}")

    # ---------------------------------------------------------------
    cur.execute("PRAGMA table_info(features)")
    colonne = {r[1] for r in cur.fetchall()}
    if not colonne:
        print("Tabella 'features' assente: esegui prima indicatori.py")
        conn.close()
        return
    for nome in ("att_adj", "dif_adj"):
        if nome not in colonne:
            cur.execute(f"ALTER TABLE features ADD COLUMN {nome} REAL")

    cur.execute("SELECT fixture_id, team_id, date FROM features")
    aggiornamenti = []
    for fid, tid, data in cur.fetchall():
        v = valori.get((data, tid))
        if v:
            aggiornamenti.append((v[0], v[1], fid, tid))
    cur.executemany("UPDATE features SET att_adj = ?, dif_adj = ? "
                    "WHERE fixture_id = ? AND team_id = ?", aggiornamenti)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS forza_corrente (
            team_id INTEGER PRIMARY KEY, att_adj REAL, dif_adj REAL,
            league_id INTEGER, season INTEGER, aggiornato TEXT)
    """)
    cur.execute("DELETE FROM forza_corrente")
    cur.executemany("""INSERT INTO forza_corrente
                       VALUES (?,?,?,?,?,datetime('now'))""",
                    [(s, a, d, l, sea) for s, (a, d, l, sea) in correnti.items()])
    conn.commit()
    print(f"  righe aggiornate in features: {len(aggiornamenti)}")

    # ---------------------------------------------------------------
    print("\n" + "=" * 72)
    print("CONFRONTO: MEDIE GREZZE CONTRO STIME CORRETTE")
    print("=" * 72)

    cur.execute("SELECT id, country || ' - ' || name FROM leagues")
    nomi_lega = dict(cur.fetchall())
    greche = [lid for lid, n in nomi_lega.items() if "Greece" in n]

    for lid in greche:
        print(f"\n{nomi_lega[lid]}")
        print(f"  {'squadra':<24} {'att grezzo':>11} {'att corretto':>13} "
              f"{'dif grezza':>11} {'dif corretta':>13}")
        print("  " + "-" * 76)
        cur.execute("""
            SELECT t.name, f.att_gol, f.att_adj, f.dif_gol, f.dif_adj
            FROM features f JOIN teams t ON t.id = f.team_id
            WHERE f.league_id = ? AND f.att_adj IS NOT NULL
              AND f.date = (SELECT MAX(date) FROM features f2
                            WHERE f2.team_id = f.team_id AND f2.att_adj IS NOT NULL)
            ORDER BY f.att_adj DESC
        """, (lid,))
        for nome, ag, aa, dg, da in cur.fetchall():
            freccia = ""
            if ag and aa and abs(aa - ag) > 0.25:
                freccia = "   <-- cambia molto"
            print(f"  {nome[:23]:<24} {ag:>11.2f} {aa:>13.2f} "
                  f"{dg:>11.2f} {da:>13.2f}{freccia}")

    # quanto cambiano le stime, in generale
    cur.execute("""
        SELECT AVG(ABS(att_adj - att_gol)), MAX(ABS(att_adj - att_gol)), COUNT(*)
        FROM features WHERE att_adj IS NOT NULL AND att_gol IS NOT NULL
    """)
    medio, massimo, n = cur.fetchone()
    if n:
        print(f"\nScostamento medio fra grezzo e corretto: {medio:.3f} "
              f"(massimo {massimo:.3f}) su {n} righe")

    print("\nLe squadre dove il valore cambia molto sono quelle che hanno")
    print("avuto un calendario sbilanciato: e' li' che la correzione conta.")

    conn.close()


if __name__ == "__main__":
    main()
