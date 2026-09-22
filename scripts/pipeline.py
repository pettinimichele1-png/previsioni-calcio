"""
PIPELINE COMPLETO
==================
Esegue il sistema su GitHub Actions. Tre momenti nella giornata:

  raccolta   (07:00 italiane)  partite giocate -> ricalcolo di tutto ->
                               formazioni probabili -> previsioni
  previsioni (08:00 italiane)  rigenera le previsioni, senza raccolta
  live       (ogni 30 min)     cerca le formazioni ufficiali delle partite
                               imminenti e ricalcola solo quelle

IL DATABASE
-----------
Vive come allegato di una Release, non nel repository: Git conserva ogni
versione di ogni file e un database da decine di MB lo farebbe esplodere
in poche settimane. Ad ogni esecuzione viene scaricato, aggiornato e
ricaricato compresso, cosi' cresce nel tempo senza appesantire il codice.

TENUTA AI GUASTI
----------------
Se una fase fallisce, quelle che dipendono da essa vengono saltate ma il
database viene salvato lo stesso: meglio un aggiornamento parziale che
perdere ore di raccolta.

USO
---
    python scripts/pipeline.py raccolta
    python scripts/pipeline.py previsioni
    python scripts/pipeline.py live
"""

import os
import sys
import time
import shutil
import subprocess
from datetime import datetime, timezone

CARTELLA = os.path.dirname(os.path.abspath(__file__))
# Dove gira il sistema. Su GitHub il database viaggia dalla Release e
# le pagine vanno in docs/; sul server il database resta fermo nella
# cartella e le pagine vanno dove le serve nginx (SITO_DIR).
SU_GITHUB = os.environ.get("GITHUB_ACTIONS") == "true"
SITO = os.environ.get("SITO_DIR", "docs")
DB_PATH = "calcio_dati.db"

# File di stato che devono sopravvivere fra un'esecuzione e l'altra.
# Ogni esecuzione parte da una macchina pulita che ha solo il codice e il
# database: senza questi, le modalita' "previsioni" e "live" non
# troverebbero il modello addestrato e si fermerebbero subito.
STATO = ("modello.json", "correzione_divisione.json")
CARTELLA_STATO = "stato"

# nome prodotto -> nome pubblicato nel sito
PUBBLICATI = {
    "previsioni.html": "index.html",
    "previsioni.json": "previsioni.json",
    "selezione.html": "selezione.html",
    "giocate.html": "giocate.html",
    "esatti.html": "esatti.html",
    "verifica.html": "verifica.html",
    "verifica.json": "verifica.json",
}


def ripristina_stato():
    """Porta i file di stato dal repository alla cartella di lavoro."""
    for nome in STATO:
        origine = os.path.join(CARTELLA_STATO, nome)
        if os.path.exists(origine):
            shutil.copy2(origine, nome)
            print(f"  ripristinato: {nome}")
        else:
            print(f"  {nome} non ancora presente")


def salva_stato():
    """Rimette i file di stato nel repository, per la prossima esecuzione."""
    os.makedirs(CARTELLA_STATO, exist_ok=True)
    for nome in STATO:
        if os.path.exists(nome):
            shutil.copy2(nome, os.path.join(CARTELLA_STATO, nome))
            print(f"  salvato: {CARTELLA_STATO}/{nome}")


def esegui(script, descrizione, argomenti=(), obbligatoria=False):
    percorso = os.path.join(CARTELLA, script)
    if not os.path.exists(percorso):
        print(f"  [SALTATA] {script} non trovato")
        return False

    print(f"\n{'=' * 64}")
    print(f"FASE: {descrizione}")
    print(f"{'=' * 64}")
    inizio = time.time()
    esito = subprocess.run([sys.executable, percorso, *argomenti], text=True)
    durata = time.time() - inizio

    if esito.returncode == 0:
        print(f"--- completata in {durata:.0f}s")
        return True
    print(f"--- FALLITA (codice {esito.returncode}) dopo {durata:.0f}s")
    if obbligatoria:
        print("Fase obbligatoria fallita: interrompo.")
        sys.exit(1)
    return False


