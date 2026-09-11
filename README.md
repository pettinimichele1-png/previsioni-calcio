# Sistema di previsione calcistica

Gira interamente su GitHub Actions e pubblica due pagine web
consultabili dal telefono. Nessun computer acceso, nessun comando da
lanciare a mano.

## Cosa fa, e quando (orari italiani)

    07:00        raccoglie le partite giocate, ricalcola tutto il modello,
                 costruisce le formazioni probabili e pubblica le previsioni
    08:00        rigenera le previsioni
    ogni 30 min  cerca le formazioni ufficiali delle partite imminenti
    (11-23)      e aggiorna quelle previsioni

Il cambio d'ora e' gestito da solo: non serve toccare nulla a ottobre.

## Le due pagine

    https://TUO_UTENTE.github.io/TUO_REPO/               previsioni
    https://TUO_UTENTE.github.io/TUO_REPO/verifica.html  verifica

La prima elenca le partite con 1X2. Toccandone una si apre il dettaglio:
doppia chance, over/under 1.5-2.5-3.5, gol/nogol, i tre risultati esatti
piu' probabili, il confronto con le quote dei bookmaker e due indici
(nettezza e affidabilita').

La seconda confronta le previsioni gia' fatte con i risultati poi
avvenuti. E' quella che dice se il sistema sta ancora funzionando, e
misura se abbiamo un vantaggio reale sul mercato.

## Il database

Vive come allegato di una Release, NON dentro il repository: Git conserva
ogni versione di ogni file, e un database di decine di MB lo farebbe
esplodere in poche settimane. Il sistema lo scarica a inizio esecuzione e
lo ricarica compresso alla fine.

Cresce da solo: ogni giorno entrano le partite nuove con statistiche di
squadra, di tutti i giocatori e formazioni.

## I file

    .github/workflows/pipeline.yml   il programma automatico
    scripts/pipeline.py              orchestratore delle fasi
    scripts/storage.py               database sulle Release
    scripts/nucleo.py                calcolo indicatori (usato da tutti)
    scripts/aggiorna_dati.py         partite nuove
    scripts/raccolta_formazioni.py   formazioni delle partite giocate
    scripts/normalizza.py            dati grezzi -> tabella pulita
    scripts/xg_surrogato_v2.py       xG stimato dove manca
    scripts/correzione_divisione.py  chi cambia categoria
    scripts/indicatori.py            forza, forma, contesto
    scripts/forza_avversari.py       forza corretta per avversario
    scripts/indicatori_formazione_v3.py  assenze pesate
    scripts/indicatori_allenatore.py     cambio e permanenza
    scripts/addestra_modello.py      stima dei parametri
    scripts/formazioni_previste.py   probabili e ufficiali
    scripts/previsioni.py            previsioni + pagina
    scripts/verifica.py              archivio e verifica nel tempo
    scripts/confronto_quote.py       diagnostica, uso manuale
    docs/                            il sito pubblicato

## Limiti da conoscere

- I cron di GitHub possono ritardare di 10-30 minuti: la fascia live
  gira ogni 30 minuti proprio per compensare.
- I workflow programmati si disattivano dopo 60 giorni di inattivita'
  del repository: bastano un clic dalla scheda Actions per riattivarli.
- Il modello batte le frequenze medie del 4,8%, con guadagno dimostrato
  statisticamente. E' un margine reale ma modesto, inferiore a quello
  dei bookmaker, che trattengono circa il 7%. Le previsioni sono
  probabilita', non pronostici.
