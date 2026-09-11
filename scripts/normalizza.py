"""
NORMALIZZAZIONE DEI DATI
=========================
Trasforma i JSON grezzi in una tabella numerica pulita: una riga per
ogni coppia (partita, squadra), pronta per i calcoli.

REGOLE APPLICATE SUI VALORI NULL
---------------------------------
Campi di CONTEGGIO (tiri, corner, falli, cartellini, contrasti...):
    null -> 0, MA solo se in quella partita il campo risulta tracciato
    (cioe' almeno una delle due squadre / almeno un giocatore ha un valore).
    Se nessuno ha valori, il campo resta NULL: la partita non e' tracciata.

Campi di MISURA (possesso, percentuali, xG, goals_prevented, rating):
    null resta NULL. Non vanno mai sostituiti con zero, perche' zero
    sarebbe un valore semanticamente diverso da "non misurato".

passes.accuracy dei giocatori e' un CONTEGGIO di passaggi riusciti
(verificato: non supera mai i passaggi totali), quindi la percentuale
viene ricalcolata da noi.

NON consuma chiamate API.

USO:
    python normalizza.py
"""

import sqlite3
import json
import os

DB_PATH = "calcio_dati.db"

# --- Classificazione dei campi di squadra --------------------------------
# nome nel JSON -> nome colonna
CONTEGGI_SQUADRA = {
    "Total Shots":        "shots_total",
    "Shots on Goal":      "shots_on",
    "Shots off Goal":     "shots_off",
    "Blocked Shots":      "shots_blocked",
    "Shots insidebox":    "shots_inside",
    "Shots outsidebox":   "shots_outside",
    "Corner Kicks":       "corners",
    "Offsides":           "offsides",
    "Fouls":              "fouls",
    "Yellow Cards":       "yellow_cards",
    "Red Cards":          "red_cards",
    "Goalkeeper Saves":   "saves",
    "Total passes":       "passes_total",
    "Passes accurate":    "passes_accurate",
}

MISURE_SQUADRA = {
    "Ball Possession":    "possession",
    "expected_goals":     "xg",
    "goals_prevented":    "goals_prevented",
}

# --- Classificazione dei campi giocatore (aggregati per squadra) ---------
# (gruppo, campo) -> nome colonna
CONTEGGI_GIOCATORE = {
    ("shots", "total"):          "pl_shots_total",
    ("shots", "on"):             "pl_shots_on",
    ("goals", "total"):          "pl_goals",
    ("goals", "assists"):        "pl_assists",
    ("passes", "total"):         "pl_passes_total",
    ("passes", "key"):           "pl_key_passes",
    ("passes", "accuracy"):      "pl_passes_accurate",
    ("tackles", "total"):        "pl_tackles",
    ("tackles", "blocks"):       "pl_blocks",
    ("tackles", "interceptions"): "pl_interceptions",
    ("duels", "total"):          "pl_duels_total",
    ("duels", "won"):            "pl_duels_won",
    ("dribbles", "attempts"):    "pl_dribbles_att",
    ("dribbles", "success"):     "pl_dribbles_succ",
    ("dribbles", "past"):        "pl_dribbled_past",
    ("fouls", "committed"):      "pl_fouls_committed",
    ("fouls", "drawn"):          "pl_fouls_drawn",
}


def numero(valore):
    """Converte un valore dell'API in numero. Gestisce '45%' e stringhe."""
    if valore is None:
        return None
    if isinstance(valore, (int, float)):
        return float(valore)
    testo = str(valore).replace("%", "").strip()
    if testo == "":
        return None
    try:
        return float(testo)
    except ValueError:
        return None


def crea_tabella(conn):
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS team_match")

    colonne_conteggio = list(CONTEGGI_SQUADRA.values())
    colonne_misura = list(MISURE_SQUADRA.values())
    colonne_giocatore = list(CONTEGGI_GIOCATORE.values())

    definizioni = "\n".join(
        f"    {c} REAL," for c in colonne_conteggio + colonne_misura + colonne_giocatore
    )

    cur.execute(f"""
        CREATE TABLE team_match (
            fixture_id   INTEGER,
            team_id      INTEGER,
            opponent_id  INTEGER,
            league_id    INTEGER,
            season       INTEGER,
            date         TEXT,
            is_home      INTEGER,
            goals_for    INTEGER,
            goals_against INTEGER,
            points       INTEGER,
            result       TEXT,
{definizioni}
            passes_pct        REAL,
            pl_passes_pct     REAL,
            pl_duels_pct      REAL,
            pl_dribbles_pct   REAL,
            avg_rating        REAL,
            minutes_total     REAL,
            players_used      INTEGER,
            has_team_stats    INTEGER,
            has_player_stats  INTEGER,
            PRIMARY KEY (fixture_id, team_id)
        )
    """)
    cur.execute("CREATE INDEX idx_tm_team ON team_match(team_id, date)")
    cur.execute("CREATE INDEX idx_tm_league ON team_match(league_id, season)")
    conn.commit()


