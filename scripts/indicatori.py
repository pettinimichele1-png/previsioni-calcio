"""
INDICATORI PRE-PARTITA
=======================
Per ogni riga (partita, squadra) calcola gli indicatori che descrivono la
squadra COME ERA PRIMA di quella partita.

REGOLA FONDAMENTALE: NIENTE DATA LEAKAGE
-----------------------------------------
Ogni indicatore usa esclusivamente partite con data ANTERIORE a quella in
esame. Vale anche per le medie di campionato usate per normalizzare, che
sono calcolate in modo espansivo (solo il passato).
Se violassimo questa regola il backtesting darebbe risultati ottimi e
irreali, e il modello fallirebbe sulle partite vere.

INDICATORI PRODOTTI
-------------------
Forza:        att_gol, dif_gol, att_xg, dif_xg (normalizzati sul campionato)
Forma:        punti medi pesati, gol pesati
Efficienza:   conversione (gol/xG), tenuta difensiva (gol subiti/xG subiti)
Controllo:    possesso, precisione passaggi, duelli, tiri, corner
Contesto:     giorni di riposo, elo, partite disponibili, qualita' dati

PESATURA
--------
Le partite recenti pesano piu' di quelle vecchie: peso = DECADIMENTO^k
dove k e' quante partite fa. Con 0.85, la 10a partita indietro pesa il 20%
della piu' recente.

USO:
    python indicatori.py
"""

import sqlite3
import os
import json
from nucleo import (media_pesata, rapporto_pesato, carica_conservazione,
                    indicatori_squadra, DECADIMENTO)
from datetime import datetime

DB_PATH = "calcio_dati.db"

MIN_PARTITE = 3         # sotto questa soglia gli indicatori restano NULL
ELO_INIZIALE = 1500.0
ELO_K = 20.0            # velocita' di aggiornamento
ELO_VANTAGGIO_CASA = 60.0

# Correzione per cambio di divisione (da correzione_divisione.py).
# Il rendimento in un'altra categoria non si trasferisce: la stima ha dato
# un coefficiente di conservazione non distinguibile da zero. Quelle partite
# vengono quindi tirate verso 1 (= come la media) invece di essere prese
# alla lettera. Se il file manca si usa questo valore prudente.
CONSERVAZIONE_PREDEFINITA = 0.0
CORREZIONE_PATH = "correzione_divisione.json"



def data_di(testo):
    return datetime.fromisoformat(testo.replace("Z", "+00:00"))




def crea_tabella(conn):
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS features")
    cur.execute("""
        CREATE TABLE features (
            fixture_id INTEGER,
            team_id    INTEGER,
            date       TEXT,
            league_id  INTEGER,
            is_home    INTEGER,

            -- esito effettivo (bersaglio del modello, MAI un ingresso)
            goals_for      INTEGER,
            goals_against  INTEGER,
            result         TEXT,

            -- quante partite precedenti erano disponibili
            n_precedenti   INTEGER,
            quota_con_stat REAL,

            -- forza normalizzata sulla media del campionato (1.0 = media)
            att_gol   REAL,
            dif_gol   REAL,
            att_xg    REAL,
            dif_xg    REAL,

            -- stessa cosa ma solo sulle partite dello stesso tipo (casa/trasferta)
            att_gol_ctx REAL,
            dif_gol_ctx REAL,

            -- forma
            punti_medi REAL,
            gol_medi   REAL,
            volatilita_gol REAL,
            volatilita_dif REAL,
            volatilita_xg  REAL,
            gol_subiti_medi REAL,

            -- efficienza
            conversione   REAL,   -- gol fatti / xG prodotti  (>1 = ciniche)
            tenuta        REAL,   -- gol subiti / xG concessi (<1 = ottima difesa/portiere)
            gol_prevenuti REAL,   -- media goals_prevented (parate di valore)

            -- controllo del gioco
            possesso    REAL,
            passaggi_pct REAL,
            duelli_pct  REAL,
            tiri        REAL,
            tiri_in_porta REAL,
            tiri_area   REAL,
            corner      REAL,
            rating      REAL,
            falli       REAL,
            cartellini  REAL,

            -- contesto
            giorni_riposo REAL,
            elo           REAL,

            PRIMARY KEY (fixture_id, team_id)
        )
    """)
    cur.execute("CREATE INDEX idx_feat_team ON features(team_id, date)")
    conn.commit()


