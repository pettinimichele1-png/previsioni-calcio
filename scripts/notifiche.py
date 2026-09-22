"""
NOTIFICHE
=========
Avvisa il telefono quando, con le formazioni ufficiali, una previsione
cambia molto rispetto a quella fatta con le formazioni probabili.

COMANDI
-------
    python scripts/notifiche.py chiavi    genera le chiavi del server (una volta)
    python scripts/notifiche.py server    servizio che riceve le iscrizioni dei
                                          telefoni (gira sempre, avviato da systemd)
    python scripts/notifiche.py invia     controlla le previsioni e manda le
                                          notifiche (lo lancia il pipeline)
    python scripts/notifiche.py prova     manda una notifica di prova
    python scripts/notifiche.py soglie    quante notifiche al giorno darebbero
                                          soglie diverse, sulle partite archiviate

COME FUNZIONA
-------------
L'app, quando tocchi la campanella, chiede il permesso al telefono e
ottiene un indirizzo privato a cui mandargli messaggi. Lo manda a questo
servizio, che lo conserva. Ogni mezz'ora, dopo il controllo delle
formazioni, "invia" guarda le previsioni con formazioni ufficiali: se una
si e' spostata oltre la soglia rispetto a quella con le probabili, e la
partita non e' ancora iniziata, manda la notifica. Una volta sola per
partita.

Il protocollo e' lo standard delle notifiche web (RFC 8291 per la
cifratura, RFC 8292 per l'identificazione del server), scritto qui
direttamente: serve solo la libreria cryptography.

SOGLIA
------
Spostamento massimo fra 1, X e 2, in punti percentuali. Predefinita 10;
si cambia con la variabile SOGLIA_NOTIFICHE nel file delle chiavi.
"""

import os
import sys
import json
import time
import hmac
import base64
import struct
import sqlite3
import hashlib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CARTELLA_DATI = os.environ.get("NOTIFICHE_DIR",
                               os.path.expanduser("~/previsioni-notifiche"))
CHIAVE_PRIVATA = os.path.join(CARTELLA_DATI, "vapid_privata.pem")
DB_NOTIFICHE = os.path.join(CARTELLA_DATI, "notifiche.db")
SOGGETTO = os.environ.get("NOTIFICHE_SOGGETTO", "https://vps-a07482f3.vps.ovh.net")
SOGLIA = float(os.environ.get("SOGLIA_NOTIFICHE", "4")) / 100
# quanto il favorito deve staccare il secondo esito per dirsi "chiaro"
STACCO_SORPASSO = float(os.environ.get("STACCO_SORPASSO", "3")) / 100
PORTA = int(os.environ.get("NOTIFICHE_PORTA", "8081"))

DB_PATH = "calcio_dati.db"
PREVISIONI = "previsioni.json"
SITO = os.environ.get("SITO_DIR", "docs")
FUSO = ZoneInfo("Europe/Rome")
ESITI = {"1": "la vittoria in casa", "X": "il pareggio", "2": "la vittoria in trasferta"}


# ---------------------------------------------------------------
#  utilita'
# ---------------------------------------------------------------

def b64u(dati):
    return base64.urlsafe_b64encode(dati).rstrip(b"=").decode()


def b64u_dec(testo):
    return base64.urlsafe_b64decode(testo + "=" * (-len(testo) % 4))


def trova(nome):
    for percorso in (nome, os.path.join(SITO, nome)):
        if os.path.exists(percorso):
            return percorso
    return None


def db():
    os.makedirs(CARTELLA_DATI, exist_ok=True)
    c = sqlite3.connect(DB_NOTIFICHE, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS iscrizioni "
              "(endpoint TEXT PRIMARY KEY, dati TEXT, creata TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS inviate "
              "(fixture_id INTEGER PRIMARY KEY, quando TEXT, spostamento REAL)")
    return c


# ---------------------------------------------------------------
#  chiavi del server (VAPID)
# ---------------------------------------------------------------

