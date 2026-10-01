"""
BACKUP GIORNALIERO DEL DATABASE
================================
Da quando il sistema gira sul server, il database esiste in un solo
posto. Questo script ne fa due copie di sicurezza:

  1. sul server, compressa e datata, tenendo gli ultimi sette giorni:
     protegge da un errore o da un database che si rovina
  2. sulla Release di GitHub, fuori dal server: protegge se il server
     si guasta o viene cancellato

Salva anche la cartella stato/ (registro della prova sulla carta,
calibrazioni), solo sul server e con gli ultimi sette giorni: non va
su GitHub perche' il repository e' pubblico. Viene fatta per prima e
per conto suo, cosi' resta salva anche se il database e' danneggiato o
GitHub non risponde.

PRIMA DI TUTTO controlla che il database sia integro. Se fosse
danneggiato NON lo carica: altrimenti sostituirebbe il backup buono di
ieri con una copia rotta, e si perderebbero entrambi.
"""
import os
import sys
import glob
import gzip
import shutil
import time
import fcntl
import tarfile
import sqlite3
from datetime import date

CODICE = os.environ.get("CARTELLA_CODICE",
                        "/home/ubuntu/previsioni-calcio/previsioni-calcio")
LOCALE = os.environ.get("CARTELLA_BACKUP", "/home/ubuntu/backup")
GIORNI = 7
ATTESA_LUCCHETTO = 600   # secondi di attesa massima per valore.py


def salva_stato():
    """Copia compressa e datata di stato/, presa a valore.py fermo."""
    os.makedirs(LOCALE, exist_ok=True)
    destinazione = os.path.join(LOCALE, f"stato_{date.today()}.tar.gz")

    def da_tenere(info):
        nome = os.path.basename(info.name)
        if nome == "valore.lock" or nome.endswith(".tmp"):
            return None
        return info

    # stesso lucchetto di valore.py: il registro non si copia a meta' giro
    with open(os.path.join("stato", "valore.lock"), "a") as lucchetto:
        fine = time.time() + ATTESA_LUCCHETTO
        preso = False
        while True:
            try:
                fcntl.flock(lucchetto, fcntl.LOCK_EX | fcntl.LOCK_NB)
                preso = True
                break
            except BlockingIOError:
                if time.time() > fine:
                    break
                time.sleep(5)
        if not preso:
            print("  stato: valore.py e' ancora al lavoro, copio lo stesso")
        with tarfile.open(destinazione, "w:gz") as tar:
            tar.add("stato", filter=da_tenere)
    kb = os.path.getsize(destinazione) / 1024
    print(f"  copia di stato: {os.path.basename(destinazione)} ({kb:.0f} KB)")

    copie = sorted(glob.glob(os.path.join(LOCALE, "stato_*.tar.gz")))
    for vecchia in copie[:-GIORNI]:
        os.remove(vecchia)
        print(f"  eliminata la copia vecchia: {os.path.basename(vecchia)}")


def main():
    os.chdir(CODICE)
    sys.path.insert(0, os.path.join(CODICE, "scripts"))
    print(f"BACKUP del {date.today()}")

    # 0. la cartella stato, solo sul server
    try:
        salva_stato()
    except Exception as e:
        print(f"  ERRORE nella copia di stato: {e}")

    # 1. il database e' integro?
    try:
        conn = sqlite3.connect("calcio_dati.db")
        esito = conn.execute("PRAGMA integrity_check").fetchone()[0]
        partite = conn.execute("SELECT COUNT(*) FROM fixtures").fetchone()[0]
        conn.close()
    except sqlite3.Error as e:
        print(f"  ERRORE nell'aprire il database: {e}")
        print("  Backup annullato: la copia precedente resta valida.")
        sys.exit(1)
    print(f"  integrita': {esito}   partite: {partite}")
    if esito != "ok":
        print("  DATABASE DANNEGGIATO: backup annullato.")
        print("  La copia precedente resta valida e va usata per ripristinare.")
        sys.exit(1)

    # 2. copia locale datata
    os.makedirs(LOCALE, exist_ok=True)
    destinazione = os.path.join(LOCALE, f"calcio_dati_{date.today()}.db.gz")
    with open("calcio_dati.db", "rb") as s, \
            gzip.open(destinazione, "wb", compresslevel=6) as d:
        shutil.copyfileobj(s, d)
    mb = os.path.getsize(destinazione) / 1024 / 1024
    print(f"  copia locale: {os.path.basename(destinazione)} ({mb:.1f} MB)")

    copie = sorted(glob.glob(os.path.join(LOCALE, "calcio_dati_*.db.gz")))
    for vecchia in copie[:-GIORNI]:
        os.remove(vecchia)
        print(f"  eliminata la copia vecchia: {os.path.basename(vecchia)}")
    print(f"  copie locali conservate: {min(len(copie), GIORNI)}")

    # 3. copia su GitHub
    try:
        import storage
        if storage.carica_database():
            print("  copia su GitHub: completata")
        else:
            print("  copia su GitHub: NON eseguita (manca il token?)")
            sys.exit(1)
    except Exception as e:
        print(f"  ERRORE nel caricamento su GitHub: {e}")
        print("  La copia locale di oggi e' comunque salva.")
        sys.exit(1)

    print("BACKUP concluso")


if __name__ == "__main__":
    main()