def main():
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} non trovato.")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(team_match)")
    colonne = {r[1] for r in cur.fetchall()}
    if "xg_finale" not in colonne:
        print("Manca la colonna xg_finale: esegui prima xg_surrogato_v2.py")
        conn.close()
        return

    print("Carico i dati...")
    cur.execute("""
        SELECT fixture_id, team_id, opponent_id, league_id, season, date, is_home,
               goals_for, goals_against, points, result,
               xg_finale, goals_prevented, possession, passes_pct, pl_duels_pct,
               shots_total, shots_on, shots_inside, corners, avg_rating,
               fouls, yellow_cards, has_team_stats
        FROM team_match
        ORDER BY date
    """)
    campi = [d[0] for d in cur.description]
    righe = [dict(zip(campi, r)) for r in cur.fetchall()]
    print(f"  righe: {len(righe)}")

    # xG concesso = xG prodotto dall'avversario nella stessa partita
    xg_per_riga = {(r["fixture_id"], r["team_id"]): r["xg_finale"] for r in righe}
    for r in righe:
        r["xg_against"] = xg_per_riga.get((r["fixture_id"], r["opponent_id"]))

    conservazione = carica_conservazione()
    print(f"Conservazione del rendimento fuori divisione: "
          f"attacco {conservazione['attacco']:.2f}, difesa {conservazione['difesa']:.2f}")

    # media gol e xG di ogni campionato-stagione: serve a normalizzare le
    # partite giocate in una divisione diversa da quella attuale
    somme = {}
    for r in righe:
        k = (r["league_id"], r["season"])
        s = somme.setdefault(k, {"gol": [0.0, 0], "xg": [0.0, 0]})
        if r["goals_for"] is not None:
            s["gol"][0] += r["goals_for"]; s["gol"][1] += 1
        if r["xg_finale"] is not None:
            s["xg"][0] += r["xg_finale"]; s["xg"][1] += 1
    media_lega = {}
    for k, s in somme.items():
        media_lega[k] = (s["gol"][0] / s["gol"][1] if s["gol"][1] >= 20 else None,
                         s["xg"][0] / s["xg"][1] if s["xg"][1] >= 20 else None)

    crea_tabella(conn)

    # ---------------------------------------------------------------
    # Medie di campionato ESPANSIVE: per ogni partita, la media del
    # campionato calcolata solo sulle partite precedenti.
    # ---------------------------------------------------------------
    print("Calcolo le medie di campionato (solo passato)...")
    per_campionato = {}
    for r in righe:
        per_campionato.setdefault(r["league_id"], []).append(r)

    medie_prima_di = {}  # (fixture_id, team_id) -> (media_gol, media_xg)
    for lega, lista in per_campionato.items():
        lista.sort(key=lambda x: x["date"])
        somma_gol = n_gol = 0.0
        somma_xg = n_xg = 0.0
        # scorro in ordine: prima registro la media corrente, poi aggiungo la riga
        data_precedente = None
        buffer_stessa_data = []
        for r in lista:
            # le partite della stessa giornata non devono vedersi tra loro
            if data_precedente is not None and r["date"] != data_precedente:
                for b in buffer_stessa_data:
                    if b["goals_for"] is not None:
                        somma_gol += b["goals_for"]; n_gol += 1
                    if b["xg_finale"] is not None:
                        somma_xg += b["xg_finale"]; n_xg += 1
                buffer_stessa_data = []
            medie_prima_di[(r["fixture_id"], r["team_id"])] = (
                somma_gol / n_gol if n_gol >= 20 else None,
                somma_xg / n_xg if n_xg >= 20 else None,
            )
            buffer_stessa_data.append(r)
            data_precedente = r["date"]

    # ---------------------------------------------------------------
    # Elo: aggiornato partita per partita, in ordine cronologico
    # ---------------------------------------------------------------
    print("Calcolo i rating Elo...")
    elo = {}
    elo_prima_di = {}

    partite = {}
    for r in righe:
        partite.setdefault(r["fixture_id"], []).append(r)

    for fid in sorted(partite, key=lambda f: partite[f][0]["date"]):
        squadre = partite[fid]
        if len(squadre) != 2:
            continue
        casa = next((s for s in squadre if s["is_home"] == 1), None)
        fuori = next((s for s in squadre if s["is_home"] == 0), None)
        if casa is None or fuori is None:
            continue

        e_casa = elo.get(casa["team_id"], ELO_INIZIALE)
        e_fuori = elo.get(fuori["team_id"], ELO_INIZIALE)
        elo_prima_di[(fid, casa["team_id"])] = e_casa
        elo_prima_di[(fid, fuori["team_id"])] = e_fuori

        atteso_casa = 1.0 / (1.0 + 10 ** (-(e_casa + ELO_VANTAGGIO_CASA - e_fuori) / 400))
        gc, gf = casa["goals_for"], fuori["goals_for"]
        if gc is None or gf is None:
            continue
        reale_casa = 1.0 if gc > gf else (0.5 if gc == gf else 0.0)
        # margine di vittoria: una vittoria larga sposta di piu'
        moltiplicatore = 1.0 + 0.35 * abs(gc - gf) if gc != gf else 1.0
        delta = ELO_K * moltiplicatore * (reale_casa - atteso_casa)
        elo[casa["team_id"]] = e_casa + delta
        elo[fuori["team_id"]] = e_fuori - delta

    # ---------------------------------------------------------------
    # Indicatori per squadra, scorrendo la sua storia in ordine
    # ---------------------------------------------------------------
    print("Costruisco gli indicatori...")
    per_squadra = {}
    for r in righe:
        per_squadra.setdefault(r["team_id"], []).append(r)

    inserimenti = []

    for team_id, storia in per_squadra.items():
        storia.sort(key=lambda x: x["date"])

        for i, r in enumerate(storia):
            precedenti = storia[:i]
            # scarto eventuali partite con la stessa data (stessa giornata)
            precedenti = [p for p in precedenti if p["date"] < r["date"]]
            precedenti = list(reversed(precedenti))  # dal piu' recente

            riga = {
                "fixture_id": r["fixture_id"], "team_id": team_id, "date": r["date"],
                "league_id": r["league_id"], "is_home": r["is_home"],
                "goals_for": r["goals_for"], "goals_against": r["goals_against"],
                "result": r["result"],
                "n_precedenti": len(precedenti),
                "elo": elo_prima_di.get((r["fixture_id"], team_id)),
            }

            # giorni di riposo
            if precedenti:
                riposo = (data_di(r["date"]) - data_di(precedenti[0]["date"])).days
                riga["giorni_riposo"] = float(riposo)
            else:
                riga["giorni_riposo"] = None

            if len(precedenti) < MIN_PARTITE:
                inserimenti.append(riga)
                continue

            con_stat = [p for p in precedenti if p["has_team_stats"] == 1]
            riga["quota_con_stat"] = len(con_stat) / len(precedenti)

            media_gol_lega, media_xg_lega = medie_prima_di.get(
                (r["fixture_id"], team_id), (None, None))

            gol_fatti = [p["goals_for"] for p in precedenti]
            gol_subiti = [p["goals_against"] for p in precedenti]
            xg_fatti = [p["xg_finale"] for p in precedenti]
            xg_subiti = [p["xg_against"] for p in precedenti]

            riga["gol_medi"] = media_pesata(gol_fatti)
            riga["gol_subiti_medi"] = media_pesata(gol_subiti)
            riga["punti_medi"] = media_pesata([p["points"] for p in precedenti])

            lega_ora = r["league_id"]

            def media_di(lega, stagione, quale):
                """Media del campionato: espansiva per quello attuale
                (solo passato), complessiva per gli altri."""
                if lega == lega_ora:
                    return media_gol_lega if quale == 0 else media_xg_lega
                return media_lega.get((lega, stagione), (None, None))[quale]

            ind = indicatori_squadra(precedenti, lega_ora, media_di,
                                     conservazione, r["is_home"])
            for chiave in ("att_gol", "dif_gol", "att_xg", "dif_xg",
                           "conversione", "tenuta", "possesso", "passaggi_pct",
                           "duelli_pct", "tiri", "tiri_in_porta", "tiri_area",
                           "corner", "rating", "falli", "cartellini",
                           "gol_prevenuti", "volatilita_gol", "volatilita_dif",
                           "volatilita_xg"):
                riga[chiave] = ind.get(chiave)
            riga["att_gol_ctx"] = ind["att_gol_ctx"].get(r["is_home"])
            riga["dif_gol_ctx"] = ind["dif_gol_ctx"].get(r["is_home"])

            # efficienza: rapporto tra somme pesate
            riga["conversione"] = rapporto_pesato(gol_fatti, xg_fatti)
            riga["tenuta"] = rapporto_pesato(gol_subiti, xg_subiti)
            riga["gol_prevenuti"] = media_pesata([p["goals_prevented"] for p in precedenti])

            # controllo del gioco (solo dalle partite con statistiche)
            if con_stat:
                ordinate = list(con_stat)
                for colonna, campo in [
                    ("possesso", "possession"), ("passaggi_pct", "passes_pct"),
                    ("duelli_pct", "pl_duels_pct"), ("tiri", "shots_total"),
                    ("tiri_in_porta", "shots_on"), ("tiri_area", "shots_inside"),
                    ("corner", "corners"), ("rating", "avg_rating"),
                    ("falli", "fouls"), ("cartellini", "yellow_cards"),
                ]:
                    riga[colonna] = media_pesata([p[campo] for p in ordinate])

            inserimenti.append(riga)

    # ---------------------------------------------------------------
    print("Salvo...")
    cur.execute("PRAGMA table_info(features)")
    colonne_tab = [r[1] for r in cur.fetchall()]
    valori = [tuple(riga.get(c) for c in colonne_tab) for riga in inserimenti]
    cur.executemany(
        f"INSERT OR REPLACE INTO features ({','.join(colonne_tab)}) "
        f"VALUES ({','.join('?' for _ in colonne_tab)})", valori)
    conn.commit()

    # ---------------------------------------------------------------
    print("\n" + "=" * 68)
    print("RISULTATO")
    print("=" * 68)
    cur.execute("SELECT COUNT(*) FROM features")
    tot = cur.fetchone()[0]
    print(f"Righe totali: {tot}")
    cur.execute(f"SELECT COUNT(*) FROM features WHERE n_precedenti >= {MIN_PARTITE}")
    utili = cur.fetchone()[0]
    print(f"Righe utilizzabili (>= {MIN_PARTITE} partite precedenti): {utili} "
          f"({utili/tot*100:.1f}%)")

    print("\nRiempimento degli indicatori (sulle righe utilizzabili):")
    for c in ["att_gol", "dif_gol", "att_xg", "dif_xg", "att_gol_ctx",
              "conversione", "tenuta", "possesso", "duelli_pct", "rating",
              "giorni_riposo", "elo"]:
        cur.execute(f"SELECT COUNT({c}) FROM features WHERE n_precedenti >= {MIN_PARTITE}")
        n = cur.fetchone()[0]
        print(f"  {c:<16} {n:>6} / {utili}  ({n/utili*100:>5.1f}%)")

    print("\nControllo di sanita' - medie attese:")
    for c, atteso in [("att_gol", "~1.0"), ("dif_gol", "~1.0"),
                      ("conversione", "~1.0"), ("tenuta", "~1.0"),
                      ("elo", "~1500")]:
        cur.execute(f"SELECT AVG({c}) FROM features WHERE {c} IS NOT NULL")
        v = cur.fetchone()[0]
        print(f"  {c:<16} media {v:>8.3f}   (atteso {atteso})")

    print("\nSquadre con Elo piu' alto:")
    cur.execute("""
        SELECT t.name, MAX(f.elo), l.name FROM features f
        JOIN teams t ON t.id = f.team_id
        LEFT JOIN leagues l ON l.id = f.league_id
        WHERE f.elo IS NOT NULL
        GROUP BY f.team_id ORDER BY MAX(f.elo) DESC LIMIT 10
    """)
    for nome, e, lega in cur.fetchall():
        print(f"  {nome:<24} {e:>7.0f}   [{lega}]")

    conn.close()
    print("\nTabella 'features' pronta.")


if __name__ == "__main__":
    main()