def carica_chiave():
    from cryptography.hazmat.primitives import serialization
    with open(CHIAVE_PRIVATA, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def pubblica_grezza(chiave):
    from cryptography.hazmat.primitives import serialization
    return chiave.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def comando_chiavi():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    if os.path.exists(CHIAVE_PRIVATA):
        print(f"Le chiavi esistono gia' in {CARTELLA_DATI}: non le rigenero,")
        print("altrimenti tutti i telefoni iscritti smetterebbero di ricevere.")
        return
    os.makedirs(CARTELLA_DATI, exist_ok=True)
    os.chmod(CARTELLA_DATI, 0o700)
    chiave = ec.generate_private_key(ec.SECP256R1())
    pem = chiave.private_bytes(serialization.Encoding.PEM,
                               serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption())
    fd = os.open(CHIAVE_PRIVATA, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    print(f"Chiavi create in {CARTELLA_DATI}")
    print(f"Chiave pubblica: {b64u(pubblica_grezza(chiave))}")


# ---------------------------------------------------------------
#  cifratura del messaggio (RFC 8291, aes128gcm)
# ---------------------------------------------------------------

def _estrai(sale, materiale):
    return hmac.new(sale, materiale, hashlib.sha256).digest()


def _espandi(prk, info, lunghezza):
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:lunghezza]


def cifra(testo, ua_pubblica, auth, privata_server=None, sale=None):
    """
    Cifra il messaggio con le chiavi del telefono: solo quel telefono puo'
    leggerlo. privata_server e sale si passano solo per le prove con
    valori fissi; normalmente sono generati nuovi a ogni messaggio.
    """
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if privata_server is None:
        privata_server = ec.generate_private_key(ec.SECP256R1())
    if sale is None:
        sale = os.urandom(16)
    as_pubblica = pubblica_grezza(privata_server)
    ua = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pubblica)
    segreto = privata_server.exchange(ec.ECDH(), ua)
    prk_chiave = _estrai(auth, segreto)
    ikm = _espandi(prk_chiave, b"WebPush: info\x00" + ua_pubblica + as_pubblica, 32)
    prk = _estrai(sale, ikm)
    cek = _espandi(prk, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _espandi(prk, b"Content-Encoding: nonce\x00", 12)
    cifrato = AESGCM(cek).encrypt(nonce, testo + b"\x02", None)
    intestazione = sale + struct.pack("!I", 4096) + bytes([len(as_pubblica)]) + as_pubblica
    return intestazione + cifrato


# ---------------------------------------------------------------
#  firma del server (RFC 8292)
# ---------------------------------------------------------------

def firma_vapid(chiave, endpoint):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    u = urllib.parse.urlparse(endpoint)
    testa = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    corpo = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}",
                             "exp": int(time.time()) + 12 * 3600,
                             "sub": SOGGETTO}, separators=(",", ":")).encode())
    da_firmare = f"{testa}.{corpo}".encode()
    r, s = decode_dss_signature(chiave.sign(da_firmare, ec.ECDSA(hashes.SHA256())))
    return f"{testa}.{corpo}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def invia_una(chiave, iscrizione, dati, ttl=3600):
    """Restituisce il codice HTTP del servizio di notifiche (201 = ok)."""
    corpo = cifra(json.dumps(dati, ensure_ascii=False).encode(),
                  b64u_dec(iscrizione["keys"]["p256dh"]),
                  b64u_dec(iscrizione["keys"]["auth"]))
    richiesta = urllib.request.Request(iscrizione["endpoint"], data=corpo, method="POST", headers={
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(ttl),
        "Urgency": "high",
        "Authorization": f"vapid t={firma_vapid(chiave, iscrizione['endpoint'])}, "
                         f"k={b64u(pubblica_grezza(chiave))}",
    })
    try:
        with urllib.request.urlopen(richiesta, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError):
        return None


def invia_a_tutti(conn, chiave, dati):
    ok = rimosse = errori = 0
    for endpoint, testo in conn.execute("SELECT endpoint, dati FROM iscrizioni").fetchall():
        codice = invia_una(chiave, json.loads(testo), dati)
        if codice in (200, 201, 202):
            ok += 1
        elif codice in (404, 410):
            # il telefono ha revocato il permesso o disinstallato l'app
            conn.execute("DELETE FROM iscrizioni WHERE endpoint = ?", (endpoint,))
            rimosse += 1
        else:
            errori += 1
            print(f"  errore {codice} verso {urllib.parse.urlparse(endpoint).netloc}")
    conn.commit()
    return ok, rimosse, errori


# ---------------------------------------------------------------
#  il messaggio
# ---------------------------------------------------------------

def _con_articolo(preposizione, n):
    """dal 28% / dall'80%, al 45% / all'11%: davanti a vocale si elide."""
    vocale = n in (1, 8, 11) or 80 <= n <= 89
    return f"{preposizione}ll'{n}%" if vocale else f"{preposizione}l {n}%"


def sorpasso(p):
    """
    Il favorito cambia davvero: era chiaro prima, e' chiaro dopo, e si e'
    invertito. In una partita da 34/34/32 il favorito cambia di continuo
    senza che sia cambiato niente, e quella non e' una notizia.
    Restituisce (nuovo favorito, vecchio favorito) oppure None.
    """
    def chiaro(q):
        ordinati = sorted(q.values(), reverse=True)
        return ordinati[0] - ordinati[1] >= STACCO_SORPASSO

    m, prima = p["mercati"], p["prima"]
    dopo_f = max(("1", "X", "2"), key=lambda k: m[k])
    prima_f = max(("1", "X", "2"), key=lambda k: prima[k])
    if dopo_f != prima_f and chiaro(prima) and chiaro(m):
        return dopo_f, prima_f
    return None


