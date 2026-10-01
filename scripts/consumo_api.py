"""
FRENO SUL CONSUMO DI CHIAMATE ALL'API
=====================================
L'abbonamento ha un tetto di chiamate al giorno (il contatore riparte a
mezzanotte UTC, le 2 di notte in Italia d'estate). Se si sfora, smettono
di funzionare anche le parti che contano davvero: le giocate di valore,
l'intervallo e l'ultima quota di Pinnacle per la prova sulla carta.

Prima dei download di quote piu' pesanti si legge il contatore con
l'endpoint "status", che non consuma chiamate: sopra la soglia quei
download si saltano e si tengono le quote gia' viste. valore.py non
passa di qui e continua a lavorare.

La soglia si cambia con FRENO_CHIAMATE in ~/.previsioni_env.
"""

import os
import json
import urllib.request

API_KEY = os.environ.get("API_FOOTBALL_KEY", "").strip()
SOGLIA = int(os.environ.get("FRENO_CHIAMATE", "6500"))


def chiamate_usate():
    """Chiamate gia' usate oggi, oppure None se il contatore non risponde."""
    if not API_KEY:
        return None
    req = urllib.request.Request("https://v3.football.api-sports.io/status",
                                 headers={"x-apisports-key": API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            dati = json.loads(r.read().decode("utf-8"))
        return int(dati["response"]["requests"]["current"])
    except Exception:
        return None


def frena(cosa):
    """True se oggi si e' vicini al tetto e il download va saltato."""
    usate = chiamate_usate()
    if usate is not None and usate >= SOGLIA:
        print(f"  [freno] gia' {usate} chiamate oggi (soglia {SOGLIA}): salto {cosa}")
        return True
    return False
