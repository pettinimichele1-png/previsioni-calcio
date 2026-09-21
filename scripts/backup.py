"""
BACKUP GIORNALIERO DEL DATABASE
================================
Da quando il sistema gira sul server, il database esiste in un solo
posto. Questo script ne fa due copie di sicurezza:

  1. sul server, compressa e datata, tenendo gli ultimi sette giorni:
     protegge da un errore o da un database che si rovina
  2. sulla Release di GitHub, fuori dal server: protegge se il server
     si guasta o viene cancellato

PRIMA DI TUTTO controlla che il database sia integro. Se fosse
danneggiato NON lo carica: altrimenti sostituirebbe il backup buono di
ieri con una copia rotta, e si perderebbero entrambi.
"""
import os
import sys
import glob
import gzip
import shutil
import sqlite3
from datetime import date

CODICE = os.environ.get("CARTELLA_CODICE",
                        "/home/ubuntu/previsioni-calcio/previsioni-calcio")
LOCALE = os.environ.get("CARTELLA_BACKUP", "/home/ubuntu/backup")
GIORNI = 7


def main():
    os.chdir(CODICE)
    sys.path.insert(0, os.path.join(CODICE, "scripts"))
    print(f"BACKUP del {date.today()}")

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