def da_notificare(p):
    return (p.get("spostamento") or 0) >= SOGLIA or sorpasso(p) is not None


def messaggio(p):
    m, prima = p["mercati"], p["prima"]
    lega = (p.get("campionato") or "").split(" - ")[-1]
    ora = datetime.fromisoformat(p["data"]).astimezone(FUSO).strftime("%H:%M")
    cambio = sorpasso(p)
    if cambio:
        nuovo, vecchio = cambio
        return {
            "titolo": f"{p['casa']} – {p['fuori']}",
            "testo": f"Con le formazioni ufficiali passa avanti {ESITI[nuovo]}: "
                     f"{round(m[nuovo] * 100)}% contro {round(m[vecchio] * 100)}%. "
                     f"{lega}, ore {ora}.",
            "url": f"./#/partita/{p['fixture_id']}",
            "tag": f"partita-{p['fixture_id']}",
        }
    esito = max(("1", "X", "2"), key=lambda k: abs(m[k] - prima[k]))
    da, a = round(prima[esito] * 100), round(m[esito] * 100)
    verso = "sale" if a > da else "scende"
    return {
        "titolo": f"{p['casa']} – {p['fuori']}",
        "testo": f"Con le formazioni ufficiali {ESITI[esito]} {verso} "
                 f"{_con_articolo('da', da)} {_con_articolo('a', a)}. {lega}, ore {ora}.",
        "url": f"./#/partita/{p['fixture_id']}",
        "tag": f"partita-{p['fixture_id']}",
    }


# ---------------------------------------------------------------
#  comandi
# ---------------------------------------------------------------

def comando_invia():
    if not os.path.exists(CHIAVE_PRIVATA):
        print("Notifiche non configurate (mancano le chiavi): niente da fare.")
        return
    try:
        chiave = carica_chiave()
    except ImportError:
        print("Libreria cryptography assente: notifiche saltate.")
        return
    percorso = trova(PREVISIONI)
    if not percorso:
        print(f"{PREVISIONI} non trovato.")
        return
    with open(percorso, encoding="utf-8") as f:
        previsioni = json.load(f).get("previsioni", [])

    conn = db()
    n_iscritti = conn.execute("SELECT COUNT(*) FROM iscrizioni").fetchone()[0]
    gia = {r[0] for r in conn.execute("SELECT fixture_id FROM inviate")}
    adesso = datetime.now(timezone.utc)
    candidate = [p for p in previsioni
                 if p.get("formazioni") == "ufficiale" and p.get("prima")
                 and da_notificare(p)
                 and p["fixture_id"] not in gia
                 and datetime.fromisoformat(p["data"]) > adesso]
    print(f"Telefoni iscritti: {n_iscritti}. Da notificare (soglia "
          f"{SOGLIA * 100:.0f} punti, o sorpasso netto): {len(candidate)}")
    if not n_iscritti:
        conn.close()
        return
    for p in candidate:
        dati = messaggio(p)
        ok, rimosse, errori = invia_a_tutti(conn, chiave, dati)
        # si segna come inviata solo se e' arrivata a qualcuno, o se non c'e'
        # stato nessun errore temporaneo: se il servizio di Apple o Google
        # ha avuto un problema, al prossimo giro si riprova
        if ok or not errori:
            conn.execute("INSERT OR REPLACE INTO inviate VALUES (?,?,?)",
                         (p["fixture_id"], adesso.isoformat(), p["spostamento"]))
            conn.commit()
            esito = f"consegnate {ok}"
        else:
            esito = "non consegnata, riprovo al prossimo giro"
        print(f"  {dati['titolo']}: {dati['testo']}  -> {esito}"
              + (f", iscrizioni scadute rimosse {rimosse}" if rimosse else "")
              + (f", errori {errori}" if errori else ""))
    conn.close()


def comando_prova():
    chiave = carica_chiave()
    conn = db()
    n = conn.execute("SELECT COUNT(*) FROM iscrizioni").fetchone()[0]
    print(f"Telefoni iscritti: {n}")
    if not n:
        print("Nessuno: tocca prima la campanella nell'app.")
        return
    ok, rimosse, errori = invia_a_tutti(conn, chiave, {
        "titolo": "Previsioni",
        "testo": "Le notifiche funzionano. Riceverai un avviso quando una "
                 "previsione cambia molto con le formazioni ufficiali.",
        "url": "./", "tag": "prova"})
    print(f"Consegnate: {ok}, iscrizioni scadute rimosse: {rimosse}, errori: {errori}")
    conn.close()


