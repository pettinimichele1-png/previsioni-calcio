"""
INDICATORI SULL'ALLENATORE
===========================
Il "rimbalzo da cambio allenatore" e' un effetto documentato: nelle prime
partite dopo un avvicendamento le squadre tendono a rendere sopra le
attese, per ragioni che vanno dalla scossa psicologica al fatto che si
cambia quando si e' in un momento negativo (quindi c'e' comunque un
ritorno verso la media).

I dati ci sono gia': raccolta_formazioni.py salva l'allenatore di ogni
partita nella tabella lineups.

INDICATORI PRODOTTI
-------------------
    allenatore_nuovo    1 se e' cambiato nelle ultime N partite
    partite_allenatore  da quante partite guida la squadra (esperienza)

ATTENZIONE AL CONFONDIMENTO
---------------------------
Le squadre cambiano allenatore quando vanno male. Quindi "allenatore
nuovo" e' associato a squadre in crisi, non solo all'effetto rimbalzo.
Il modello ha gia' dentro forza e forma, quindi in parte lo separa: se
il coefficiente risultasse positivo, sarebbe un rimbalzo al netto del
rendimento. Ma resta un indicatore da guardare con prudenza.

NIENTE LEAKAGE
--------------
L'allenatore di una partita e' noto prima del fischio d'inizio, e il
conteggio delle partite precedenti usa solo il passato.

USO:
    python indicatori_allenatore.py
"""

import sqlite3
import os

DB_PATH = "calcio_dati.db"
# entro quante partite dal cambio si considera "allenatore nuovo"
FINESTRA_NUOVO = 3


def main():
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} non trovato.")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lineups'")
    if not cur.fetchone():
        print("Tabella 'lineups' assente: esegui prima raccolta_formazioni.py")
        conn.close()
        return

    cur.execute("""
        SELECT l.fixture_id, l.team_id, l.coach_id, l.coach_name, f.date
        FROM lineups l JOIN fixtures f ON f.id = l.fixture_id
        WHERE l.coach_id IS NOT NULL
        ORDER BY f.date
    """)
    righe = cur.fetchall()
    print(f"Partite con allenatore registrato: {len(righe)}")
    if len(righe) < 200:
        print("Troppo poche per calcolare l'indicatore.")
        conn.close()
        return

    storia = {}
    for fid, tid, cid, nome, data in righe:
        storia.setdefault(tid, []).append((data, fid, cid, nome))
    for tid in storia:
        storia[tid].sort(key=lambda x: x[0])

    print(f"Squadre seguite: {len(storia)}")

    # ---------------------------------------------------------------
    aggiornamenti = []
    cambi = []
    for tid, partite in storia.items():
        for i, (data, fid, cid, nome) in enumerate(partite):
            precedenti = partite[:i]
            if not precedenti:
                # prima partita nota: non sappiamo se e' un cambio
                aggiornamenti.append((None, None, fid, tid))
                continue

            # da quante partite consecutive guida la squadra
            quante = 0
            for d2, f2, c2, n2 in reversed(precedenti):
                if c2 == cid:
                    quante += 1
                else:
                    break

            nuovo = 1 if quante < FINESTRA_NUOVO else 0
            if quante == 0:
                precedente = precedenti[-1][3]
                cambi.append((data[:10], tid, precedente, nome))

            aggiornamenti.append((nuovo, quante, fid, tid))

    # ---------------------------------------------------------------
    cur.execute("PRAGMA table_info(features)")
    colonne = {r[1] for r in cur.fetchall()}
    if not colonne:
        print("Tabella 'features' assente: esegui prima indicatori.py")
        conn.close()
        return
    for nome_col, tipo in [("allenatore_nuovo", "INTEGER"),
                           ("partite_allenatore", "INTEGER")]:
        if nome_col not in colonne:
            cur.execute(f"ALTER TABLE features ADD COLUMN {nome_col} {tipo}")

    cur.executemany("""
        UPDATE features SET allenatore_nuovo = ?, partite_allenatore = ?
        WHERE fixture_id = ? AND team_id = ?
    """, aggiornamenti)
    conn.commit()

    # ---------------------------------------------------------------
    print("\n" + "=" * 62)
    print("RISULTATO")
    print("=" * 62)
    cur.execute("SELECT COUNT(*) FROM features WHERE allenatore_nuovo IS NOT NULL")
    print(f"Righe con l'indicatore: {cur.fetchone()[0]}")
    print(f"Cambi di allenatore rilevati: {len(cambi)}")

    cur.execute("""SELECT AVG(partite_allenatore), MAX(partite_allenatore)
                   FROM features WHERE partite_allenatore IS NOT NULL""")
    media, massimo = cur.fetchone()
    if media is not None:
        print(f"Permanenza media: {media:.1f} partite (massimo {massimo})")

    if cambi:
        print("\nUltimi cambi rilevati:")
        cur.execute("SELECT id, name FROM teams")
        nomi = dict(cur.fetchall())
        for data, tid, prima, dopo in sorted(cambi, reverse=True)[:8]:
            print(f"  {data}  {nomi.get(tid, tid)[:20]:<22} {str(prima)[:18]:<20} -> {dopo}")

    # ---- il rendimento cambia davvero? ----
    print("\n" + "=" * 62)
    print("RENDIMENTO DOPO IL CAMBIO")
    print("=" * 62)
    print(f"{'situazione':<26} {'partite':>8} {'punti':>8} {'gol':>7} {'subiti':>8}")
    print("-" * 62)
    for condizione, etichetta in [
        ("allenatore_nuovo = 1", "allenatore nuovo"),
        ("allenatore_nuovo = 0 AND partite_allenatore < 10", "in carica da poco"),
        ("partite_allenatore >= 10", "in carica da tempo"),
    ]:
        cur.execute(f"""
            SELECT COUNT(*),
                   AVG(CASE result WHEN 'W' THEN 3 WHEN 'D' THEN 1 ELSE 0 END),
                   AVG(goals_for), AVG(goals_against)
            FROM features WHERE {condizione}
        """)
        n, punti, gf, ga = cur.fetchone()
        if n and n >= 30:
            print(f"{etichetta:<26} {n:>8} {punti:>8.2f} {gf:>7.2f} {ga:>8.2f}")

    print("\nSe le squadre con allenatore nuovo fanno MENO punti, l'indicatore")
    print("sta misurando la crisi che ha causato il cambio, non il rimbalzo.")
    print("Sara' il modello a separarli: ha gia' dentro forza e forma.")

    conn.close()


if __name__ == "__main__":
    main()