def estrai_stat_squadra(conn):
    """fixture_id -> {team_id: {tipo: valore_grezzo}}"""
    cur = conn.cursor()
    cur.execute("SELECT fixture_id, team_id, raw_json FROM fixture_stats")
    risultato = {}
    for fid, tid, raw in cur.fetchall():
        blocco = json.loads(raw)
        valori = {}
        for voce in blocco.get("statistics", []):
            valori[voce.get("type")] = voce.get("value")
        risultato.setdefault(fid, {})[tid] = valori
    return risultato


def aggrega_giocatori(conn):
    """
    fixture_id -> {team_id: {colonna: valore aggregato}}
    Applica la regola: null = 0 solo se il campo risulta tracciato
    in quella partita per quella squadra.
    """
    cur = conn.cursor()
    cur.execute("SELECT fixture_id, team_id, raw_json FROM player_stats")

    grezzi = {}
    for fid, tid, raw in cur.fetchall():
        blocco = json.loads(raw)
        for st in blocco.get("statistics", []):
            grezzi.setdefault((fid, tid), []).append(st)

    aggregati = {}
    for (fid, tid), lista in grezzi.items():
        # tengo solo chi ha effettivamente giocato
        giocanti = [st for st in lista
                    if (st.get("games") or {}).get("minutes") is not None]
        if not giocanti:
            continue

        riga = {}

        for (gruppo, campo), colonna in CONTEGGI_GIOCATORE.items():
            valori = [numero((st.get(gruppo) or {}).get(campo)) for st in giocanti]
            non_nulli = [v for v in valori if v is not None]
            if not non_nulli:
                # nessun giocatore ha un valore: campo non tracciato
                riga[colonna] = None
            else:
                # i null degli altri sono zeri veri
                riga[colonna] = sum(non_nulli)

        # minuti e rating (rating e' una MISURA: media pesata sui minuti)
        minuti = [numero((st.get("games") or {}).get("minutes")) or 0 for st in giocanti]
        riga["minutes_total"] = sum(minuti)
        riga["players_used"] = len(giocanti)

        coppie = []
        for st in giocanti:
            g = st.get("games") or {}
            r = numero(g.get("rating"))
            m = numero(g.get("minutes")) or 0
            if r is not None and m > 0:
                coppie.append((r, m))
        if coppie:
            tot_min = sum(m for _, m in coppie)
            riga["avg_rating"] = sum(r * m for r, m in coppie) / tot_min
        else:
            riga["avg_rating"] = None

        aggregati.setdefault(fid, {})[tid] = riga

    return aggregati


