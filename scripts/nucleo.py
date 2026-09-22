"""
NUCLEO DEL CALCOLO
===================
Contiene il calcolo degli indicatori di squadra in UN SOLO posto.

PERCHE' ESISTE
--------------
Prima questo calcolo era duplicato in indicatori.py (che prepara i dati
per l'addestramento) e in previsioni.py (che prevede le partite future).
Le due copie sono andate fuori sincrono: la correzione per il cambio di
divisione era stata applicata solo alla prima, e le previsioni hanno
continuato a sbagliare sulle neopromosse senza che se ne vedesse traccia.

Da qui in avanti ogni correzione si scrive una volta e vale per entrambi.

COME SI USA
-----------
Il chiamante prepara la lista delle partite PRECEDENTI di una squadra
(dalla piu' recente) e una funzione che dice la media del campionato.
Il modulo restituisce gli indicatori.

    from nucleo import indicatori_squadra, carica_conservazione

    ind = indicatori_squadra(precedenti, lega_ora, media_di, conservazione)

CHIAVI ATTESE in ogni partita:
    league_id, season, is_home, goals_for, goals_against,
    xg_finale, xg_against    (questi due possono mancare)
    possession, fouls        (facoltativi)
"""

import os
import json

# Peso delle partite piu' vecchie: la decima indietro pesa circa il 20%
# della piu' recente. E' il valore predefinito delle funzioni di media.
DECADIMENTO = 0.85

# FORZA DELLE SQUADRE (indicatori_squadra): due regole tarate a settembre
# 2026 con test_decadimento.py su 4.185 partite.
#   - le partite di questa stagione si ricordano a lungo: ognuna pesa il
#     95% della successiva (prima era l'85%)
#   - quelle della stagione scorsa pesano il 30%, perche' d'estate le
#     rose cambiano: a inizio stagione il nostro storico era in buona
#     parte fatto di squadre che non esistono piu'
# Risultato: guadagno a inizio stagione, neutro nel resto, distanza dal
# mercato ridotta di circa un decimo. Con 0.85 e 1.0 si torna a prima.
DECADIMENTO_SQUADRA = 0.95
PESO_STAGIONE_PRECEDENTE = 0.3

CORREZIONE_PATH = "correzione_divisione.json"


def _peso(k, fattori, decadimento):
    d = DECADIMENTO if decadimento is None else decadimento
    return d ** k * (fattori[k] if fattori else 1.0)


def media_pesata(valori, fattori=None, decadimento=None):
    """
    valori: lista dal piu' recente al piu' vecchio. Salta i None.
    fattori: moltiplicatore facoltativo per ogni valore (es. stagione).
    """
    num = den = 0.0
    for k, v in enumerate(valori):
        if v is None:
            continue
        p = _peso(k, fattori, decadimento)
        num += p * v
        den += p
    return num / den if den > 0 else None


def rapporto_pesato(numeratori, denominatori, fattori=None, decadimento=None):
    """Rapporto tra somme pesate: piu' stabile della media dei rapporti."""
    num = den = 0.0
    for k, (a, b) in enumerate(zip(numeratori, denominatori)):
        if a is None or b is None:
            continue
        p = _peso(k, fattori, decadimento)
        num += p * a
        den += p * b
    return num / den if den > 0 else None


def deviazione_pesata(valori, fattori=None, decadimento=None):
    """
    Quanto sono ballerini i valori, con lo stesso peso decrescente della
    media. Misura la COSTANZA di una squadra: chi alterna 4-0 e 0-3 ha
    deviazione alta anche avendo la stessa media di chi fa sempre 1-1.
    Serve almeno qualche partita: sotto le 4 restituisce None.
    """
    coppie = [(v, _peso(k, fattori, decadimento))
              for k, v in enumerate(valori) if v is not None]
    if len(coppie) < 4:
        return None
    peso_tot = sum(p for _, p in coppie)
    if peso_tot <= 0:
        return None
    media = sum(v * p for v, p in coppie) / peso_tot
    var = sum(p * (v - media) ** 2 for v, p in coppie) / peso_tot
    return var ** 0.5


def carica_conservazione(percorso=CORREZIONE_PATH):
    """
    Quanto del rendimento in un'altra divisione si conserva.

    La stima su 52 squadre che hanno cambiato categoria ha dato un
    coefficiente non distinguibile da zero: dominare in seconda divisione
    non dice quasi nulla sulla forza in prima. Senza il file si usa 0,
    cioe' quelle partite non contano per stabilire la forza (contano
    comunque per la forma recente e per l'Elo).
    """
    valori = {"attacco": 0.0, "difesa": 0.0}
    if os.path.exists(percorso):
        try:
            with open(percorso, encoding="utf-8") as f:
                dati = json.load(f)
            for chiave in valori:
                c = (dati.get("coefficienti", {}).get(chiave) or {}).get("c")
                if c is not None:
                    # fuori da [0, 1] non avrebbe senso calcistico
                    valori[chiave] = max(0.0, min(1.0, c))
        except (ValueError, OSError):
            pass
    return valori