def comando_soglie():
    conn = sqlite3.connect(DB_PATH)
    try:
        righe = conn.execute(
            "SELECT fixture_id, tipo, data, p1, px, p2 FROM archivio_versioni").fetchall()
    except sqlite3.OperationalError:
        print("Archivio delle versioni assente.")
        return
    conn.close()
    prima, dopo = {}, {}
    for fid, tipo, data, a, b, c in righe:
        if tipo == "probabile" or (tipo == "nessuna" and fid not in prima):
            prima[fid] = (a, b, c)
        elif tipo == "ufficiale":
            dopo[fid] = (data, (a, b, c))
    spostamenti = []
    for fid, (data, v) in dopo.items():
        if fid in prima:
            giorno = datetime.fromisoformat(data).astimezone(FUSO).date()
            spostamenti.append((giorno, max(abs(x - y) for x, y in zip(v, prima[fid]))))
    if not spostamenti:
        print("Nessuna partita con entrambe le versioni.")
        return
    giorni = sorted({g for g, _ in spostamenti})
    valori = sorted(s for _, s in spostamenti)
    print(f"Partite con previsione prima e dopo le formazioni ufficiali: "
          f"{len(spostamenti)}, in {len(giorni)} giorni")
    print(f"Spostamento tipico: {valori[len(valori) // 2] * 100:.1f} punti; "
          f"nel 10% dei casi oltre {valori[int(len(valori) * .9)] * 100:.1f}\n")
    print(f"{'soglia':<14}{'notifiche':>10}{'al giorno':>11}{'giorno peggiore':>17}")
    for punti in (2, 3, 4, 5, 8, 10):
        per_giorno = {}
        for g, s in spostamenti:
            if s >= punti / 100:
                per_giorno[g] = per_giorno.get(g, 0) + 1
        tot = sum(per_giorno.values())
        segno = "  <- attuale" if abs(punti / 100 - SOGLIA) < 1e-9 else ""
        print(f"  {punti:>2} punti   {tot:>10}{tot / len(giorni):>11.1f}"
              f"{max(per_giorno.values(), default=0):>17}{segno}")


# ---------------------------------------------------------------
#  servizio che riceve le iscrizioni
# ---------------------------------------------------------------

class Gestore(BaseHTTPRequestHandler):
    def _risposta(self, codice, dati):
        corpo = json.dumps(dati).encode()
        self.send_response(codice)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *argomenti):
        pass

    def do_GET(self):
        if self.path.split("?")[0] == "/api/notifiche/chiave":
            return self._risposta(200, {"chiave": b64u(pubblica_grezza(carica_chiave()))})
        self._risposta(404, {"errore": "non trovato"})

    def do_POST(self):
        percorso = self.path.split("?")[0]
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 < n <= 8192:
                raise ValueError
            dati = json.loads(self.rfile.read(n))
        except (ValueError, json.JSONDecodeError):
            return self._risposta(400, {"errore": "richiesta non valida"})

        endpoint = str(dati.get("endpoint") or "")
        if percorso == "/api/notifiche/iscrivi":
            chiavi = dati.get("keys") or {}
            try:
                p256dh, auth = b64u_dec(chiavi["p256dh"]), b64u_dec(chiavi["auth"])
                valida = (endpoint.startswith("https://") and len(endpoint) < 1000
                          and len(p256dh) == 65 and p256dh[0] == 4 and len(auth) == 16)
            except (KeyError, TypeError, ValueError):
                valida = False
            if not valida:
                return self._risposta(400, {"errore": "iscrizione non valida"})
            conn = db()
            conn.execute("INSERT OR REPLACE INTO iscrizioni VALUES (?,?,?)",
                         (endpoint, json.dumps({"endpoint": endpoint, "keys": {
                             "p256dh": chiavi["p256dh"], "auth": chiavi["auth"]}}),
                          datetime.now(timezone.utc).isoformat()))
            conn.commit()
            conn.close()
            return self._risposta(200, {"ok": True})
        if percorso == "/api/notifiche/disiscrivi":
            conn = db()
            conn.execute("DELETE FROM iscrizioni WHERE endpoint = ?", (endpoint,))
            conn.commit()
            conn.close()
            return self._risposta(200, {"ok": True})
        self._risposta(404, {"errore": "non trovato"})


def comando_server():
    carica_chiave()          # se mancano le chiavi, meglio fermarsi subito
    db().close()
    server = ThreadingHTTPServer(("127.0.0.1", PORTA), Gestore)
    print(f"Servizio notifiche in ascolto su 127.0.0.1:{PORTA}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    comandi = {"chiavi": comando_chiavi, "server": comando_server, "invia": comando_invia,
               "prova": comando_prova, "soglie": comando_soglie}
    if len(sys.argv) != 2 or sys.argv[1] not in comandi:
        print("Uso: python scripts/notifiche.py " + " | ".join(comandi))
        sys.exit(1)
    comandi[sys.argv[1]]()
