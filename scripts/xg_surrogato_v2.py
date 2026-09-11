"""
xG SURROGATO - VERSIONE 2
==========================
Migliora la versione precedente su due fronti.

PROBLEMA 1: COLLINEARITA'
  Nella v1 i coefficienti di shots_outside (-0.001) e corners (-0.017)
  erano nulli o negativi: privi di senso calcistico. Causa: le variabili
  si sovrappongono (shots_on contiene parte dell'informazione degli altri
  tiri). Qui confrontiamo varianti piu' semplici e scartiamo i coefficienti
  negativi rifacendo la stima senza quella variabile.

PROBLEMA 2: VALIDAZIONE TROPPO FACILE
  Lo split casuale mette righe dello stesso campionato in training e test.
  Ma noi applichiamo il modello a campionati MAI VISTI (Ligue 2, 1. Lig).
  Qui usiamo "leave-one-league-out": alleno su tutti i campionati tranne
  uno, testo su quello. Ripetuto per ognuno. Misura onesta del nostro caso.

USO:
    python xg_surrogato_v2.py
"""

import sqlite3
import os

DB_PATH = "calcio_dati.db"

# Varianti da confrontare (nome -> lista di predittori)
VARIANTI = {
    "A: area + fuori":              ["shots_inside", "shots_outside"],
    "B: area + fuori + in porta":   ["shots_inside", "shots_outside", "shots_on"],
    "C: area + in porta":           ["shots_inside", "shots_on"],
    "D: v1 completa":               ["shots_inside", "shots_outside", "shots_on", "corners"],
    "E: solo tiri totali":          ["shots_total"],
}

MIN_RIGHE_PER_LEGA = 40  # campionati con meno righe non entrano nella validazione


# ---------------------------------------------------------------
# Minimi quadrati (Python puro)
# ---------------------------------------------------------------