def normalizza(partita, campo, quale, tipo, lega_ora, media_di, conservazione):
    """
    Valore di una partita rapportato alla media del campionato in cui e'
    stata giocata. Se quel campionato non e' quello attuale, il risultato
    viene tirato verso 1.

    quale: 0 per i gol, 1 per gli xG (serve a scegliere la media giusta)
    tipo:  'attacco' o 'difesa'
    """
    v = partita.get(campo)
    if v is None:
        return None
    media = media_di(partita["league_id"], partita.get("season"), quale)
    if not media or media <= 0:
        return None
    valore = v / media
    if partita["league_id"] != lega_ora:
        valore = 1.0 + conservazione[tipo] * (valore - 1.0)
    return valore


def indicatori_squadra(precedenti, lega_ora, media_di, conservazione,
                       is_home_attuale=None, stagione_ora=None):
    """
    Calcola gli indicatori di una squadra dalle sue partite precedenti.

    precedenti: lista di dict, DALLA PIU' RECENTE alla piu' vecchia.
                Devono essere partite anteriori a quella da prevedere:
                questo modulo non filtra le date, ci pensa il chiamante.
    lega_ora:   campionato in cui la squadra gioca adesso
    media_di:   funzione (lega, stagione, quale) -> media, oppure None
    is_home_attuale: se indicato (0 o 1), lo split casa/trasferta viene
                calcolato solo su quel contesto; altrimenti su entrambi.

    stagione_ora: stagione della partita da prevedere. Se manca si usa
                quella della partita piu' recente: a inizio stagione, prima
                della prima giornata, il risultato e' lo stesso, perche' se
                tutte le partite hanno lo stesso fattore la media non cambia.

    Restituisce un dizionario di indicatori (valori mancanti = None).
    """
    if not precedenti:
        return {}
    if stagione_ora is None:
        stagione_ora = precedenti[0].get("season")

    def fattori(lista=None):
        return [1.0 if p.get("season") == stagione_ora else PESO_STAGIONE_PRECEDENTE
                for p in (lista if lista is not None else precedenti)]

    def mp(valori, lista=None):
        return media_pesata(valori, fattori(lista), DECADIMENTO_SQUADRA)

    def rp(num, den):
        return rapporto_pesato(num, den, fattori(), DECADIMENTO_SQUADRA)

    def dp(valori):
        return deviazione_pesata(valori, fattori(), DECADIMENTO_SQUADRA)

    def norma(campo, quale, tipo, lista=None):
        return [normalizza(p, campo, quale, tipo, lega_ora, media_di, conservazione)
                for p in (lista if lista is not None else precedenti)]

    ind = {
        "n_precedenti": len(precedenti),
        "att_gol": mp(norma("goals_for", 0, "attacco")),
        "dif_gol": mp(norma("goals_against", 0, "difesa")),
        "att_xg": mp(norma("xg_finale", 1, "attacco")),
        "dif_xg": mp(norma("xg_against", 1, "difesa")),
        "gol_medi": mp([p.get("goals_for") for p in precedenti]),
        "gol_subiti_medi": mp([p.get("goals_against") for p in precedenti]),
        "punti_medi": mp([p.get("points") for p in precedenti]),
        "conversione": rp([p.get("goals_for") for p in precedenti],
                           [p.get("xg_finale") for p in precedenti]),
        # costanza di rendimento: quanto la squadra oscilla intorno alla
        # propria media. Non e' forza: e' prevedibilita'.
        "volatilita_gol": dp(norma("goals_for", 0, "attacco")),
        "volatilita_dif": dp(norma("goals_against", 0, "difesa")),
        "volatilita_xg": dp(norma("xg_finale", 1, "attacco")),
        "tenuta": rp([p.get("goals_against") for p in precedenti],
                      [p.get("xg_against") for p in precedenti]),
    }

    # split casa / trasferta
    contesti = (is_home_attuale,) if is_home_attuale is not None else (0, 1)
    ctx_att, ctx_dif = {}, {}
    for casa in contesti:
        stesse = [p for p in precedenti if p.get("is_home") == casa]
        if len(stesse) >= 2:
            ctx_att[casa] = mp(norma("goals_for", 0, "attacco", stesse), stesse)
            ctx_dif[casa] = mp(norma("goals_against", 0, "difesa", stesse), stesse)
        else:
            ctx_att[casa] = None
            ctx_dif[casa] = None
    ind["att_gol_ctx"] = ctx_att
    ind["dif_gol_ctx"] = ctx_dif

    # medie semplici delle statistiche di gioco (solo partite tracciate)
    con_stat = [p for p in precedenti if p.get("has_team_stats", 1)]
    for chiave, campo in [("possesso", "possession"), ("passaggi_pct", "passes_pct"),
                          ("duelli_pct", "pl_duels_pct"), ("tiri", "shots_total"),
                          ("tiri_in_porta", "shots_on"), ("tiri_area", "shots_inside"),
                          ("corner", "corners"), ("rating", "avg_rating"),
                          ("falli", "fouls"), ("cartellini", "yellow_cards"),
                          ("gol_prevenuti", "goals_prevented")]:
        ind[chiave] = mp([p.get(campo) for p in con_stat], con_stat) if con_stat else None

    return ind
