"""
ARCHIVIO DEL DATABASE SU GITHUB
================================
Il database supera gli 80 MB e cresce. Committarlo ad ogni esecuzione
farebbe esplodere il repository, perche' Git conserva ogni versione di
ogni file.

SOLUZIONE: le Release di GitHub
-------------------------------
Una Release puo' contenere file fino a 2 GB e NON entra nella cronologia
di Git. Ad ogni esecuzione:
    1. si scarica il database dalla Release
    2. lo si aggiorna
    3. lo si ricarica sostituendo il precedente

Il database viaggia compresso (gzip): un SQLite si comprime molto bene,
tipicamente si riduce a un quarto.

PRIMA CONFIGURAZIONE
--------------------
Crea a mano una Release nel repository con tag "database" (vedi le
istruzioni nel README) e caricaci il tuo calcio_dati.db.gz iniziale.
Se la Release non esiste, questo modulo la crea da solo alla prima
esecuzione, partendo da un database vuoto.

USO COME MODULO
---------------
    from storage import scarica_database, carica_database
    scarica_database()      # all'inizio del lavoro
    ...                     # gli script lavorano su calcio_dati.db
    carica_database()       # alla fine
"""

import os
import sys
import json
import gzip
import shutil
import urllib.request
import urllib.error

DB_PATH = "calcio_dati.db"
DB_COMPRESSO = "calcio_dati.db.gz"
TAG_RELEASE = "database"
NOME_ASSET = "calcio_dati.db.gz"

TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO = os.environ.get("GITHUB_REPOSITORY", "")   # es. "utente/nome-repo"
API = "https://api.github.com"


def _richiesta(url, metodo="GET", dati=None, headers=None, binario=False):
    h = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "previsioni-calcio",
    }
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, method=metodo, data=dati, headers=h)
    with urllib.request.urlopen(req, timeout=300) as r:
        contenuto = r.read()
    return contenuto if binario else json.loads(contenuto.decode("utf-8") or "{}")


def _trova_release():
    """Restituisce la Release con il nostro tag, o None se non esiste."""
    try:
        return _richiesta(f"{API}/repos/{REPO}/releases/tags/{TAG_RELEASE}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _crea_release():
    corpo = json.dumps({
        "tag_name": TAG_RELEASE,
        "name": "Archivio database",
        "body": "Contiene il database del sistema di previsione. "
                "Sostituito automaticamente ad ogni aggiornamento.",
        "draft": False,
        "prerelease": False,
    }).encode()
    print("  creo la Release 'database' (non esisteva)")
    return _richiesta(f"{API}/repos/{REPO}/releases", "POST", corpo)


def scarica_database():
    """Scarica e decomprime il database. True se ne ha trovato uno."""
    if not TOKEN or not REPO:
        print("GITHUB_TOKEN o GITHUB_REPOSITORY mancanti: "
              "sto girando fuori da GitHub Actions?")
        return os.path.exists(DB_PATH)

    release = _trova_release()
    if not release:
        print("Nessuna Release 'database': si parte da zero.")
        return False

    asset = next((a for a in release.get("assets", [])
                  if a["name"] == NOME_ASSET), None)
    if not asset:
        print("Release presente ma senza database allegato: si parte da zero.")
        return False

    dimensione = asset["size"] / 1024 / 1024
    print(f"Scarico il database ({dimensione:.1f} MB compressi)...")

    contenuto = _richiesta(asset["url"], headers={"Accept": "application/octet-stream"},
                           binario=True)
    with open(DB_COMPRESSO, "wb") as f:
        f.write(contenuto)

    with gzip.open(DB_COMPRESSO, "rb") as sorgente, open(DB_PATH, "wb") as destinazione:
        shutil.copyfileobj(sorgente, destinazione)
    os.remove(DB_COMPRESSO)

    print(f"  database pronto: {os.path.getsize(DB_PATH)/1024/1024:.1f} MB")
    return True


def carica_database():
    """Comprime il database e lo ricarica sostituendo il precedente."""
    if not TOKEN or not REPO:
        print("Fuori da GitHub Actions: salto il caricamento.")
        return False
    if not os.path.exists(DB_PATH):
        print("Nessun database da caricare.")
        return False

    print("Comprimo il database...")
    with open(DB_PATH, "rb") as sorgente, gzip.open(DB_COMPRESSO, "wb", compresslevel=6) as dest:
        shutil.copyfileobj(sorgente, dest)

    originale = os.path.getsize(DB_PATH) / 1024 / 1024
    compresso = os.path.getsize(DB_COMPRESSO) / 1024 / 1024
    print(f"  {originale:.1f} MB -> {compresso:.1f} MB")

    release = _trova_release() or _crea_release()

    # rimuovo la versione precedente, altrimenti GitHub rifiuta un asset
    # con lo stesso nome
    for a in release.get("assets", []):
        if a["name"] == NOME_ASSET:
            print("  rimuovo la versione precedente")
            _richiesta(f"{API}/repos/{REPO}/releases/assets/{a['id']}", "DELETE")

    print("  carico la nuova versione...")
    url_caricamento = release["upload_url"].split("{")[0] + f"?name={NOME_ASSET}"
    with open(DB_COMPRESSO, "rb") as f:
        _richiesta(url_caricamento, "POST", f.read(),
                   headers={"Content-Type": "application/gzip"})

    os.remove(DB_COMPRESSO)
    print("  caricamento completato")
    return True


if __name__ == "__main__":
    # uso da riga di comando: python storage.py scarica | carica
    azione = sys.argv[1] if len(sys.argv) > 1 else "scarica"
    if azione == "scarica":
        scarica_database()
    elif azione == "carica":
        carica_database()
    else:
        print("Azioni ammesse: scarica, carica")