def pubblica():
    """
    Sposta gli HTML e i JSON nella cartella del sito, e pubblica l'app:
    i suoi file stanno nella cartella app/ del repository e vengono
    copiati a ogni giro, cosi' un aggiornamento via git pull arriva da
    solo sul telefono.
    """
    os.makedirs(SITO, exist_ok=True)
    for origine, destinazione in PUBBLICATI.items():
        if os.path.exists(origine):
            shutil.move(origine, os.path.join(SITO, destinazione))
            print(f"  pubblicato: {SITO}/{destinazione}")

    sorgente_app = os.path.join(os.path.dirname(CARTELLA), "app")
    destinazione_app = os.path.join(SITO, "app")
    if os.path.isdir(sorgente_app):
        shutil.copytree(sorgente_app, destinazione_app, dirs_exist_ok=True)
        if os.path.exists("app.json"):
            shutil.move("app.json", os.path.join(destinazione_app, "app.json"))
        print(f"  app pubblicata: {destinazione_app}")


def main():
    modalita = sys.argv[1] if len(sys.argv) > 1 else "previsioni"
    if modalita not in ("raccolta", "previsioni", "live"):
        print("Modalita' ammesse: raccolta, previsioni, live")
        sys.exit(1)

    avvio = datetime.now(timezone.utc)
    print(f"PIPELINE '{modalita}' avviato il {avvio.strftime('%Y-%m-%d %H:%M')} UTC")

    sys.path.insert(0, CARTELLA)
    print(f"Ambiente: {'GitHub Actions' if SU_GITHUB else 'server'}  "
          f"-  pagine in {SITO}")

    if SU_GITHUB:
        import storage
        print(f"\n{'=' * 64}\nFASE: recupero del database e dello stato\n{'=' * 64}")
        trovato = storage.scarica_database()
        ripristina_stato()
    else:
        # sul server il database e il modello restano nella cartella
        # fra un'esecuzione e l'altra: non c'e' niente da scaricare
        trovato = os.path.exists(DB_PATH)
        print(f"\nDatabase locale: {'presente' if trovato else 'ASSENTE'}")
    if not trovato:
        print("Nessun database disponibile.")
        if modalita != "raccolta":
            print("Senza database non si puo' prevedere nulla.")
            sys.exit(1)
    dimensione_iniziale = (os.path.getsize(DB_PATH) / 1024 / 1024
                           if os.path.exists(DB_PATH) else 0)

    if modalita == "raccolta":
        esegui("aggiorna_dati.py", "partite giocate e statistiche")
        esegui("raccolta_formazioni.py", "formazioni delle partite nuove")

        if esegui("normalizza.py", "normalizzazione dei dati grezzi"):
            esegui("xg_surrogato_v2.py", "xG surrogato dove manca")
            esegui("correzione_divisione.py", "correzione per cambio divisione")
            if esegui("indicatori.py", "indicatori di squadra"):
                esegui("forza_avversari.py", "forza corretta per avversario")
                esegui("indicatori_formazione_v3.py", "indicatori di formazione")
                esegui("indicatori_allenatore.py", "indicatori sull'allenatore")
                esegui("addestra_modello.py", "addestramento del modello")

        esegui("formazioni_previste.py", "formazioni probabili", ["probabili"])
        if esegui("previsioni.py", "previsioni della giornata"):
            esegui("verifica.py", "archiviazione delle previsioni", ["archivia"])
        esegui("verifica.py", "verifica dei risultati arrivati", ["report"])
        esegui("esporta_app.py", "dati per l'app")
        esegui("notifiche.py", "notifiche", ["invia"])

    elif modalita == "previsioni":
        esegui("formazioni_previste.py", "formazioni probabili", ["probabili"])
        if esegui("previsioni.py", "previsioni della giornata"):
            esegui("verifica.py", "archiviazione delle previsioni", ["archivia"])
        esegui("verifica.py", "verifica dei risultati arrivati", ["report"])
        esegui("esporta_app.py", "dati per l'app")
        esegui("notifiche.py", "notifiche", ["invia"])

    else:  # live
        if esegui("formazioni_previste.py",
                  "formazioni ufficiali delle partite imminenti", ["ufficiali"]):
            esegui("previsioni.py", "previsioni aggiornate")
            esegui("verifica.py", "archiviazione", ["archivia"])
            esegui("esporta_app.py", "dati per l'app")
            esegui("notifiche.py", "notifiche", ["invia"])

    if SU_GITHUB:
        salva_stato()
    pubblica()

    if os.path.exists(DB_PATH):
        finale = os.path.getsize(DB_PATH) / 1024 / 1024
        print(f"\n  database: {dimensione_iniziale:.1f} MB -> {finale:.1f} MB")
        if SU_GITHUB:
            print(f"\n{'=' * 64}\nFASE: salvataggio del database\n{'=' * 64}")
            storage.carica_database()

    durata = (datetime.now(timezone.utc) - avvio).total_seconds()
    print(f"\nPIPELINE concluso in {durata/60:.1f} minuti")


if __name__ == "__main__":
    main()