def main():
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} non trovato.")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    print("Creo la tabella normalizzata...")
    crea_tabella(conn)

    print("Carico le statistiche di squadra...")
    stat_squadra = estrai_stat_squadra(conn)
    print(f"  partite con statistiche squadra: {len(stat_squadra)}")

    print("Aggrego le statistiche giocatore (puo' richiedere un minuto)...")
    stat_giocatori = aggrega_giocatori(conn)
    print(f"  partite con statistiche giocatore: {len(stat_giocatori)}")

    print("Costruisco le righe...")
    cur.execute("""
        SELECT id, league_id, season, date,
               home_team_id, away_team_id, goals_home, goals_away
        FROM fixtures
    """)
    partite = cur.fetchall()

    colonne_conteggio = list(CONTEGGI_SQUADRA.values())
    colonne_misura = list(MISURE_SQUADRA.values())
    colonne_giocatore = list(CONTEGGI_GIOCATORE.values())
    tutte = (["fixture_id", "team_id", "opponent_id", "league_id", "season",
              "date", "is_home", "goals_for", "goals_against", "points", "result"]
             + colonne_conteggio + colonne_misura + colonne_giocatore
             + ["passes_pct", "pl_passes_pct", "pl_duels_pct", "pl_dribbles_pct",
                "avg_rating", "minutes_total", "players_used",
                "has_team_stats", "has_player_stats"])

    inserimenti = []
    n_senza_stat = 0

    for fid, lid, season, data, home_id, away_id, gh, ga in partite:
        if gh is None or ga is None:
            continue

        for team_id, opp_id, is_home, gf, gs in (
            (home_id, away_id, 1, gh, ga),
            (away_id, home_id, 0, ga, gh),
        ):
            riga = {c: None for c in tutte}
            riga.update({
                "fixture_id": fid, "team_id": team_id, "opponent_id": opp_id,
                "league_id": lid, "season": season, "date": data,
                "is_home": is_home, "goals_for": gf, "goals_against": gs,
                "points": 3 if gf > gs else (1 if gf == gs else 0),
                "result": "W" if gf > gs else ("D" if gf == gs else "L"),
            })

            # ---- statistiche di squadra ----
            blocchi = stat_squadra.get(fid, {})
            valori_squadra = blocchi.get(team_id)
            valori_avversario = blocchi.get(opp_id, {})

            if valori_squadra:
                # il campo e' tracciato se almeno una delle due squadre ha un valore
                tracciato = {}
                for tipo in list(CONTEGGI_SQUADRA) + list(MISURE_SQUADRA):
                    a = numero(valori_squadra.get(tipo))
                    b = numero(valori_avversario.get(tipo))
                    tracciato[tipo] = (a is not None) or (b is not None)

                n_valorizzati = sum(1 for t in tracciato.values() if t)
                riga["has_team_stats"] = 1 if n_valorizzati >= 3 else 0

                if riga["has_team_stats"]:
                    for tipo, colonna in CONTEGGI_SQUADRA.items():
                        v = numero(valori_squadra.get(tipo))
                        # conteggio: null -> 0 se tracciato in questa partita
                        riga[colonna] = v if v is not None else (0.0 if tracciato[tipo] else None)
                    for tipo, colonna in MISURE_SQUADRA.items():
                        # misura: null resta null
                        riga[colonna] = numero(valori_squadra.get(tipo))

                    if riga["passes_total"] and riga["passes_total"] > 0 \
                       and riga["passes_accurate"] is not None:
                        riga["passes_pct"] = riga["passes_accurate"] / riga["passes_total"] * 100
            else:
                riga["has_team_stats"] = 0
                n_senza_stat += 1

            # ---- statistiche giocatore aggregate ----
            agg = stat_giocatori.get(fid, {}).get(team_id)
            if agg:
                riga["has_player_stats"] = 1
                for colonna in colonne_giocatore:
                    riga[colonna] = agg.get(colonna)
                riga["avg_rating"] = agg.get("avg_rating")
                riga["minutes_total"] = agg.get("minutes_total")
                riga["players_used"] = agg.get("players_used")

                # percentuali ricalcolate da noi
                if riga["pl_passes_total"] and riga["pl_passes_total"] > 0 \
                   and riga["pl_passes_accurate"] is not None:
                    riga["pl_passes_pct"] = riga["pl_passes_accurate"] / riga["pl_passes_total"] * 100
                if riga["pl_duels_total"] and riga["pl_duels_total"] > 0 \
                   and riga["pl_duels_won"] is not None:
                    riga["pl_duels_pct"] = riga["pl_duels_won"] / riga["pl_duels_total"] * 100
                if riga["pl_dribbles_att"] and riga["pl_dribbles_att"] > 0 \
                   and riga["pl_dribbles_succ"] is not None:
                    riga["pl_dribbles_pct"] = riga["pl_dribbles_succ"] / riga["pl_dribbles_att"] * 100
            else:
                riga["has_player_stats"] = 0

            inserimenti.append(tuple(riga[c] for c in tutte))

    segnaposto = ",".join("?" for _ in tutte)
    cur.executemany(
        f"INSERT OR REPLACE INTO team_match ({','.join(tutte)}) VALUES ({segnaposto})",
        inserimenti
    )
    conn.commit()

    # ---- riepilogo ----
    print("\n" + "=" * 62)
    print("RISULTATO DELLA NORMALIZZAZIONE")
    print("=" * 62)
    cur.execute("SELECT COUNT(*) FROM team_match")
    print(f"Righe squadra-partita create: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM team_match WHERE has_team_stats = 1")
    print(f"  con statistiche squadra:    {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM team_match WHERE has_player_stats = 1")
    print(f"  con statistiche giocatori:  {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM team_match WHERE xg IS NOT NULL")
    print(f"  con xG:                     {cur.fetchone()[0]}")

    print("\nRiempimento delle colonne principali:")
    cur.execute("SELECT COUNT(*) FROM team_match")
    totale = cur.fetchone()[0]
    for colonna in ["shots_total", "shots_inside", "shots_outside", "corners",
                    "possession", "passes_pct", "xg", "pl_duels_pct",
                    "pl_tackles", "pl_dribbles_att", "avg_rating"]:
        cur.execute(f"SELECT COUNT({colonna}) FROM team_match")
        n = cur.fetchone()[0]
        print(f"  {colonna:<20} {n:>6} / {totale}  ({n/totale*100:>5.1f}%)")

    print("\nEsempio di riga (Serie A):")
    cur.execute("""
        SELECT tm.date, t.name, tm.is_home, tm.goals_for, tm.goals_against,
               tm.shots_total, tm.shots_on, tm.possession, tm.xg,
               tm.pl_duels_pct, tm.avg_rating
        FROM team_match tm JOIN teams t ON t.id = tm.team_id
        WHERE tm.xg IS NOT NULL AND tm.pl_duels_pct IS NOT NULL
        ORDER BY tm.date DESC LIMIT 3
    """)
    for r in cur.fetchall():
        print(f"  {r[0][:10]} {r[1]:<20} casa={r[2]} {r[3]}-{r[4]} "
              f"tiri={r[5]:.0f}({r[6]:.0f}) poss={r[7]:.0f}% xG={r[8]:.2f} "
              f"duelli={r[9]:.0f}% rating={r[10]:.2f}")

    conn.close()
    print("\nTabella 'team_match' pronta.")


if __name__ == "__main__":
    main()