def risolvi_sistema(A, b):
    n = len(A)
    M = [riga[:] + [b[i]] for i, riga in enumerate(A)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[pivot][col]) < 1e-12:
            raise ValueError("sistema singolare")
        M[col], M[pivot] = M[pivot], M[col]
        for r in range(col + 1, n):
            f = M[r][col] / M[col][col]
            for c in range(col, n + 1):
                M[r][c] -= f * M[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (M[r][n] - sum(M[r][c] * x[c] for c in range(r + 1, n))) / M[r][r]
    return x


def minimi_quadrati(X, y):
    X1 = [[1.0] + r for r in X]
    k = len(X1[0])
    XtX = [[sum(X1[i][a] * X1[i][b] for i in range(len(X1))) for b in range(k)]
           for a in range(k)]
    Xty = [sum(X1[i][a] * y[i] for i in range(len(X1))) for a in range(k)]
    return risolvi_sistema(XtX, Xty)


def stima_senza_negativi(X, y, nomi):
    """
    Stima i coefficienti; se qualcuno risulta negativo, rimuove quella
    variabile e ripete. Un coefficiente negativo su tiri o corner non ha
    senso calcistico ed e' segno di collinearita'.
    Restituisce (coefficienti, nomi_tenuti, indici_tenuti).
    """
    indici = list(range(len(nomi)))
    while True:
        X_rid = [[riga[i] for i in indici] for riga in X]
        coef = minimi_quadrati(X_rid, y)
        # coef[0] e' l'intercetta: puo' essere negativa senza problemi
        negativi = [j for j, c in enumerate(coef[1:]) if c < 0]
        if not negativi or len(indici) == 1:
            return coef, [nomi[i] for i in indici], indici
        # rimuovo la variabile con il coefficiente piu' negativo
        peggiore = min(range(len(coef) - 1), key=lambda j: coef[j + 1])
        indici.pop(peggiore)


def prevedi(coef, riga):
    return max(0.0, coef[0] + sum(c * v for c, v in zip(coef[1:], riga)))


def mae(previsti, reali):
    return sum(abs(p - r) for p, r in zip(previsti, reali)) / len(reali)


# ---------------------------------------------------------------

def main():
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} non trovato.")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    tutte_var = sorted({v for lista in VARIANTI.values() for v in lista})
    cond = " AND ".join(f"{v} IS NOT NULL" for v in tutte_var)

    cur.execute(f"""
        SELECT league_id, xg, {', '.join(tutte_var)}
        FROM team_match WHERE xg IS NOT NULL AND {cond}
    """)
    righe = cur.fetchall()
    print(f"Righe con xG reale: {len(righe)}")

    cur.execute("SELECT id, country || ' - ' || name FROM leagues")
    nomi_lega = dict(cur.fetchall())

    # organizzo per campionato
    per_lega = {}
    for r in righe:
        per_lega.setdefault(r[0], []).append(r)

    leghe_valide = [l for l, v in per_lega.items() if len(v) >= MIN_RIGHE_PER_LEGA]
    print(f"Campionati nella validazione: {len(leghe_valide)}\n")

    indice_var = {v: i + 2 for i, v in enumerate(tutte_var)}  # posizione nella riga

    # ============================================================
    print("=" * 72)
    print("VALIDAZIONE LEAVE-ONE-LEAGUE-OUT")
    print("(ogni campionato viene testato da un modello che non lo ha mai visto)")
    print("=" * 72)
    print(f"{'VARIANTE':<30} {'MAE medio':>10} {'MAE peggiore':>14}")
    print("-" * 72)

    risultati = {}

    for nome_var, predittori in VARIANTI.items():
        errori_per_lega = {}

        for lega_esclusa in leghe_valide:
            X_tr, y_tr, X_te, y_te = [], [], [], []
            for lega, lista in per_lega.items():
                for r in lista:
                    valori = [r[indice_var[p]] for p in predittori]
                    if lega == lega_esclusa:
                        X_te.append(valori); y_te.append(r[1])
                    else:
                        X_tr.append(valori); y_tr.append(r[1])

            coef, _, idx = stima_senza_negativi(X_tr, y_tr, predittori)
            previsti = [prevedi(coef, [riga[i] for i in idx]) for riga in X_te]
            errori_per_lega[lega_esclusa] = mae(previsti, y_te)

        medio = sum(errori_per_lega.values()) / len(errori_per_lega)
        peggiore = max(errori_per_lega.values())
        risultati[nome_var] = (medio, peggiore, errori_per_lega, predittori)
        print(f"{nome_var:<30} {medio:>10.4f} {peggiore:>14.4f}")

    # riferimento: regola grezza
    err_regola = {}
    for lega in leghe_valide:
        te = per_lega[lega]
        previsti = [0.11 * r[indice_var["shots_total"]] for r in te]
        err_regola[lega] = mae(previsti, [r[1] for r in te])
    medio_regola = sum(err_regola.values()) / len(err_regola)
    print(f"{'RIFERIMENTO: 0.11 x tiri':<30} {medio_regola:>10.4f} "
          f"{max(err_regola.values()):>14.4f}")

    # ============================================================
    # scelta: minimo MAE medio, a parita' preferisco meno variabili
    migliore = min(risultati, key=lambda k: (round(risultati[k][0], 4), len(risultati[k][3])))
    medio, peggiore, errori_lega, predittori = risultati[migliore]

    print("\n" + "=" * 72)
    print(f"VARIANTE SCELTA: {migliore}")
    print("=" * 72)
    print(f"  MAE medio su campionati mai visti: {medio:.4f}")
    print(f"  Miglioramento sulla regola grezza: {(1 - medio/medio_regola)*100:.1f}%")

    if medio >= medio_regola:
        print("\n  Nessuna variante batte la regola grezza su campionati nuovi.")
        print("  Uso comunque la variante migliore, ma il guadagno e' marginale.")

    # coefficienti finali su TUTTI i dati
    X_tutti = [[r[indice_var[p]] for p in predittori] for r in righe]
    y_tutti = [r[1] for r in righe]
    coef, nomi_tenuti, idx = stima_senza_negativi(X_tutti, y_tutti, predittori)

    print("\n  Coefficienti finali (calibrati su tutti i dati):")
    print(f"    intercetta            {coef[0]:>8.4f}")
    for nome, c in zip(nomi_tenuti, coef[1:]):
        print(f"    {nome:<20}  {c:>8.4f}")
    scartate = [p for p in predittori if p not in nomi_tenuti]
    if scartate:
        print(f"    (scartate per coefficiente negativo: {', '.join(scartate)})")

    print("\n  Errore per campionato (mai visto in training):")
    for lega, err in sorted(errori_lega.items(), key=lambda x: -x[1]):
        print(f"    {nomi_lega.get(lega, lega):<38} MAE {err:.4f}")

    # ============================================================
    print("\n" + "=" * 72)
    print("APPLICAZIONE")
    print("=" * 72)

    cur.execute("PRAGMA table_info(team_match)")
    colonne = {r[1] for r in cur.fetchall()}
    for nuova in ("xg_stimato", "xg_finale", "xg_origine"):
        if nuova not in colonne:
            cur.execute(f"ALTER TABLE team_match ADD COLUMN {nuova} "
                        f"{'TEXT' if nuova == 'xg_origine' else 'REAL'}")

    cond_pred = " AND ".join(f"{p} IS NOT NULL" for p in nomi_tenuti)
    cur.execute(f"""
        SELECT fixture_id, team_id, xg, {', '.join(nomi_tenuti)}
        FROM team_match WHERE {cond_pred}
    """)
    aggiornamenti = []
    n_reali = n_stimati = 0
    for fid, tid, xg_reale, *valori in cur.fetchall():
        stima = prevedi(coef, list(valori))
        if xg_reale is not None:
            aggiornamenti.append((stima, xg_reale, "reale", fid, tid)); n_reali += 1
        else:
            aggiornamenti.append((stima, stima, "stimato", fid, tid)); n_stimati += 1

    cur.executemany("""
        UPDATE team_match SET xg_stimato = ?, xg_finale = ?, xg_origine = ?
        WHERE fixture_id = ? AND team_id = ?
    """, aggiornamenti)
    conn.commit()

    print(f"  xG reale mantenuto: {n_reali}")
    print(f"  xG stimato:         {n_stimati}")
    cur.execute("SELECT COUNT(*) FROM team_match WHERE xg_finale IS NULL")
    print(f"  ancora senza xG:    {cur.fetchone()[0]}  (righe prive di statistiche tiri)")

    conn.close()


if __name__ == "__main__":
    main()
