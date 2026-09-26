#!/usr/bin/env python3
"""
AGGIUNGE I RISULTATI ESATTI ALLA SEZIONE VERIFICA

Modifica l'app che c'e' gia' sul server invece di sostituirla, cosi'
non si perde niente di quello che contiene. Tocca due righe sole:
l'elenco dei nomi delle categorie e l'elenco delle categorie mostrate
nella tabella della verifica. Poi alza il numero di versione, cosi'
dal telefono si vede che la modifica e' arrivata.

Si puo' rilanciare quante volte si vuole: se il lavoro e' gia' fatto
non fa niente e lo dice.

Uso, dalla cartella del progetto:
    python3 scripts/aggiungi_esatti.py
"""

import os
import re
import sys

APP = os.path.join("app", "app.js")
SW = os.path.join("app", "sw.js")


def leggi(percorso):
    if not os.path.exists(percorso):
        print(f"  Non trovo {percorso}: sei nella cartella giusta?")
        sys.exit(1)
    with open(percorso, encoding="utf-8") as f:
        return f.read()


def scrivi(percorso, testo):
    with open(percorso, "w", encoding="utf-8") as f:
        f.write(testo)


def main():
    app = leggi(APP)
    originale = app

    versione = re.search(r'VERSIONE_APP\s*=\s*"(\d+)"', app)
    if not versione:
        print("  Non trovo il numero di versione dentro app.js. Mi fermo.")
        sys.exit(1)
    print(f"  Versione attuale dell'app: {versione.group(1)}")
    print(f"  Righe del file: {app.count(chr(10)) + 1}")

    fatto = []

    # 1) il nome leggibile della categoria
    if re.search(r'NOMI_CATEGORIE\s*=\s*\{[^}]*esatti\s*:', app):
        print("  - il nome della categoria c'era gia'")
    else:
        nuovo, n = re.subn(
            r'(var NOMI_CATEGORIE\s*=\s*\{[^}]*?)(\s*\})',
            r'\1, esatti: "Risultati esatti"\2', app, count=1)
        if n != 1:
            print("  Non riesco a trovare NOMI_CATEGORIE. Mi fermo senza")
            print("  toccare niente: meglio fermarsi che rompere il file.")
            sys.exit(1)
        app = nuovo
        fatto.append("nome della categoria")

    # 2) la riga della tabella nella sezione verifica
    if re.search(r'\[\s*"singole"[^\]]*"esatti"\s*\]', app):
        print("  - la riga nella tabella c'era gia'")
    else:
        nuovo, n = re.subn(
            r'(\[\s*"singole"\s*,\s*"alta"\s*,\s*"valore"\s*,\s*"sistemi"\s*,\s*"miste")(\s*\])',
            r'\1, "esatti"\2', app, count=1)
        if n != 1:
            print("  Non riesco a trovare l'elenco delle categorie. Mi fermo")
            print("  senza toccare niente.")
            sys.exit(1)
        app = nuovo
        fatto.append("riga nella tabella della verifica")

    if not fatto:
        print("\n  Era gia' tutto a posto: non ho cambiato niente.")
        return

    # 3) numero di versione, cosi' dal telefono si vede che e' arrivata
    nuova = str(int(versione.group(1)) + 1)
    app = re.sub(r'(VERSIONE_APP\s*=\s*")\d+(")', r'\g<1>' + nuova + r'\2',
                 app, count=1)

    # una copia di sicurezza prima di scrivere
    scrivi(APP + ".prima", originale)
    scrivi(APP, app)

    if os.path.exists(SW):
        sw = leggi(SW)
        sw2 = re.sub(r'(VERSIONE\s*=\s*"previsioni-)\d+(")',
                     r'\g<1>' + nuova + r'\2', sw, count=1)
        if sw2 != sw:
            scrivi(SW, sw2)
            print(f"  - aggiornato anche sw.js")

    print("\n  Modificato: " + ", ".join(fatto))
    print(f"  Nuova versione dell'app: {nuova}")
    print(f"  Copia del file di prima: {APP}.prima")
    print("\n  Ora rilancia la pipeline e ricarica l'app dal telefono.")
    print(f"  In fondo alla pagina Verifica deve comparire versione {nuova},")
    print("  e nella tabella delle categorie la riga 'Risultati esatti'")
    print("  (compare solo quando la prima schedina si e' conclusa).")


if __name__ == "__main__":
    main()
