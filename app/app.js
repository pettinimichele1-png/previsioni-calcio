/* Previsioni - logica dell'app.
 * Legge app.json, prodotto ogni mezz'ora dal sistema sul server, e
 * valore.json, scritto da valore.py. Quattro schermate nella barra in
 * basso: Oggi (le giocate di valore da fare adesso), Partite (con la
 * pagina di ogni partita), Giocate (le proposte del modello e i
 * risultati esatti) e Risultati (la prova sulla carta e la verifica del
 * modello).
 */
(function () {
  "use strict";

  var VERSIONE_APP = "20.7";

  var stato = {
    dati: null,
    datiCaricati: false,       // il primo tentativo di scaricare app.json e' finito
    fuoriLinea: false,
    valore: null,              // valore.json: giocate di valore e prova sulla carta
    valoreCaricato: false,     // il primo tentativo di scaricarlo e' finito
    giorno: "tutti",           // partite: giorno scelto
    lega: "tutte",             // partite: campionato scelto
    scheda: "alta",            // giocate: alta, sistemi, miste, esatti
    risultati: "carta",        // risultati: carta o modello
    aperta: false,             // il modello: schedine concluse ieri
    oggiAperto: {},            // oggi: righe "in corso" e "chiuse" aperte
    mercati: {},               // pagina della partita: gruppi di mercati aperti
    grafico: null,             // risultati: i punti del grafico, per il dito
    notifiche: "sconosciuto"   // attive, spente, negate, installa, non-supportate
  };

  var schermo = document.getElementById("schermo");

  // ---------------------------------------------------------------
  //  utilita'
  // ---------------------------------------------------------------
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function pct(v, dec) {
    if (v == null || isNaN(v)) return "–";
    return (v * 100).toFixed(dec || 0).replace(".", ",") + "%";
  }
  function pctSegno(v, dec) {
    if (v == null || isNaN(v)) return "–";
    var s = Math.abs(v * 100).toFixed(dec == null ? 1 : dec).replace(".", ",");
    return (v > 0 ? "+" : v < 0 ? "−" : "") + s + "%";
  }
  function quota(v, dec) {
    if (v == null || isNaN(v)) return "–";
    return Number(v).toFixed(dec == null ? 2 : dec);
  }
  function segno(v) {
    var n = Math.round(v * 100);
    return (n > 0 ? "+" : n < 0 ? "−" : "") + Math.abs(n);
  }
  function euro(v, segnoSempre) {
    if (v == null || isNaN(v)) return "–";
    var s = Math.abs(v).toFixed(2).replace(".", ",").replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    var pre = v < -0.004 ? "−" : (segnoSempre && v > 0.004 ? "+" : "");
    return pre + s + " €";
  }
  function euroTondo(v) {
    return Math.round(v) === v ? v + " €" : euro(v);
  }
  function data(iso) { return new Date(iso); }
  function chiaveGiorno(d) {
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") +
           "-" + String(d.getDate()).padStart(2, "0");
  }
  var GIORNI = ["Dom", "Lun", "Mar", "Mer", "Gio", "Ven", "Sab"];
  var GIORNI_LUNGHI = ["Domenica", "Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato"];
  var MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
              "agosto", "settembre", "ottobre", "novembre", "dicembre"];
  function oraDi(d) {
    return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }
  function dataBreve(d) {
    return GIORNI[d.getDay()] + " " + String(d.getDate()).padStart(2, "0") + "/" +
           String(d.getMonth() + 1).padStart(2, "0");
  }
  function giornoCorto(d) { return d.getDate() + " " + MESI[d.getMonth()].slice(0, 3); }
  function daChiave(chiave) {
    var p = String(chiave).split("-");
    return new Date(+p[0], +p[1] - 1, +p[2]);
  }
  // La giornata delle giocate va dalle 7 alle 7 del giorno dopo, come sul
  // server: le partite sudamericane della notte restano con la sera prima.
  function giornataDi(d) {
    return chiaveGiorno(new Date(d.getFullYear(), d.getMonth(), d.getDate() - (d.getHours() < 7 ? 1 : 0)));
  }
  function ieri() { var d = daChiave(giornataDi(new Date())); d.setDate(d.getDate() - 1); return chiaveGiorno(d); }
  // l'ora di una partita, col giorno davanti se non e' oggi (quelle della notte)
  function oraConGiorno(d) {
    return (chiaveGiorno(d) !== chiaveGiorno(new Date()) ? GIORNI[d.getDay()].toLowerCase() + " " : "") + oraDi(d);
  }
  function relativo(chiave) {
    var oggi = new Date();
    var domani = new Date(); domani.setDate(oggi.getDate() + 1);
    if (chiave === chiaveGiorno(oggi)) return "Oggi";
    if (chiave === chiaveGiorno(domani)) return "Domani";
    return null;
  }
  function titoloGiorno(chiave) {
    var d = daChiave(chiave);
    var base = GIORNI_LUNGHI[d.getDay()] + " " + d.getDate() + " " + MESI[d.getMonth()];
    var r = relativo(chiave);
    return r ? r + " · " + base.toLowerCase() : base;
  }
  function nomeLega(campionato) {
    var parti = String(campionato || "").split(" - ");
    return { paese: parti.length > 1 ? parti[0] : "", nome: parti[parti.length - 1] };
  }
  // Bandierine: il paese arriva in inglese dall'API (es. "Czech-Republic").
  // Inghilterra, Scozia e Galles hanno bandiere proprie, diverse dal Regno Unito.
  var ISO = {
    "italy": "IT", "spain": "ES", "germany": "DE", "france": "FR", "netherlands": "NL",
    "portugal": "PT", "belgium": "BE", "turkey": "TR", "turkiye": "TR", "greece": "GR",
    "austria": "AT", "switzerland": "CH", "denmark": "DK", "sweden": "SE", "norway": "NO",
    "poland": "PL", "czech republic": "CZ", "czechia": "CZ", "croatia": "HR", "serbia": "RS",
    "romania": "RO", "bulgaria": "BG", "ukraine": "UA", "israel": "IL", "finland": "FI",
    "ireland": "IE", "northern ireland": "GB", "japan": "JP", "south korea": "KR",
    "korea republic": "KR", "china": "CN", "saudi arabia": "SA", "qatar": "QA",
    "united arab emirates": "AE", "brazil": "BR", "argentina": "AR", "colombia": "CO",
    "chile": "CL", "peru": "PE", "ecuador": "EC", "uruguay": "UY", "paraguay": "PY",
    "bolivia": "BO", "venezuela": "VE", "usa": "US", "united states": "US", "mexico": "MX",
    "canada": "CA", "hungary": "HU", "slovakia": "SK", "slovenia": "SI", "cyprus": "CY",
    "australia": "AU", "iceland": "IS", "russia": "RU", "egypt": "EG", "morocco": "MA",
    "india": "IN", "iran": "IR", "kazakhstan": "KZ", "belarus": "BY", "lithuania": "LT",
    "latvia": "LV", "estonia": "EE", "bosnia": "BA", "north macedonia": "MK", "albania": "AL",
    "georgia": "GE", "armenia": "AM", "azerbaijan": "AZ", "moldova": "MD", "montenegro": "ME",
    "costa rica": "CR", "wales": "#gbwls", "scotland": "#gbsct", "england": "#gbeng"
  };
  function bandiera(paese) {
    var codice = ISO[String(paese || "").toLowerCase().replace(/[-_]/g, " ").trim()];
    if (!codice) return "";
    if (codice.charAt(0) === "#") {
      var punti = [0x1F3F4];
      codice.slice(1).split("").forEach(function (c) { punti.push(0xE0000 + c.charCodeAt(0)); });
      punti.push(0xE007F);
      return String.fromCodePoint.apply(null, punti);
    }
    return String.fromCodePoint(0x1F1E6 + codice.charCodeAt(0) - 65, 0x1F1E6 + codice.charCodeAt(1) - 65);
  }
  // la partita con la bandiera del paese davanti (Giocate e risultati
  // esatti dalla v19.8, Oggi e giocate di valore dalla v19.9); senza
  // campionato nei dati, solo il nome
  function conBandiera(e) {
    var b = e.campionato ? bandiera(nomeLega(e.campionato).paese) : "";
    return (b ? b + " " : "") + esc(e.partita);
  }
  function trovaPartita(id) {
    var lista = (stato.dati && stato.dati.partite) || [];
    for (var i = 0; i < lista.length; i++) if (String(lista[i].id) === String(id)) return lista[i];
    return null;
  }
  function contaGiocate(n) { return n === 1 ? "1 giocata" : n + " giocate"; }

  // ---------------------------------------------------------------
  //  pezzi grafici ricorrenti
  // ---------------------------------------------------------------
  var PERCORSI = {
    destra: '<path d="M9.5 6l6 6-6 6"></path>',
    sinistra: '<path d="M15 5l-7 7 7 7"></path>',
    giu: '<path d="M6 9.5l6 6 6-6"></path>',
    su: '<path d="M6 14.5l6-6 6 6"></path>',
    orologio: '<circle cx="12" cy="12" r="8.5"></circle><path d="M12 7.5V12l3 2"></path>',
    info: '<circle cx="12" cy="12" r="9"></circle><path d="M12 11v5.5M12 7.9v.2"></path>',
    vinta: '<path d="M5 12.5l4.5 4.5L19 7.5"></path>',
    persa: '<path d="M7 7l10 10M17 7L7 17"></path>',
    avviso: '<path d="M12 3l9.5 17h-19z"></path><path d="M12 10v4M12 17.5v.5"></path>'
  };
  function icona(nome, cls) {
    return '<svg class="ic' + (cls ? " " + cls : "") + '" viewBox="0 0 24 24" aria-hidden="true">' + PERCORSI[nome] + "</svg>";
  }

  function testa(titolo, sotto, destra, grande) {
    return '<header class="testa' + (grande ? " grande" : "") + '"><div class="testa-riga"><div class="testa-testi">' +
           (grande ? '<div class="marchio">Previsioni</div>' : "") +
           '<h1 class="titolo">' + esc(titolo) + "</h1>" +
           (sotto ? '<div class="sottotitolo">' + sotto + "</div>" : "") + "</div>" +
           (destra || "") + "</div></header>";
  }
  function campana() {
    var attiva = stato.notifiche === "attive";
    return '<button class="campana' + (attiva ? " attiva" : "") + '" data-campana="1" aria-label="' +
      (attiva ? "Notifiche attive: tocca per disattivarle" : "Attiva le notifiche") + '">' +
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 16v-5a6 6 0 1 1 12 0v5l1.5 2h-15z"></path>' +
      '<path d="M10 20.5a2 2 0 0 0 4 0"></path></svg></button>';
  }
  function avvisoFuoriLinea() {
    return stato.fuoriLinea
      ? '<div class="fuori-linea">Sei senza rete: stai vedendo gli ultimi dati salvati.</div>' : "";
  }
  function senzaDati() {
    if (!stato.datiCaricati) return '<div class="caricamento">Caricamento…</div>';
    return '<div class="vuoto"><h3>Nessun dato</h3>Non riesco a scaricare le previsioni. Controlla la connessione e riprova.' +
           '<br><button data-ricarica="1">Riprova</button></div>';
  }
  // tre segmenti grigi: il piu' probabile chiaro, gli altri piu' scuri
  function barra3(p) {
    var v = [p["1"], p["X"], p["2"]];
    var ord = v.slice().sort(function (a, b) { return b - a; });
    return '<div class="barra3">' + v.map(function (x) {
      var cls = x === ord[0] ? "forte" : (x === ord[1] ? "medio" : "");
      return '<div class="' + cls + '" style="width:' + (x * 100).toFixed(1) + '%"></div>';
    }).join("") + "</div>";
  }
  function badge(formazioni) {
    if (formazioni === "ufficiale") return '<span class="badge uff">UFF</span>';
    if (formazioni === "probabile") return '<span class="badge">PROB</span>';
    return "";
  }
  // due esiti opposti con la barra: il piu' probabile in chiaro. Con le
  // quote giuste accanto quando le percentuali vengono dal mercato.
  function coppia(nA, a, nB, b, conQuote) {
    var pari = Math.abs(a - b) < 0.02, vinceA = a >= b;
    function lato(nome, p, vince) {
      return '<span class="' + (vince && !pari ? "vince" : "") + '">' + esc(nome) + " <b>" + pct(p) + "</b>" +
        (conQuote ? ' <span class="fioco">' + quota(1 / p) + "</span>" : "") + "</span>";
    }
    return '<div class="coppia"><div class="coppia-testi">' + lato(nA, a, vinceA) + lato(nB, b, !vinceA) + "</div>" +
      '<div class="barra2"><div class="' + (pari ? "pari" : (vinceA ? "forte" : "")) + '" style="width:' + (a * 100).toFixed(1) + '%"></div>' +
      '<div class="' + (pari ? "pari" : (!vinceA ? "forte" : "")) + '" style="width:' + (b * 100).toFixed(1) + '%"></div></div></div>';
  }
  // riquadri con percentuale e quota: il piu' probabile bordato di bianco
  function tessere(voci, conQuote) {
    var massimo = Math.max.apply(null, voci.map(function (x) { return x.p; }));
    return '<div class="griglia3">' + voci.map(function (x) {
      return '<div class="tessera piccola' + (x.p === massimo ? " primo" : "") + '"' + (x.lungo ? ' title="' + esc(x.lungo) + '"' : "") + ">" +
        "<small>" + esc(x.n) + "</small><b>" + pct(x.p) + "</b>" +
        (conQuote ? '<span class="eq">' + quota(1 / x.p) + "</span>" : "") + "</div>";
    }).join("") + "</div>";
  }

  // ---------------------------------------------------------------
  //  GIOCATE DI VALORE (valore.json)
  // ---------------------------------------------------------------
  // In che momento e' una giocata di oggi: si puo' ancora fare, e' in
  // corso, o e' chiusa. Quelle nate all'intervallo si possono fare solo
  // durante la pausa, cioe' per un quarto d'ora.
  function momentoValore(r) {
    if (r.esito === "vinta" || r.esito === "persa" || r.esito === "annullata") return "chiuse";
    var ora = Date.now();
    if (r.intervallo) {
      var nata = r.registrata ? data(r.registrata).getTime() : 0;
      return ora - nata < 15 * 60000 ? "giocare" : "corso";
    }
    return data(r.data).getTime() > ora ? "giocare" : "corso";
  }
  function momentoSchedina(s) {
    if (s.esito === "vinta" || s.esito === "persa" || s.esito === "annullata") return "chiuse";
    return s.prima && data(s.prima).getTime() > Date.now() ? "giocare" : "corso";
  }
  function nomeValore(r) { return String(r.nome || "").replace(/ \(intervallo [^)]*\)$/, ""); }
  function libroValore(r) { return esc(r.book) + (r.commissione ? " exchange" : ""); }
  function puntata() { return (stato.valore && stato.valore.puntata) || 10; }
  function utileValore(r) {
    if (r.esito === "vinta") return (1 + (r.quota - 1) * (1 - (r.commissione || 0)) - 1) * puntata();
    if (r.esito === "persa") return -puntata();
    return 0;
  }
  function utileSchedina(s) {
    if (s.esito === "vinta") return (s.quota - 1) * puntata();
    if (s.esito === "persa") return -puntata();
    return 0;
  }
  function minutiPausa(r) {
    var resto = 15 - (Date.now() - data(r.registrata || 0).getTime()) / 60000;
    return Math.max(1, Math.ceil(resto));
  }
  function linkPartita(fid) {
    return fid != null && trovaPartita(fid) ? "#/partita/" + encodeURIComponent(fid) : null;
  }
  // prima quelle all'intervallo, che scadono; poi le altre
  // in ordine di individuazione, la prima trovata in alto (v20.3); a pari
  // ora prima la partita che comincia prima
  function ordineDaFare(a, b) {
    if (!!a.intervallo !== !!b.intervallo) return a.intervallo ? -1 : 1;
    var ta = a.registrata ? data(a.registrata).getTime() : 0, tb = b.registrata ? data(b.registrata).getTime() : 0;
    return (ta - tb) || (data(a.data) - data(b.data));
  }
  function daFareOggi() {
    var v = stato.valore;
    return ((v && v.oggi) || []).filter(function (r) { return momentoValore(r) === "giocare"; });
  }
  var FAMIGLIE_VALORE = [
    ["esito e doppia chance", "Esito e doppia chance"], ["Under/Over", "Under / Over"],
    ["Gol/NoGol", "Gol / NoGol"], ["primo e secondo tempo", "Primo e secondo tempo"],
    ["all'intervallo", "All'intervallo"]
  ];
  var VERDETTI_VALORE = {
    presto: ["Ancora presto", "Il verdetto arriva dopo {min} giocate confrontate con l'ultima quota di Pinnacle: ora sono {n}. Fino ad allora solo sulla carta."],
    vero: ["Il vantaggio è vero", "Le quote prese battono quelle finali di Pinnacle in modo dimostrato. Si può passare ai soldi veri: puntata fissa, solo alla quota minima o sopra."],
    no: ["Nessun vantaggio", "Le quote prese non battono quelle finali di Pinnacle: con questi bookmaker il metodo non funziona."],
    incerto: ["Non ancora chiaro", "I numeri non dicono ancora né sì né no: si continua sulla carta."]
  };
  function etichettaProva(v) {
    return v.prova && v.prova.giorno <= v.prova.di
      ? "Prova sulla carta · giorno " + v.prova.giorno + " di " + v.prova.di : "Prova sulla carta";
  }

  // una giocata da fare: partita, giocata e bookmaker a sinistra, quota
  // minima a destra. Quelle all'intervallo hanno in piu' il tempo che resta.
  // statistiche del primo tempo e confronto alla ripresa (v20.5)
  function rigaStatPt(st) {
    if (!st) return "";
    var pezzi = [];
    [["tiri", "Tiri"], ["porta", "in porta"], ["angoli", "angoli"], ["possesso", "possesso"], ["xg", "xG"]].forEach(function (n) {
      var x = st[n[0]];
      if (!x || x.length !== 2) return;
      var f = function (v) { return String(v).replace(".", ","); };
      pezzi.push(n[1] + " " + f(x[0]) + "–" + f(x[1]) + (n[0] === "possesso" ? "%" : ""));
    });
    return pezzi.length ? '<span class="val-stat">1° tempo: ' + esc(pezzi.join(" · ")) + "</span>" : "";
  }
  function testoRipresa(x) {
    if (!x) return "";
    if (!x.misurata) return "ripresa non misurabile";
    return "alla ripresa " + quota(x.quota) + " (" + x.minuto + "') · " + pctSegno(x.clv, 0);
  }
  function testoVerdettoRipresa(rp) {
    if (!rp) return "";
    if (!rp.n) return "Confronto alla ripresa: ancora nessuna giocata misurata. Il verdetto arriva dopo " + rp.min_verdetto + ".";
    var base = "Confronto alla ripresa: " + pctSegno(rp.media, 1) + " su " + rp.n + (rp.n === 1 ? " giocata misurata" : " giocate misurate");
    if (rp.verdetto === "vero") return base + ". Il valore c'è: il mercato si avvicina al nostro prezzo.";
    if (rp.verdetto === "no") return base + ". Il valore non c'è: il mercato si allontana dal nostro prezzo.";
    if (rp.verdetto === "incerto") return base + ". Non ancora chiaro.";
    return base + ". Il verdetto arriva dopo " + rp.min_verdetto + ".";
  }
  function cartaValore(r) {
    var href = linkPartita(r.fixture_id);
    var tag = href ? "a" : "div";
    var pausa = !!r.intervallo;
    var html = "<" + tag + ' class="val carta' + (pausa ? " pausa" : "") + '"' + (href ? ' href="' + href + '"' : "") + ">";
    if (pausa) {
      // la partita va sulla riga sotto, a tutta larghezza (v19.7): qui veniva tagliata
      html += '<div class="val-testa"><span class="tag-live">Intervallo ' + esc(r.intervallo) + "</span>" +
        '<span class="val-conto">' + icona("orologio") + "ancora " + minutiPausa(r) + " min</span></div>";
    }
    html += '<div class="val-corpo"><div class="val-sx">';
    html += '<div class="val-riga1">' + (pausa ? "" : '<b class="val-ora">' + oraConGiorno(data(r.data)) + "</b>") +
      '<span class="val-partita">' + conBandiera(r) + "</span></div>" + (pausa ? rigaStatPt(r.stat_pt) : "");
    html += '<span class="val-nome">' + esc(nomeValore(r)) + "</span>" +
      '<span class="val-libro">' + libroValore(r) + " " + quota(r.quota) + ' · <b class="lime">' + pctSegno(r.vantaggio, 0) + "</b></span>" +
      // quando e' stata proposta (v20.0): le quote dell'API possono avere qualche ora
      (r.registrata ? '<span class="val-proposta">proposta alle ' + oraConGiorno(data(r.registrata)) + "</span>" : "") + "</div>" +
      '<div class="val-dx"><span class="val-etich">Minima</span><b class="val-min">' + quota(r.minima) + "</b>" +
      '<span class="val-giusto">giusto ' + quota(r.giusta) + "</span></div>" +
      (href ? icona("destra", "freccia") : "") + "</div></" + tag + ">";
    return html;
  }

  function orarioVoce(v) {
    var oggi = (stato.valore && stato.valore.oggi) || [];
    for (var i = 0; i < oggi.length; i++) {
      if (oggi[i].partita === v.partita && oggi[i].nome === v.nome) return oraConGiorno(data(oggi[i].data));
    }
    return "";
  }
  function nomeSchedina(s) { return s.ora ? "Schedina delle " + String(s.ora).split(":")[0] : "Schedina del giorno"; }
  function cartaSchedina(s) {
    var html = '<article class="schedina carta">' +
      (s.ora ? '<div class="sch-titolo">' + nomeSchedina(s) + " · tutta su " + esc(s.book) + "</div>" : "");
    (s.voci || []).forEach(function (v) {
      var ora = orarioVoce(v);
      var fatto = v.esito === "vinta" ? icona("vinta", "turchese") : v.esito === "persa" ? icona("persa", "arancio") : "";
      html += '<div class="sch-voce"><div class="sch-sx"><b>' + esc(v.nome) + "</b><small>" + conBandiera(v) +
        (ora ? " · " + ora : "") + '</small></div><span class="sch-q">' + fatto + quota(v.quota) + "</span></div>";
    });
    var esito = s.esito === "vinta" ? "Vinta · " : s.esito === "persa" ? "Persa · " : "";
    return html + '<div class="sch-piede"><span>' + esito + "Esce " + Math.round(s.prob * 100) + " volte su 100 · vantaggio " +
      '<b class="lime">' + pctSegno(s.prob * s.quota - 1, 0) + '</b></span><span class="sch-tot"><small>quota</small><b>' +
      quota(s.quota) + "</b></span></div></article>";
  }

  // una riga compatta: in corso (con l'orario) o chiusa (con l'esito e i soldi)
  function rigaGiocata(r, chiusa, conData) {
    var segnoR, dx;
    if (chiusa) {
      segnoR = r.esito === "vinta" ? '<span class="tondo vinta">' + icona("vinta") + "</span>"
        : r.esito === "persa" ? '<span class="tondo persa">' + icona("persa") + "</span>"
        : '<span class="tondo nulla">–</span>';
      var u = utileValore(r);
      dx = '<div class="rg-dx"><b class="' + (u > 0 ? "turchese" : u < 0 ? "arancio" : "fioco") + '">' +
        (r.esito === "annullata" ? "annullata" : euro(u, true)) + "</b><small>quota " + quota(r.quota) + "</small></div>";
    } else {
      segnoR = '<span class="tondo attesa">' + icona("orologio") + "</span>";
      // quasi due ore dopo il calcio d'inizio la partita e' finita: il
      // risultato arriva al primo controllo del server (ogni 15 minuti)
      var finita = Date.now() - data(r.data).getTime() > 115 * 60000;
      dx = '<div class="rg-dx"><small>' + (finita ? "risultato in arrivo" : r.intervallo ? "dall'intervallo" : "dalle " + oraConGiorno(data(r.data))) + "</small></div>";
    }
    var sotto = conBandiera(r);
    if (chiusa) {
      if (r.risultato) sotto += " · finita " + esc(String(r.risultato).split(" ")[0]);
      if (conData) sotto += " · " + giornoCorto(data(r.data));
    } else {
      sotto += " · " + libroValore(r) + " " + quota(r.quota);
    }
    var rip = r.intervallo && r.ripresa ? "<small>" + esc(testoRipresa(r.ripresa)) + "</small>" : "";
    return '<div class="riga-giocata">' + segnoR + '<div class="rg-testi"><b>' + esc(nomeValore(r)) + "</b><small>" + sotto + "</small>" + rip + "</div>" + dx + "</div>";
  }
  function rigaSchedina(s, chiusa) {
    var segnoR, dx;
    if (chiusa) {
      segnoR = s.esito === "vinta" ? '<span class="tondo vinta">' + icona("vinta") + "</span>"
        : s.esito === "persa" ? '<span class="tondo persa">' + icona("persa") + "</span>" : '<span class="tondo nulla">–</span>';
      var u = utileSchedina(s);
      dx = '<div class="rg-dx"><b class="' + (u > 0 ? "turchese" : u < 0 ? "arancio" : "fioco") + '">' +
        (s.esito === "annullata" ? "annullata" : euro(u, true)) + "</b><small>quota " + quota(s.quota) + "</small></div>";
    } else {
      segnoR = '<span class="tondo attesa">' + icona("orologio") + "</span>";
      dx = '<div class="rg-dx"><small>quota ' + quota(s.quota) + "</small></div>";
    }
    var n = (s.voci || []).length;
    return '<div class="riga-giocata">' + segnoR + '<div class="rg-testi"><b>' + nomeSchedina(s) + "</b><small>" + n +
      (n === 1 ? " partita" : " partite") + " · " + esc(s.book) + "</small></div>" + dx + "</div>";
  }

  // ---------------------------------------------------------------
  //  OGGI
  // ---------------------------------------------------------------
  function riepilogoProva(v) {
    // dalla v19.5 valore.json separa le giocate prima della partita (che
    // contano per il verdetto) da quelle all'intervallo; col file vecchio, tutte
    var b = v.bilancio || {};
    var s = (b.prima || b.singole) || { n: 0 };
    var pz = b.prima && b.intervallo && b.intervallo.n ? b.intervallo.n : 0;
    var p = puntata(), num = "0,00 €", cls = "";
    if (s.n) {
      var u = s.utile * p;
      num = euro(u, true);
      cls = u > 0.004 ? "turchese" : u < -0.004 ? "arancio" : "";
    }
    var sotto = s.n
      ? contaGiocate(s.n).replace("giocata", "giocata chiusa").replace("giocate", "giocate chiuse") + " · " + s.vinte + (s.vinte === 1 ? " vinta" : " vinte") + " · " + euroTondo(p) + " a giocata"
      : "Ancora nessuna giocata chiusa · " + euroTondo(p) + " a giocata";
    if (b.prima) sotto = "Prima della partita: " + sotto.charAt(0).toLowerCase() + sotto.slice(1) +
      (pz ? " · più " + pz + " all'intervallo, a parte" : "");
    return '<a class="riepilogo carta" href="#/risultati"><div class="riep-testi"><span class="etichetta">' + etichettaProva(v) +
      '</span><span class="riep-num ' + cls + '">' + num + '</span><span class="riep-sotto">' + sotto + "</span></div>" +
      icona("destra", "freccia") + "</a>";
  }

  function notaOggi() {
    return '<p class="nota-info">' + icona("info") + "<span>Gioca solo se sul tuo sito trovi almeno la quota minima: le quote qui " +
      "possono essere vecchie di qualche ora. Le giocate all'intervallo sono nella sezione Live. " +
      "La giornata va dalle 7 alle 7: le partite della notte restano con la sera prima.</span></p>";
  }

  // ---------------------------------------------------------------
  //  LIVE (dalla v20.1): le giocate durante la partita, per ora quelle
  //  all'intervallo, che prima stavano in Oggi
  // ---------------------------------------------------------------
  function pausaDaGiocare() {
    var v = stato.valore;
    return ((v && v.oggi) || []).filter(function (r) { return r.intervallo && momentoValore(r) === "giocare"; });
  }
  function avvisoLive() {
    var n = pausaDaGiocare().length;
    if (!n) return "";
    return '<a class="riepilogo carta" href="#/live"><div class="riep-testi"><span class="etichetta">Live · all\'intervallo</span>' +
      '<span class="riep-sotto">' + contaGiocate(n) + " da fare adesso, prima che ricominci la partita</span></div>" +
      icona("destra", "freccia") + "</a>";
  }
  function riepilogoLive(v) {
    var pz = ((v.bilancio || {}).intervallo) || { n: 0 };
    var p = puntata(), num = "0,00 €", cls = "";
    if (pz.n) {
      var u = pz.utile * p;
      num = euro(u, true);
      cls = u > 0.004 ? "turchese" : u < -0.004 ? "arancio" : "";
    }
    var sotto = pz.n
      ? contaGiocate(pz.n).replace("giocata", "giocata chiusa").replace("giocate", "giocate chiuse") + " · " + pz.vinte +
        (pz.vinte === 1 ? " vinta" : " vinte") + " (attese " + String(pz.attese).replace(".", ",") + ") · " + euroTondo(p) + " a giocata"
      : "Ancora nessuna giocata all'intervallo chiusa · " + euroTondo(p) + " a giocata";
    var rp = (v.bilancio || {}).ripresa;
    return '<a class="riepilogo carta" href="#/risultati"><div class="riep-testi"><span class="etichetta">All\'intervallo, sulla carta</span>' +
      '<span class="riep-num ' + cls + '">' + num + '</span><span class="riep-sotto">' + sotto + "</span>" +
      (rp ? '<span class="riep-sotto">' + esc(testoVerdettoRipresa(rp)) + "</span>" : "") + "</div>" +
      icona("destra", "freccia") + "</a>";
  }
  function notaLive() {
    return '<p class="nota-info">' + icona("info") + "<span>Le quote all'intervallo sono quelle live di Bet365, aggiornate di continuo: " +
      "gioca prima che ricominci la partita, e solo se sul tuo sito trovi almeno la quota minima. " +
      "Non contano per il verdetto della prova: all'intervallo non c'è una quota di Pinnacle con cui confrontarle.</span></p>";
  }
  function vistaLive() {
    var v = stato.valore;
    var html = testa("Live", "Le giocate durante la partita: per ora all'intervallo", campana()) + avvisoFuoriLinea() + '<div class="corpo">';
    if (!v) {
      if (!stato.valoreCaricato) return html + '<div class="caricamento">Caricamento…</div></div>';
      return html + '<div class="vuoto"><h3>Ancora nessun dato</h3>La pagina si riempie appena valore.py gira sul server.</div>' + notaLive() + "</div>";
    }
    html += riepilogoLive(v);

    var lista = (v.oggi || []).filter(function (r) { return r.intervallo; });
    var gruppi = { giocare: [], corso: [], chiuse: [] };
    lista.forEach(function (r) { gruppi[momentoValore(r)].push(r); });
    gruppi.giocare.sort(ordineDaFare);

    html += '<section class="gruppo"><div class="gruppo-testa"><h2>Da giocare adesso</h2><span>' +
      contaGiocate(gruppi.giocare.length) + "</span></div>";
    if (!gruppi.giocare.length) {
      html += '<div class="vuoto piccolo">Adesso nessuna partita all\'intervallo con una quota di valore. Il server controlla ' +
        "le partite di oggi ogni 5 minuti e manda una notifica appena ne trova una.</div>";
    }
    gruppi.giocare.forEach(function (r) { html += cartaValore(r); });
    html += "</section>";

    var righe = "";
    var corso = gruppi.corso.slice().sort(function (a, b) { return data(a.data) - data(b.data); });
    if (corso.length) {
      righe += rigaApri("live-corso", "In corso", String(corso.length), corso.map(function (r) { return rigaGiocata(r, false); }).join(""));
    }
    var chiuse = gruppi.chiuse.slice().sort(function (a, b) { return data(b.data) - data(a.data); });
    var quando = "oggi";
    if (!chiuse.length) {
      quando = "ieri";
      var giornoPrima = ieri();
      chiuse = (v.ultime || []).filter(function (r) { return r.intervallo && giornataDi(data(r.data)) === giornoPrima; });
    }
    if (chiuse.length) {
      var decise = chiuse.filter(function (r) { return r.esito !== "annullata"; });
      var vinte = decise.filter(function (r) { return r.esito === "vinta"; }).length, soldi = 0;
      chiuse.forEach(function (r) { soldi += utileValore(r); });
      var destra = vinte + " su " + decise.length + ' · <b class="' + (soldi > 0.004 ? "turchese" : soldi < -0.004 ? "arancio" : "") + '">' + euro(soldi, true) + "</b>";
      righe += rigaApri("live-chiuse", "Chiuse " + quando, destra, chiuse.map(function (r) { return rigaGiocata(r, true); }).join(""));
    }
    if (righe) html += '<div class="elenco-righe carta">' + righe + "</div>";
    return html + notaLive() + "</div>";
  }

  function rigaApri(chiave, titolo, destra, dentro) {
    var aperta = !!stato.oggiAperto[chiave];
    return '<button class="riga-apri" data-apri-oggi="' + chiave + '" aria-expanded="' + aperta + '">' +
      '<span class="ra-t1">' + titolo + '</span><span class="ra-dx">' + destra + icona(aperta ? "su" : "giu") + "</span></button>" +
      (aperta ? '<div class="righe-dentro">' + dentro + "</div>" : "");
  }

  function vistaOggi() {
    var v = stato.valore;
    // fino alle 7 del mattino la pagina resta sulla giornata di ieri sera
    var adesso = new Date(), chiaveOggi = giornataDi(adesso), oggi = daChiave(chiaveOggi);
    var sotto = GIORNI_LUNGHI[oggi.getDay()] + " " + oggi.getDate() + " " + MESI[oggi.getMonth()];
    if (v && v.generato) {
      var dg = data(v.generato);
      sotto += " · " + (giornataDi(dg) === chiaveOggi ? "aggiornato alle " + oraDi(dg)
        : "ultimo aggiornamento " + dataBreve(dg) + " " + oraDi(dg));
    }
    var html = testa("Oggi", sotto, campana(), true) + avvisoFuoriLinea() + '<div class="corpo">';
    if (!v) {
      if (!stato.valoreCaricato) return html + '<div class="caricamento">Caricamento…</div></div>';
      return html + '<div class="vuoto"><h3>Ancora nessun dato</h3>La pagina si riempie appena valore.py gira sul server.</div>' + notaOggi() + "</div>";
    }

    html += avvisoLive() + riepilogoProva(v);

    // le giocate all'intervallo stanno in Live (v20.1)
    var lista = (v.oggi || []).filter(function (r) { return !r.intervallo; });
    var gruppi = { giocare: [], corso: [], chiuse: [] };
    lista.forEach(function (r) { gruppi[momentoValore(r)].push(r); });
    gruppi.giocare.sort(ordineDaFare);
    // dalla v20.6 due schedine al giorno (delle 9 e delle 15); col file vecchio una
    var sched = v.schedine || (v.schedina ? [v.schedina] : []);
    var sGiocare = sched.filter(function (x) { return momentoSchedina(x) === "giocare"; });
    var sCorso = sched.filter(function (x) { return momentoSchedina(x) === "corso"; });
    var sChiuse = sched.filter(function (x) { return momentoSchedina(x) === "chiuse"; });

    html += '<section class="gruppo"><div class="gruppo-testa"><h2>Da giocare adesso</h2><span>' +
      contaGiocate(gruppi.giocare.length) + "</span></div>";
    if (!gruppi.giocare.length) {
      html += '<div class="vuoto piccolo">' + (lista.length
        ? "Adesso niente da giocare: le giocate di oggi sono già partite."
        : "Per ora nessun bookmaker paga più del giusto: oggi si salta. Il server ricontrolla ogni ora; quelle all'intervallo sono in Live.") + "</div>";
    }
    gruppi.giocare.forEach(function (r) { html += cartaValore(r); });
    html += "</section>";

    if (sGiocare.length) {
      html += '<section class="gruppo"><div class="gruppo-testa"><h2>' + (sGiocare.length > 1 ? "Schedine del giorno" : "Schedina del giorno") +
        "</h2><span>" + (sGiocare.length > 1 ? sGiocare.length + " schedine" : "tutta su " + esc(sGiocare[0].book)) + "</span></div>" +
        sGiocare.map(cartaSchedina).join("") + "</section>";
    }

    // in corso e chiuse: due righe che si aprono, cosi' la pagina resta corta
    var righe = "";
    var corso = gruppi.corso.slice().sort(function (a, b) { return data(a.data) - data(b.data); });
    var nCorso = corso.length + sCorso.length;
    if (nCorso) {
      var dentro = corso.map(function (r) { return rigaGiocata(r, false); }).join("") +
        sCorso.map(function (x) { return rigaSchedina(x, false); }).join("");
      righe += rigaApri("corso", "In corso", String(nCorso), dentro);
    }
    var chiuse = gruppi.chiuse.slice().sort(function (a, b) { return data(b.data) - data(a.data); });
    var quando = "oggi";
    if (!chiuse.length && !sChiuse.length) {
      quando = "ieri";
      var giornoPrima = ieri();
      chiuse = (v.ultime || []).filter(function (r) { return !r.intervallo && giornataDi(data(r.data)) === giornoPrima; });
    }
    if (chiuse.length || sChiuse.length) {
      var decise = chiuse.filter(function (r) { return r.esito !== "annullata"; });
      var vinte = decise.filter(function (r) { return r.esito === "vinta"; }).length;
      var totale = decise.length, soldi = 0;
      chiuse.forEach(function (r) { soldi += utileValore(r); });
      sChiuse.forEach(function (x) {
        if (x.esito === "annullata") return;
        totale += 1; vinte += x.esito === "vinta" ? 1 : 0; soldi += utileSchedina(x);
      });
      var destra = vinte + " su " + totale + ' · <b class="' + (soldi > 0.004 ? "turchese" : soldi < -0.004 ? "arancio" : "") + '">' + euro(soldi, true) + "</b>";
      var dentroC = chiuse.map(function (r) { return rigaGiocata(r, true); }).join("") +
        sChiuse.map(function (x) { return rigaSchedina(x, true); }).join("");
      righe += rigaApri("chiuse", "Chiuse " + quando, destra, dentroC);
    }
    if (righe) html += '<div class="elenco-righe carta">' + righe + "</div>";

    return html + notaOggi() + "</div>";
  }

  // ---------------------------------------------------------------
  //  PARTITE
  // ---------------------------------------------------------------
  function vistaPartite() {
    var tutte = stato.dati.partite || [];

    // giorni presenti, in ordine
    var giorni = [];
    tutte.forEach(function (m) {
      var k = chiaveGiorno(data(m.data));
      if (giorni.indexOf(k) < 0) giorni.push(k);
    });
    if (stato.giorno !== "tutti" && giorni.indexOf(stato.giorno) < 0) stato.giorno = "tutti";

    // campionati: solo quelli che giocano nel giorno scelto, e se due
    // hanno lo stesso nome si aggiunge il paese
    var delGiorno = tutte.filter(function (m) {
      return stato.giorno === "tutti" || chiaveGiorno(data(m.data)) === stato.giorno;
    });
    var conteggio = {}, perNome = {};
    delGiorno.forEach(function (m) {
      conteggio[m.campionato] = (conteggio[m.campionato] || 0) + 1;
      var n = nomeLega(m.campionato);
      (perNome[n.nome] = perNome[n.nome] || {})[m.campionato] = true;
    });
    var leghe = Object.keys(conteggio).sort(function (a, b) { return conteggio[b] - conteggio[a]; });
    // la bandiera del paese davanti al nome; senza bandiera, il paese
    // scritto quando due campionati hanno lo stesso nome
    function etichettaLega(c) {
      var n = nomeLega(c);
      var b = bandiera(n.paese);
      if (b) return b + " " + n.nome;
      return perNome[n.nome] && Object.keys(perNome[n.nome]).length > 1 && n.paese ? n.nome + " · " + n.paese : n.nome;
    }
    if (stato.lega !== "tutte" && !conteggio[stato.lega]) stato.lega = "tutte";

    var filtrate = tutte.filter(function (m) {
      return (stato.giorno === "tutti" || chiaveGiorno(data(m.data)) === stato.giorno) &&
             (stato.lega === "tutte" || m.campionato === stato.lega);
    });

    var gruppi = [];
    filtrate.forEach(function (m) {
      var k = chiaveGiorno(data(m.data));
      var g = gruppi.length && gruppi[gruppi.length - 1].k === k ? gruppi[gruppi.length - 1] : null;
      if (!g) { g = { k: k, partite: [] }; gruppi.push(g); }
      g.partite.push(m);
    });

    // le partite con una giocata di valore da fare adesso
    var conValore = {};
    daFareOggi().forEach(function (r) { if (r.fixture_id != null) conValore[String(r.fixture_id)] = true; });

    var agg = stato.dati.generato ? oraDi(data(stato.dati.generato)) : "";
    var n = filtrate.length;
    var html = testa("Partite", "Aggiornato alle " + agg + " · " + n + (n === 1 ? " partita" : " partite"), campana());

    html += '<div class="filtri"><div class="riga-chip nascosto-scroll">';
    html += '<button class="chip-giorno' + (stato.giorno === "tutti" ? " attivo" : "") +
            '" data-giorno="tutti"><span class="su">Tutti</span><span class="giu">' + giorni.length + " gg</span></button>";
    giorni.forEach(function (k) {
      var d = daChiave(k);
      var r = relativo(k);
      html += '<button class="chip-giorno' + (stato.giorno === k ? " attivo" : "") + '" data-giorno="' + k +
              '"><span class="su">' + (r || GIORNI[d.getDay()]) + '</span><span class="giu">' + d.getDate() + "</span></button>";
    });
    html += '</div><div class="riga-chip nascosto-scroll">';
    html += '<button class="chip leggero' + (stato.lega === "tutte" ? " attivo" : "") + '" data-lega="tutte">Tutti</button>';
    leghe.forEach(function (c) {
      html += '<button class="chip leggero' + (stato.lega === c ? " attivo" : "") + '" data-lega="' + esc(c) + '">' +
              esc(etichettaLega(c)) + "</button>";
    });
    html += "</div></div>" + avvisoFuoriLinea() + '<div class="corpo">';

    if (!filtrate.length) {
      html += '<div class="vuoto"><h3>Nessuna partita</h3>Con questi filtri non ci sono partite in programma.' +
              '<br><button data-azzera="1">Mostra tutto</button></div>';
    } else {
      html += '<p class="nota">Percentuali dalle quote dei bookmaker, senza il loro margine. Dove non ci sono quote, quelle del nostro modello.</p>';
    }
    gruppi.forEach(function (g) {
      html += '<section class="gruppo"><div class="gruppo-testa"><h2>' + esc(titoloGiorno(g.k)) + "</h2><span>" +
              g.partite.length + (g.partite.length === 1 ? " partita" : " partite") + "</span></div>";
      g.partite.forEach(function (m) {
        var d = data(m.data);
        var mk = m.mercato && m.mercato["1"] != null ? m.mercato : null;
        var p = mk || m.p;
        var max = Math.max(p["1"], p["X"], p["2"]);
        html += '<a class="partita carta" href="#/partita/' + encodeURIComponent(m.id) + '">' +
          '<div class="riga"><div class="ora"><b>' + oraDi(d) + "</b><span>" + esc(etichettaLega(m.campionato)) + "</span></div>" +
          '<div class="etichette">' + badge(m.formazioni) + (mk ? "" : '<span class="badge">MODELLO</span>') +
          (conValore[String(m.id)] ? '<span class="tag-valore">VALORE</span>' : "") + "</div></div>" +
          '<div class="riga"><div class="squadre"><span>' + esc(m.casa) + "</span><span>" + esc(m.fuori) + "</span></div>" +
          '<div class="pillole">' + ["1", "X", "2"].map(function (k) {
            return '<div class="pillola' + (p[k] === max ? " forte" : "") + '"><small>' + k + "</small><b>" + pct(p[k]) + "</b></div>";
          }).join("") + "</div></div>" + barra3(p) + "</a>";
      });
      html += "</section>";
    });
    return html + "</div>";
  }

  // ---------------------------------------------------------------
  //  PAGINA DELLA PARTITA
  // ---------------------------------------------------------------
  function finaleDi(m) {
    var f = stato.valore && stato.valore.finale;
    return f ? f[String(m.id)] || null : null;
  }
  // da dove vengono le percentuali dell'esito: Pinnacle (partite di oggi),
  // altrimenti la media dei bookmaker, altrimenti il nostro modello
  function fonteEsito(m) {
    var f = finaleDi(m);
    if (f && f["1"] != null && f["X"] != null && f["2"] != null) {
      return { p: { "1": f["1"], X: f["X"], "2": f["2"] },
               dc: { "1X": f["1X"] != null ? f["1X"] : f["1"] + f["X"],
                     "12": f["12"] != null ? f["12"] : f["1"] + f["2"],
                     "X2": f["X2"] != null ? f["X2"] : f["X"] + f["2"] },
               nome: "Pinnacle, senza margine", mercato: true };
    }
    var mk = m.mercato;
    if (mk && mk["1"] != null) {
      return { p: { "1": mk["1"], X: mk["X"], "2": mk["2"] },
               dc: { "1X": mk["1"] + mk["X"], "12": mk["1"] + mk["2"], "X2": mk["X"] + mk["2"] },
               nome: mk.bookmaker ? "media di " + mk.bookmaker + " bookmaker" : "media dei bookmaker", mercato: true };
    }
    return { p: m.p, dc: m.dc, nome: "dal nostro modello", mercato: false };
  }
  function fonteGol(m) {
    var f = finaleDi(m);
    if (f && f.over25 != null && f.under25 != null) {
      var righe = [["Over 1.5", "over15", "Under 1.5", "under15"], ["Over 2.5", "over25", "Under 2.5", "under25"],
                   ["Over 3.5", "over35", "Under 3.5", "under35"]]
        .filter(function (r) { return f[r[1]] != null && f[r[3]] != null; })
        .map(function (r) { return [r[0], f[r[1]], r[2], f[r[3]]]; });
      return { righe: righe, gg: f.gol_gol != null && f.no_gol != null ? ["Gol", f.gol_gol, "NoGol", f.no_gol] : null,
               nome: "Pinnacle, senza margine", quote: true };
    }
    return { righe: [["Over 1.5", m.gol.over15, "Under 1.5", m.gol.under15], ["Over 2.5", m.gol.over25, "Under 2.5", m.gol.under25],
                     ["Over 3.5", m.gol.over35, "Under 3.5", m.gol.under35]],
             gg: ["Gol", m.gg.gol, "NoGol", m.gg.nogol], nome: "dal nostro modello", quote: false };
  }

  // le giocate di valore di questa partita, se ce ne sono da fare adesso
  function bloccoValore(m) {
    var v = stato.valore;
    var mie = ((v && v.oggi) || []).filter(function (r) {
      return String(r.fixture_id) === String(m.id) && momentoValore(r) === "giocare";
    });
    if (!mie.length) return "";
    var html = '<section class="blocco carta valore-blocco"><h2>' + (mie.length === 1 ? "Giocata di valore" : "Giocate di valore") + "</h2>";
    mie.forEach(function (r) {
      html += '<div class="vb"><div class="vb-testa"><span class="vb-nome">' + esc(nomeValore(r)) + '</span><span class="tag-valore grande">' +
        pctSegno(r.vantaggio, 0) + "</span></div>" +
        (r.intervallo ? '<span class="vb-pausa">Intervallo ' + esc(r.intervallo) + " · ancora " + minutiPausa(r) + " min</span>" : "") +
        '<div class="griglia3"><div class="vb-num"><small>' + libroValore(r) + "</small><b>" + quota(r.quota) + "</b></div>" +
        '<div class="vb-num"><small>Giusto</small><b class="fioco">' + quota(r.giusta) + "</b></div>" +
        '<div class="vb-num minima"><small>Minima</small><b class="lime">' + quota(r.minima) + "</b></div></div></div>";
    });
    return html + '<span class="nota">Gioca solo se sul tuo sito trovi almeno la quota minima.</span></section>';
  }

  function bloccoEsito(m) {
    var f = fonteEsito(m);
    var max = Math.max(f.p["1"], f.p["X"], f.p["2"]);
    var html = '<section class="blocco carta"><div class="blocco-testa"><h2>Esito finale</h2><span>' + esc(f.nome) + "</span></div>" +
      '<div class="griglia3">' + ["1", "X", "2"].map(function (k) {
        return '<div class="tessera' + (f.p[k] === max ? " primo" : "") + '"><small>' + k + "</small><b>" + pct(f.p[k]) + "</b>" +
          (f.mercato ? '<span class="eq">' + quota(1 / f.p[k]) + "</span>" : "") + "</div>";
      }).join("") + "</div>" + barra3(f.p);
    html += '<div class="doppie">' + ["1X", "12", "X2"].map(function (k) {
      return "<div><small>" + k + "</small><b>" + pct(f.dc[k]) + "</b>" + (f.mercato ? '<span class="eq">' + quota(1 / f.dc[k]) + "</span>" : "") + "</div>";
    }).join("") + "</div></section>";
    return html + bloccoConfronto(m, f);
  }

  // il nostro modello contro il mercato, esito per esito, come nella
  // versione 17: stesso mercato delle percentuali qui sopra
  function bloccoConfronto(m, f) {
    if (!f.mercato) return "";
    var mk = m.mercato || {};
    var fonte = f.nome.indexOf("Pinnacle") === 0 ? "Mercato: le quote di Pinnacle, margine già tolto."
      : "Mercato: media di " + (mk.bookmaker || "più") + " bookmaker" + (mk.margine != null ? ", margine del " + pct(mk.margine, 1) : "") + " già tolto.";
    var scarti = ["1", "X", "2"].map(function (k) { return m.p[k] - f.p[k]; });
    var massimo = Math.max.apply(null, scarti.map(Math.abs));
    return '<section class="blocco carta"><h2>Confronto con i bookmaker</h2><div class="confronto">' +
      '<span class="ti"></span><span class="ti dx">NOI</span><span class="ti dx">MERCATO</span><span class="ti dx">SCARTO</span>' +
      ["1", "X", "2"].map(function (k, i) {
        return '<span class="v">' + k + '</span><span class="v dx">' + pct(m.p[k]) + '</span><span class="v dx fioco">' + pct(f.p[k]) +
          '</span><span class="v dx ' + (Math.abs(scarti[i]) >= 0.005 ? "chiaro" : "fioco") + '">' + segno(scarti[i]) + "</span>";
      }).join("") + '</div><span class="nota">' + fonte + "</span>" +
      (massimo >= 0.15 ? '<div class="avviso">' + icona("avviso") + "<span>Divergenza forte. Nei nostri test, quando ci allontaniamo così dal mercato di solito sbagliamo noi.</span></div>" : "") +
      "</section>";
  }

  function bloccoGol(m) {
    var f = fonteGol(m);
    var html = '<section class="blocco carta"><div class="blocco-testa"><h2>Gol</h2><span>' + esc(f.nome) + "</span></div>";
    f.righe.forEach(function (r) { html += coppia(r[0], r[1], r[2], r[3], f.quote); });
    if (f.gg) html += '<div class="separato">' + coppia(f.gg[0], f.gg[1], f.gg[2], f.gg[3], f.quote) + "</div>";
    return html + "</section>";
  }

  // PRIMO E SECONDO TEMPO: le quote giuste arrivano da valore.json,
  // ricavate dalle quote di Pinnacle e corrette su 35.000 partite. Ci
  // sono solo per le partite del giorno con le quote di Pinnacle.
  function gruppiTempi(p, m) {
    function coppiaT(n1, k, n2) {
      return p[k] == null ? [] : [{ n: n1, p: p[k] }, { n: n2, p: 1 - p[k] }];
    }
    function tess(elenco) {
      return elenco.filter(function (x) { return p[x[1]] != null; })
        .map(function (x) { return { n: x[0], p: p[x[1]] }; });
    }
    function tempo(suf, titolo) {
      return [
        { t: "Esito " + titolo, f: "tessere",
          v: tess([["1", "1" + suf], ["X", "X" + suf], ["2", "2" + suf],
                   ["1X", "1X" + suf], ["12", "12" + suf], ["X2", "X2" + suf]]) },
        { t: "Gol " + titolo, f: "coppie",
          v: [].concat(coppiaT("Over 0.5", "over05" + suf, "Under 0.5"),
                       coppiaT("Over 1.5", "over15" + suf, "Under 1.5"),
                       coppiaT("Over 2.5", "over25" + suf, "Under 2.5"),
                       coppiaT("Gol", "gol_gol" + suf, "NoGol"),
                       coppiaT(m.casa + " segna", "casa_segna" + suf, "Non segna"),
                       coppiaT(m.fuori + " segna", "fuori_segna" + suf, "Non segna")) }
      ];
    }
    return [
      { chiave: "tempi-primo", t: "Primo tempo", sotto: "Esito e gol", gruppi: tempo("_pt", "primo tempo") },
      { chiave: "tempi-secondo", t: "Secondo tempo", sotto: "Esito e gol", gruppi: tempo("_st", "secondo tempo") },
      { chiave: "tempi-due", t: "Tra i due tempi", sotto: "Gol in entrambi, tempo con più gol", gruppi: [
        { t: "Gol in tutti e due i tempi", f: "coppie",
          v: [].concat(coppiaT("Gol in entrambi", "gol_entrambi_tempi", "No"),
                       coppiaT(m.casa + " in entrambi", "casa_segna_entrambi", "No"),
                       coppiaT(m.fuori + " in entrambi", "fuori_segna_entrambi", "No")) },
        { t: "Tempo con più gol", f: "tessere",
          v: tess([["1° tempo", "pt_piu_gol"], ["Pari", "tempi_pari_gol"], ["2° tempo", "st_piu_gol"]]) }] }
    ].map(function (r) {
      r.gruppi = r.gruppi.filter(function (g) { return g.v.length; });
      return r;
    }).filter(function (r) { return r.gruppi.length; });
  }

  function corpoGruppo(g, conQuote) {
    if (g.f === "coppie") {
      var html = "";
      for (var i = 0; i + 1 < g.v.length; i += 2) html += coppia(g.v[i].n, g.v[i].p, g.v[i + 1].n, g.v[i + 1].p, conQuote);
      return '<div class="coppie">' + html + "</div>";
    }
    return tessere(g.v, true);
  }

  // una lista di righe che si aprono una per volta; ogni riga puo'
  // contenere piu' gruppi, ciascuno con il suo titoletto
  function listaApri(righe, conQuote) {
    var html = '<div class="carta lista-apri">';
    righe.forEach(function (r) {
      var aperta = !!stato.mercati[r.chiave];
      var n = r.gruppi.reduce(function (s, g) { return s + g.v.length; }, 0);
      html += '<button class="riga-apri" data-mercato="' + esc(r.chiave) + '" aria-expanded="' + aperta + '">' +
        '<span class="ra-testi"><span class="ra-t1">' + esc(r.t) + '</span><span class="ra-t2">' + esc(r.sotto) +
        (r.senzaConta ? "" : " · " + n + (n === 1 ? " esito" : " esiti")) + "</span></span>" + icona(aperta ? "su" : "giu") + "</button>";
      if (aperta) {
        html += '<div class="ra-dentro">' + r.gruppi.map(function (g) {
          return (r.gruppi.length > 1 || g.titoletto ? '<span class="sotto-t">' + esc(g.t) + "</span>" : "") + corpoGruppo(g, conQuote);
        }).join("") + "</div>";
      }
    });
    return html + "</div>";
  }

  function bloccoTempi(m) {
    var p = stato.valore && stato.valore.tempi && stato.valore.tempi[String(m.id)];
    if (!p) return "";
    var righe = gruppiTempi(p, m);
    if (!righe.length) return "";
    return '<section class="blocco elenco"><h2>Primo e secondo tempo</h2>' + listaApri(righe, true) +
      '<p class="nota">Dalle quote di Pinnacle, corrette su 35.000 partite. Accanto a ogni percentuale c\'è la quota giusta: ' +
      "conviene solo se il bookmaker paga almeno il 5% in più.</p></section>";
  }

  // gli altri mercati del nostro modello, raccolti in poche righe
  var RIGHE_ALTRI = [
    { t: "Multigol", sotto: "Totale e di squadra", da: ["Multigol", "Multigol di squadra"] },
    { t: "Gol delle squadre", sotto: "Segna, Over e Under di ciascuna", da: ["Gol di {casa}", "Gol di {fuori}"] },
    { t: "Combinazioni", sotto: "Esito con Under/Over, Gol, Multigol", da: ["Combinazioni"] },
    { t: "Handicap e altri totali", sotto: "Scarto, Over 4.5, pari e dispari", da: ["Scarto e handicap", "Altri totali"] }
  ];
  function bloccoAltri(m) {
    var righe = [];
    if (m.esatti && m.esatti.length) {
      righe.push({ chiave: "altri-esatti", t: "Risultati esatti", sotto: m.esatti.length === 1 ? "Il più probabile" : "I " + m.esatti.length + " più probabili",
                   senzaConta: true,
                   gruppi: [{ t: "Risultati esatti", f: "tessere", v: m.esatti.map(function (e) { return { n: e.r, p: e.p }; }) }] });
    }
    var vocab = (stato.dati && stato.dati.mercati) || [];
    if (m.altri && m.altri.length && vocab.length) {
      var gruppi = {};
      vocab.forEach(function (g, i) {
        var valori = m.altri[i] || [];
        var gr = {
          t: g.t.replace("{casa}", m.casa).replace("{fuori}", m.fuori), f: g.f,
          v: g.n.map(function (nome, j) {
            return { n: nome, lungo: (g.lunghi || [])[j] || nome, p: (valori[j] || 0) / 1000 };
          })
        };
        // nei riquadri gli esiti sotto lo 0,5% si tolgono; nelle coppie
        // no, altrimenti si scompagnano e la barra non torna
        if (gr.f !== "coppie") gr.v = gr.v.filter(function (x) { return x.p > 0; });
        gruppi[g.t] = gr;
      });
      var usati = {};
      RIGHE_ALTRI.forEach(function (r) {
        var dentro = r.da.map(function (t) { usati[t] = true; return gruppi[t]; })
          .filter(function (g) { return g && g.v.length; });
        if (dentro.length) righe.push({ chiave: "altri-" + r.t, t: r.t, sotto: r.sotto, gruppi: dentro });
      });
      // un gruppo nuovo, che qui non e' previsto, ha una riga sua
      vocab.forEach(function (g) {
        if (!usati[g.t] && gruppi[g.t] && gruppi[g.t].v.length) {
          righe.push({ chiave: "altri-" + g.t, t: gruppi[g.t].t, sotto: "Dal nostro modello", gruppi: [gruppi[g.t]] });
        }
      });
    }
    if (!righe.length) return "";
    return '<section class="blocco elenco"><h2>Altri mercati</h2>' + listaApri(righe, true) +
      '<p class="nota">Dal nostro modello. Il numero piccolo è la quota equa, cioè il rovescio della percentuale: non è un consiglio di gioco.</p></section>';
  }

  function bloccoFiducia(m) {
    var etichettaForm = m.formazioni === "ufficiale" ? "Formazioni ufficiali" :
                        m.formazioni === "probabile" ? "Formazioni probabili" : "Formazioni non note";
    var etichetta = m.affidabilita_etichetta || "bassa";
    var storia = m.storia || [];
    var html = '<section class="blocco carta"><h2>Quanto fidarsi del nostro modello</h2>' +
      '<div class="indice"><div class="testi"><span>Affidabilità <span class="tag">' + esc(etichetta.toUpperCase()) +
      '</span></span><span class="num">' + Math.round(m.affidabilita || 0) + "<small>/100</small></span></div>" +
      '<div class="traccia"><div style="width:' + Math.max(0, Math.min(100, m.affidabilita || 0)) + '%"></div></div>' +
      '<span class="spiega">Storico di ' + esc(storia[0] == null ? "–" : storia[0]) + " e " + esc(storia[1] == null ? "–" : storia[1]) +
      " partite · " + etichettaForm.toLowerCase() + "</span></div>";
    // prima e dopo le formazioni ufficiali: la notifica porta qui
    if (m.prima && m.formazioni === "ufficiale") {
      html += '<div class="confronto"><span class="ti"></span><span class="ti dx">STIMATE</span><span class="ti dx">UFFICIALI</span><span class="ti dx">SCARTO</span>' +
        ["1", "X", "2"].map(function (k) {
          var s = m.p[k] - m.prima[k];
          return '<span class="v">' + k + '</span><span class="v dx fioco">' + pct(m.prima[k]) + '</span><span class="v dx">' + pct(m.p[k]) +
                 '</span><span class="v dx ' + (Math.abs(s) >= 0.005 ? "chiaro" : "fioco") + '">' + segno(s) + "</span>";
        }).join("") + "</div>";
    }
    if (m.attesi && m.attesi[0] != null) {
      html += '<div class="attesi"><span>Gol attesi</span><b>' + quota(m.attesi[0]) + ' <span class="fioco">–</span> ' + quota(m.attesi[1]) + "</b></div>";
    }
    return html + "</section>";
  }

  function vistaDettaglio(id) {
    var m = trovaPartita(id);
    var indietro = '<a class="indietro" href="#/partite">' + icona("sinistra") + "Partite</a>";
    if (!m) return indietro + '<div class="vuoto"><h3>Partita non trovata</h3>Potrebbe essere già stata giocata.</div>';

    var d = data(m.data);
    var lega = nomeLega(m.campionato);
    var etichettaForm = m.formazioni === "ufficiale" ? "Formazioni ufficiali" :
                        m.formazioni === "probabile" ? "Formazioni probabili" : "Formazioni non note";
    var html = indietro + '<div class="eroe"><div class="riga"><span class="lega">' + bandiera(lega.paese) + " " + esc(lega.nome) +
      (lega.paese ? " · " + esc(lega.paese) : "") + '</span><span class="badge' + (m.formazioni === "ufficiale" ? " uff" : "") + '">' +
      etichettaForm.toUpperCase() + '</span></div><h1 class="nomi"><span class="nome">' + esc(m.casa) +
      '</span><span class="contro">contro</span><span class="nome">' + esc(m.fuori) + "</span></h1>" +
      '<div class="quando">' + GIORNI_LUNGHI[d.getDay()] + " " + d.getDate() + " " + MESI[d.getMonth()] + " · <b>" + oraDi(d) + "</b></div></div>";

    html += bloccoValore(m) + bloccoEsito(m) + bloccoGol(m) + bloccoTempi(m) + bloccoAltri(m) + bloccoFiducia(m);
    return html + '<p class="nota-piccola centro">Sono probabilità, non certezze: un esito al 60% non si verifica quattro volte su dieci.</p>';
  }

  // ---------------------------------------------------------------
  //  GIOCATE: le proposte del modello e i risultati esatti
  // ---------------------------------------------------------------
  var SCHEDE = [
    { id: "alta", nome: "Alta probabilità", testo: "Esiti molto probabili, uno per partita, combinati fino a superare quota 1.45." },
    { id: "sistemi", nome: "Sistemi", testo: "Più esiti sulla stessa partita: basta che in ogni partita se ne avveri almeno uno." },
    { id: "miste", nome: "Miste", testo: "Schedine attorno a quota 5, 10 e 17, solo con esiti sopra il 30%." },
    { id: "esatti", nome: "Esatti", testo: "I cinque risultati esatti più probabili di tutto il palinsesto." }
  ];

  function corpoEsatti() {
    var lista = stato.dati.esatti || [];
    var html = '<div class="avviso">' + icona("avviso") +
      "<span>La parte meno verificata del modello. Anche il punteggio più probabile esce raramente più di una volta su sette.</span></div>";
    if (!lista.length) html += '<div class="vuoto"><h3>Nessuna partita</h3>Non ci sono partite in programma.</div>';
    lista.forEach(function (r, i) {
      html += '<div class="esatto carta' + (r.p >= 0.15 ? " netto" : "") + '"><div class="pos' + (i === 0 ? " primo" : "") + '">' + (i + 1) +
        '</div><div class="punteggio">' + esc(r.ris) + '</div><div class="info"><b>' + conBandiera(r) + "</b><small>" + esc(r.info) +
        "</small><span>stacca il secondo di " + (r.distacco * 100).toFixed(1).replace(".", ",") + ' punti</span></div><div class="perc">' +
        pct(r.p, 1) + "</div></div>";
    });
    return html;
  }

  function vistaGiocate() {
    var scheda = SCHEDE.filter(function (s) { return s.id === stato.scheda; })[0];
    if (!scheda) { stato.scheda = "alta"; scheda = SCHEDE[0]; }
    var g = stato.dati.giocate || {};
    var fatte = g.generato ? "Proposte di oggi, fatte alle " + oraDi(data(g.generato)) : "Le proposte del nostro modello";
    var html = testa("Giocate", fatte) + '<div class="filtri"><div class="riga-chip stretta nascosto-scroll">' +
      SCHEDE.map(function (s) {
        return '<button class="chip' + (stato.scheda === s.id ? " attivo" : "") + '" data-scheda="' + s.id + '">' + s.nome + "</button>";
      }).join("") + "</div></div>" + avvisoFuoriLinea() + '<div class="corpo"><p class="nota">' + scheda.testo + "</p>";

    if (stato.scheda === "esatti") {
      html += corpoEsatti();
    } else {
      var carte = g[stato.scheda] || [];
      if (!carte.length) {
        var spiega = (g.note || {})[stato.scheda] || "Oggi il modello non trova giocate di questo tipo.";
        html += '<div class="vuoto"><h3>Nessuna proposta</h3>' + esc(spiega) + "</div>";
      }
      carte.forEach(function (c) {
        var sistema = stato.scheda === "sistemi";
        var q = sistema ? quota(c.quota, 1) + "–" + quota(c.quota_max, 0) : quota(c.quota);
        var titolo = sistema && c.combinazioni ? c.titolo + " · " + c.combinazioni + " combinazioni" : c.titolo;
        html += '<article class="giocata carta"><div class="giocata-testa"><span>' + esc(titolo) +
          '</span><div class="quota"><small>quota</small><b>' + q + "</b></div></div>";
        (c.eventi || []).forEach(function (e) {
          html += '<div class="evento"><div class="sx"><b>' + conBandiera(e) + "</b><small>" + esc(e.info) +
            '</small></div><div class="dx2"><span class="esito">' + esc(e.esito) + "</span><small>" +
            (e.almeno_uno ? "almeno uno " : "") + pct(e.p) + "</small></div></div>";
        });
        var piede = sistema ? "probabilità di vincere almeno una combinazione" : "probabilità che esca tutto";
        html += '<div class="piede' + (c.prob < 0.35 ? " rischio" : "") + '"><span>' + piede + "</span><b>" + pct(c.prob) + "</b></div></article>";
      });
    }

    return html + '<p class="nota-info">' + icona("info") + "<span>Proposte fatte al mattino dal nostro modello, senza un vantaggio dimostrato: " +
      "il mercato resta più preciso. Nelle multiple il margine del bookmaker si moltiplica: circa 7% su una singola, 14% su una doppia, " +
      "22% su una tripla. Le giocate con vantaggio sono in Oggi.</span></p></div>";
  }

  // ---------------------------------------------------------------
  //  RISULTATI: la prova sulla carta e la verifica del modello
  // ---------------------------------------------------------------
  // il grafico della prova, giocata per giocata; il dito ci scorre sopra
  function graficoProva(serie) {
    var p = puntata();
    var punti = [0], giorni = [null];
    serie.forEach(function (x) { punti.push(punti[punti.length - 1] + x[1] * p); giorni.push(x[0]); });
    var W = 326, H = 128, sx = 18, dx = 10, su = 12, giu = 12;
    var min = Math.min(0, Math.min.apply(null, punti)), max = Math.max(0, Math.max.apply(null, punti));
    var margine = (max - min) * 0.08 || 5;
    min -= margine; max += margine;
    var n = punti.length - 1;
    function X(i) { return sx + (W - sx - dx) * i / n; }
    function Y(v) { return su + (H - su - giu) * (1 - (v - min) / (max - min)); }
    var ultimo = punti[n], giu2 = ultimo < 0;
    stato.grafico = { punti: punti, giorni: giorni, W: W, X: X, Y: Y };
    var linea = punti.map(function (v, i) { return (i ? "L" : "M") + X(i).toFixed(1) + " " + Y(v).toFixed(1); }).join(" ");
    var y0 = Y(0).toFixed(1);
    var svg = '<div class="grafico-box" data-grafico="1"><svg class="grafico" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Andamento della prova sulla carta, giocata per giocata">' +
      '<line class="g-zero" x1="14" x2="' + W + '" y1="' + y0 + '" y2="' + y0 + '"></line>' +
      '<text class="g-asse" x="0" y="' + (Y(0) + 3.5).toFixed(1) + '">0</text>' +
      '<path class="g-linea' + (giu2 ? " giu" : "") + '" d="' + linea + '"></path>' +
      '<line class="g-croce" id="g-croce" x1="0" x2="0" y1="' + su + '" y2="' + (H - giu) + '" style="display:none"></line>' +
      '<circle class="g-punto' + (giu2 ? " giu" : "") + '" cx="' + X(n).toFixed(1) + '" cy="' + Y(ultimo).toFixed(1) + '" r="4.5"></circle>' +
      '<circle class="g-punto mobile" id="g-dito" cx="0" cy="0" r="4.5" style="display:none"></circle></svg>' +
      '<div class="g-etichetta" id="g-etichetta" hidden><b></b><small></small></div></div>';
    var primo = giorni[1], ultimoG = giorni[n], mezzo = giorni[Math.max(1, Math.round(n / 2))];
    // ogni giorno scritto una volta sola: all'inizio le giocate sono quasi tutte dello stesso giorno
    if (mezzo === primo || mezzo === ultimoG) mezzo = null;
    if (ultimoG === primo) ultimoG = null;
    svg += '<div class="asse"><span>' + (primo ? giornoCorto(daChiave(primo)) : "") + "</span><span>" +
      (n > 2 && mezzo ? giornoCorto(daChiave(mezzo)) : "") + "</span><span>" + (ultimoG ? giornoCorto(daChiave(ultimoG)) : "") + "</span></div>";
    return svg;
  }
  function mostraPunto(ev) {
    var box = ev.target.closest && ev.target.closest("[data-grafico]");
    var g = stato.grafico;
    if (!box || !g) return;
    var svg = box.querySelector("svg"), r = svg.getBoundingClientRect();
    if (!r.width) return;
    var x = (ev.clientX - r.left) / r.width * g.W;
    var n = g.punti.length - 1, i = 0, meglio = Infinity;
    for (var k = 1; k <= n; k++) { var dd = Math.abs(g.X(k) - x); if (dd < meglio) { meglio = dd; i = k; } }
    if (!i) return;
    var croce = document.getElementById("g-croce"), dito = document.getElementById("g-dito");
    var et = document.getElementById("g-etichetta");
    croce.setAttribute("x1", g.X(i).toFixed(1)); croce.setAttribute("x2", g.X(i).toFixed(1)); croce.style.display = "";
    dito.setAttribute("cx", g.X(i).toFixed(1)); dito.setAttribute("cy", g.Y(g.punti[i]).toFixed(1)); dito.style.display = "";
    et.querySelector("b").textContent = euro(g.punti[i], true);
    et.querySelector("small").textContent = "dopo " + i + (i === 1 ? " giocata" : " giocate") + (g.giorni[i] ? " · " + giornoCorto(daChiave(g.giorni[i])) : "");
    et.hidden = false;
    var sinistra = g.X(i) / g.W * r.width;
    et.style.left = Math.max(0, Math.min(r.width - et.offsetWidth, sinistra - et.offsetWidth / 2)) + "px";
  }
  function nascondiPunto() {
    ["g-croce", "g-dito"].forEach(function (id) { var e = document.getElementById(id); if (e) e.style.display = "none"; });
    var et = document.getElementById("g-etichetta"); if (et) et.hidden = true;
  }

  function corpoCarta() {
    var v = stato.valore;
    if (!v) {
      return stato.valoreCaricato ? '<div class="vuoto"><h3>Ancora nessun dato</h3>La prova sulla carta compare appena valore.py gira sul server.</div>'
        : '<div class="caricamento">Caricamento…</div>';
    }
    var b = v.bilancio || {};
    // dalla v19.5: prima della partita (contano per il verdetto) e intervallo
    // separati; col valore.json vecchio si mostra tutto insieme come prima
    var divisa = !!b.prima;
    var s = (divisa ? b.prima : b.singole) || { n: 0 };
    var clv = b.clv || { n: 0 };
    var p = puntata();
    var ver = VERDETTI_VALORE[b.verdetto] || VERDETTI_VALORE.presto;
    var min = b.min_verdetto || 100;
    var html = '<section class="riquadro carta"><div><span class="etichetta">' + etichettaProva(v) + '</span><div class="verdetto">' + ver[0] + "</div></div>";
    if (!b.verdetto || b.verdetto === "presto") {
      var fatto = Math.min(1, (clv.n || 0) / min);
      html += '<div class="progresso"><div class="traccia"><div style="width:' + (fatto * 100).toFixed(1) + '%"></div></div>' +
        '<div class="progresso-testi"><span><b>' + (clv.n || 0) + "</b> di " + min + " giocate</span><span>poi il verdetto</span></div></div>";
    }
    html += '<p class="testo">' + esc(ver[1].replace("{min}", min).replace("{n}", clv.n || 0)) + "</p></section>";

    // i soldi sulla carta, con il grafico
    var serie = (v.serie || []).filter(function (x) { return !divisa || !x[2]; });
    var utile = s.n ? s.utile * p : 0;
    var giornoPrima = ieri(), diIeri = serie.filter(function (x) { return x[0] === giornoPrima; });
    var utileIeri = diIeri.reduce(function (t, x) { return t + x[1] * p; }, 0);
    html += '<section class="riquadro carta"><div class="soldi-testa"><div><span class="etichetta">' +
      (divisa ? "Prima della partita, " : "Sulla carta, ") + euroTondo(p) + " a giocata</span>" +
      '<div class="soldi ' + (utile > 0.004 ? "turchese" : utile < -0.004 ? "arancio" : "") + '">' + euro(utile, true) + "</div></div>" +
      (diIeri.length ? '<span class="soldi-ieri">ieri <b class="' + (utileIeri > 0.004 ? "turchese" : utileIeri < -0.004 ? "arancio" : "") + '">' + euro(utileIeri, true) + "</b></span>" : "") +
      "</div>";
    stato.grafico = null;
    if (serie.length >= 2) html += graficoProva(serie);
    else html += '<p class="testo fioco">Il grafico parte quando si chiudono le prime giocate.</p>';
    html += '<div class="tre separato"><div><b>' + (s.n || 0) + "</b><span>chiuse" + (s.n ? " · " + s.vinte + (s.vinte === 1 ? " vinta" : " vinte") : "") + "</span></div>" +
      '<div><b class="' + (s.n ? (s.rendimento >= 0 ? "turchese" : "arancio") : "") + '">' + (s.n ? pctSegno(s.rendimento, Math.abs(s.rendimento) >= 0.995 ? 0 : 1) : "–") + "</b><span>rendimento</span></div>" +
      '<div><b class="' + (clv.n ? (clv.media >= 0 ? "turchese" : "arancio") : "") + '">' + (clv.n ? pctSegno(clv.media) : "–") +
      "</b><span>contro Pinnacle a fine mercato</span></div></div></section>";

    // per mercato
    if (s.n) {
      html += '<section class="riquadro carta"><h2 class="titoletto">Per mercato</h2><div class="tabella"><span class="ti">Mercato</span><span class="ti dx">Gioc.</span>' +
        '<span class="ti dx">Vinte</span><span class="ti dx">Rend.</span>';
      FAMIGLIE_VALORE.forEach(function (f) {
        var c = (b.famiglie || {})[f[0]];
        if (!c || !c.n || (divisa && f[0] === "all'intervallo")) return;
        html += '<span class="c nome">' + f[1] + '</span><span class="c dx">' + c.n + '</span><span class="c dx">' + c.vinte +
          '</span><span class="c dx ' + (c.rendimento >= 0 ? "turchese" : "arancio") + '">' + pctSegno(c.rendimento, 0) + "</span>";
      });
      var sc = b.schedine || { n: 0 };
      if (sc.n) {
        html += '<span class="c nome">Schedine del giorno</span><span class="c dx">' + sc.n + '</span><span class="c dx">' + sc.vinte +
          '</span><span class="c dx ' + (sc.rendimento >= 0 ? "turchese" : "arancio") + '">' + pctSegno(sc.rendimento, 0) + "</span>";
      }
      // dalla v19.6: le giocate prima della partita nei campionati seguiti solo per il valore
      var ag = b.aggiunti || { n: 0 };
      if (divisa && ag.n) {
        html += '<span class="c nome">di cui campionati aggiunti</span><span class="c dx">' + ag.n + '</span><span class="c dx">' + ag.vinte +
          '</span><span class="c dx ' + (ag.rendimento >= 0 ? "turchese" : "arancio") + '">' + pctSegno(ag.rendimento, 0) + "</span>";
      }
      html += "</div></section>";
    }

    // all'intervallo, a parte: non c'e' una quota di Pinnacle per il verdetto
    var pz = b.intervallo || { n: 0 };
    if (divisa && pz.n) {
      var up = pz.utile * p;
      html += '<section class="riquadro carta"><div class="soldi-testa"><div><span class="etichetta">All\'intervallo, a parte</span>' +
        '<div class="soldi ' + (up > 0.004 ? "turchese" : up < -0.004 ? "arancio" : "") + '">' + euro(up, true) + "</div></div></div>" +
        '<div class="tre separato"><div><b>' + pz.n + "</b><span>chiuse · " + pz.vinte + (pz.vinte === 1 ? " vinta" : " vinte") + "</span></div>" +
        "<div><b>" + String(pz.attese).replace(".", ",") + "</b><span>vinte attese</span></div>" +
        '<div><b class="' + (pz.rendimento >= 0 ? "turchese" : "arancio") + '">' + pctSegno(pz.rendimento, Math.abs(pz.rendimento) >= 0.995 ? 0 : 1) +
        "</b><span>rendimento</span></div></div>" +
        (b.ripresa ? '<p class="testo">' + esc(testoVerdettoRipresa(b.ripresa)) + "</p>" : "") +
        '<p class="testo fioco">Non contano per il verdetto: all\'intervallo non c\'è una quota di Pinnacle con cui confrontarle. ' +
        "Il loro verdetto è il confronto alla ripresa.</p></section>";
    }

    // le ultime chiuse
    var ultime = (v.ultime || []).slice(0, 10);
    if (ultime.length) {
      html += '<section class="riquadro carta"><div class="blocco-testa"><h2 class="titoletto">Ultime chiuse</h2><span>la più recente in alto</span></div>' +
        '<div class="righe-dentro">' + ultime.map(function (r) { return rigaGiocata(r, true, true); }).join("") + "</div></section>";
    }

    return html + '<p class="nota-piccola">Il prezzo giusto è la quota di Pinnacle senza il suo margine; sui mercati dei tempi che Pinnacle non quota ' +
      "è stimato dalle sue quote finali, e lì si chiede più vantaggio. Le giocate all'intervallo confrontano Bet365 live con il nostro prezzo, " +
      "che parte dalle quote di Pinnacle del mattino e dal risultato del primo tempo. \"Contro Pinnacle a fine mercato\" dice quanto le quote " +
      "prese battono l'ultima quota giusta prima della partita: è il segnale più rapido che il vantaggio è vero.</p>";
  }

  var NOMI_CATEGORIE = { singole: "Singole", alta: "Alta probabilità", valore: "Valore", sistemi: "Sistemi", miste: "Miste", esatti: "Risultati esatti" };

  function corpoModello() {
    if (!stato.dati) return senzaDati();
    var v = stato.dati.verifica;
    var s = stato.dati.schedine || { totale: { n: 0 }, categorie: {}, ieri: [] };
    var html = "";

    if (!v || !v.verificate) {
      html += '<div class="vuoto">Ancora nessuna partita verificata.</div>';
    } else {
      html += '<section class="riquadro carta"><h2 class="titoletto">Le previsioni</h2><div class="griglia2">' +
        '<div class="stat"><small>Verificate</small><b>' + v.verificate + "</b><span>partite, dall'inizio</span></div>" +
        '<div class="stat"><small>Azzeccate</small><b>' + pct(v.azzeccate / v.verificate) + "</b><span>" + v.azzeccate + " su " + v.verificate +
        " · log loss " + Number(v.log_loss).toFixed(4).replace(".", ",") + "</span></div></div></section>";

      if (v.con_quote && v.intervallo) {
        var lo = v.intervallo[0], hi = v.intervallo[1];
        var verdetto = lo > 0 ? "Battiamo il mercato" : (hi < 0 ? "Il mercato è migliore" : "Non distinguibile");
        var frase = lo > 0 ? "Su " + v.con_quote + " partite con quote siamo avanti in modo dimostrato."
          : hi < 0 ? "Su " + v.con_quote + " partite con quote il mercato è più preciso di noi, in modo dimostrato. Per questo le giocate di valore partono dalle quote di Pinnacle."
          : "Su " + v.con_quote + " partite con quote " + (v.vantaggio >= 0 ? "siamo leggermente avanti" : "siamo leggermente indietro") +
            ", ma la fascia di incertezza attraversa lo zero: ancora troppo presto per dire chi è migliore.";
        var posiz = function (x) { return Math.max(2, Math.min(98, 50 + x / 0.06 * 50)); };
        var sx = posiz(lo), dx = posiz(hi);
        html += '<section class="riquadro carta"><div><span class="etichetta">Contro i bookmaker</span><div class="verdetto">' + verdetto +
          '</div></div><div><div class="scala"><div class="t"></div><div class="f" style="left:' + sx + "%;width:" + (dx - sx) +
          '%"></div><div class="z"></div><div class="p" style="left:' + posiz(v.vantaggio) + '%"></div></div>' +
          '<div class="scala-testi"><span>meglio il mercato</span><span>pari</span><span>meglio noi</span></div></div>' +
          '<p class="testo">' + frase + "</p></section>";
      }
    }

    var t = s.totale || { n: 0 };
    if (!t.n) {
      html += '<div class="vuoto">Nessuna schedina ancora conclusa. I conteggi compariranno appena le prime saranno giocate.</div>';
    } else {
      html += '<section class="riquadro carta"><h2 class="titoletto">Le schedine proposte</h2><div class="tre"><div><b>' + t.n + "</b><span>concluse in totale</span></div>" +
        "<div><b>" + pct(t.vinte / t.n) + "</b><span>uscite</span></div>" +
        "<div><b>" + pct(t.attesa) + "</b><span>attese dal modello</span></div></div>" +
        '<div class="tabella"><span class="ti">Categoria</span><span class="ti dx">Gioc.</span><span class="ti dx">Uscite</span><span class="ti dx">Attese</span>';
      ["alta", "sistemi", "miste", "esatti", "singole", "valore"].forEach(function (k) {
        var c = (s.categorie || {})[k];
        if (!c || !c.n) return;
        html += '<span class="c nome">' + NOMI_CATEGORIE[k] + '</span><span class="c dx">' + c.n + '</span><span class="c dx forte">' +
          pct(c.vinte / c.n) + '</span><span class="c dx">' + pct(c.attesa) + "</span>";
      });
      html += '</div><p class="testo fioco">Se "uscite" e "attese" restano vicine, le probabilità delle schedine sono oneste.' +
        (t.annullate ? " " + t.annullate + (t.annullate === 1 ? " schedina annullata" : " schedine annullate") + " per partite rinviate o mai arrivate." : "") +
        "</p></section>";
    }

    var ieriL = s.ieri || [];
    var vinte = ieriL.filter(function (x) { return x.vinta; }).length;
    html += '<div class="carta lista-apri"><button class="riga-apri" data-apri="1" aria-expanded="' + stato.aperta + '">' +
      '<span class="ra-testi"><span class="ra-t1">Schedine concluse ieri</span><span class="ra-t2">' +
      (ieriL.length ? ieriL.length + (ieriL.length === 1 ? " schedina · " : " schedine · ") + vinte + (vinte === 1 ? " vinta" : " vinte") : "Nessuna schedina conclusa ieri") +
      "</span></span>" + icona(stato.aperta ? "su" : "giu") + "</button>";
    if (stato.aperta) {
      html += '<div class="ra-dentro">';
      if (!ieriL.length) html += '<div class="nota">Nessuna schedina si è chiusa ieri.</div>';
      ieriL.forEach(function (x) {
        var q = x.categoria === "sistemi" ? "" : quota(x.quota);
        html += '<div class="conclusa"><div class="riga"><div class="etichette"><span class="esito-badge ' + (x.vinta ? "vinta" : "persa") + '">' +
          (x.vinta ? "VINTA" : "PERSA") + '</span><span class="titolo-conclusa">' + esc(x.titolo) +
          '</span></div><b class="q-conclusa' + (x.vinta ? " turchese" : " fioco") + '">' + q + "</b></div>";
        (x.eventi || []).forEach(function (e) {
          html += '<div class="riga-evento"><span class="segno' + (e.preso ? "" : " no") + '">' + icona(e.preso ? "vinta" : "persa") +
            '</span><span class="nome">' + esc(e.partita) + '</span><span class="es">' + esc(e.esito) + '</span><span class="rs">' + esc(e.ris) + "</span></div>";
        });
        html += '<span class="nota">Il modello dava il ' + pct(x.prob) + (x.categoria === "sistemi" ? " di vincere almeno una combinazione" : " che uscisse tutto") + "</span></div>";
      });
      html += "</div>";
    }
    html += "</div>";

    return html + '<p class="nota-piccola">Ogni partita e ogni schedina restano salvate: contano tutte nei totali qui sopra, anche se non vengono elencate.<br><br>App versione ' + VERSIONE_APP + "</p>";
  }

  function vistaRisultati() {
    var html = testa("Risultati", "Come stanno andando le giocate") + avvisoFuoriLinea() + '<div class="corpo">' +
      '<div class="segmenti"><button class="segmento' + (stato.risultati === "carta" ? " attivo" : "") + '" data-risultati="carta" aria-pressed="' + (stato.risultati === "carta") + '">Sulla carta</button>' +
      '<button class="segmento' + (stato.risultati === "modello" ? " attivo" : "") + '" data-risultati="modello" aria-pressed="' + (stato.risultati === "modello") + '">Il modello</button></div>';
    html += stato.risultati === "modello" ? corpoModello() : corpoCarta();
    return html + "</div>";
  }

  // ---------------------------------------------------------------
  //  disegno e navigazione
  // ---------------------------------------------------------------
  function sostituisci(hash) {
    if (history.replaceState) history.replaceState(null, "", hash);
    return hash;
  }

  function disegna() {
    var h = location.hash || "#/";
    // indirizzi delle versioni prima della 18, anche dentro le notifiche
    if (h === "#/valore" || h === "#/mie" || h === "#/nuova" || h.indexOf("#/nuova/") === 0) h = sostituisci("#/");
    else if (h === "#/esatti") { stato.scheda = "esatti"; h = sostituisci("#/giocate"); }
    else if (h === "#/verifica") { stato.risultati = "modello"; h = sostituisci("#/risultati"); }

    var vista = "oggi", html;
    if (h.indexOf("#/partita/") === 0) {
      vista = "partite"; html = stato.dati ? vistaDettaglio(decodeURIComponent(h.slice(10))) : senzaDati();
    } else if (h === "#/live") {
      vista = "live"; html = vistaLive();
    } else if (h === "#/partite") {
      vista = "partite"; html = stato.dati ? vistaPartite() : senzaDati();
    } else if (h === "#/giocate") {
      vista = "giocate"; html = stato.dati ? vistaGiocate() : senzaDati();
    } else if (h === "#/risultati") {
      vista = "risultati"; html = vistaRisultati();
    } else {
      html = !stato.dati && !stato.valore && stato.valoreCaricato && stato.datiCaricati ? senzaDati() : vistaOggi();
    }
    schermo.innerHTML = html;
    document.querySelectorAll("#barra a").forEach(function (a) {
      var attiva = a.getAttribute("data-vista") === vista;
      a.classList.toggle("attiva", attiva);
      if (attiva) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    // pallino su Live quando c'e' una giocata all'intervallo da fare
    var live = document.querySelector('#barra a[data-vista="live"]');
    if (live) live.classList.toggle("con-avviso", pausaDaGiocare().length > 0);
  }
  function ridisegnaFermo() {
    var y = window.scrollY; disegna(); window.scrollTo(0, y);
  }

  schermo.addEventListener("click", function (ev) {
    var el = ev.target.closest("button");
    if (!el) return;
    if (el.hasAttribute("data-giorno")) { stato.giorno = el.getAttribute("data-giorno"); disegna(); }
    else if (el.hasAttribute("data-lega")) { stato.lega = el.getAttribute("data-lega"); disegna(); }
    else if (el.hasAttribute("data-scheda")) { stato.scheda = el.getAttribute("data-scheda"); disegna(); }
    else if (el.hasAttribute("data-risultati")) { stato.risultati = el.getAttribute("data-risultati"); disegna(); }
    else if (el.hasAttribute("data-apri")) { stato.aperta = !stato.aperta; ridisegnaFermo(); }
    else if (el.hasAttribute("data-apri-oggi")) {
      var k = el.getAttribute("data-apri-oggi");
      stato.oggiAperto[k] = !stato.oggiAperto[k];
      ridisegnaFermo();
    }
    else if (el.hasAttribute("data-mercato")) {
      var g = el.getAttribute("data-mercato");
      stato.mercati[g] = !stato.mercati[g];
      ridisegnaFermo();          // si riapre dove si era rimasti
    }
    else if (el.hasAttribute("data-azzera")) { stato.giorno = "tutti"; stato.lega = "tutte"; disegna(); }
    else if (el.hasAttribute("data-ricarica")) { carica(); }
    else if (el.hasAttribute("data-campana")) { gestisciCampana(); }
  });

  // il dito sul grafico della prova mostra il saldo giocata per giocata
  schermo.addEventListener("pointerdown", mostraPunto);
  schermo.addEventListener("pointermove", mostraPunto);
  schermo.addEventListener("pointerleave", function (ev) {
    if (ev.target.hasAttribute && ev.target.hasAttribute("data-grafico")) nascondiPunto();
  }, true);

  window.addEventListener("hashchange", function () { disegna(); window.scrollTo(0, 0); });

  // ---------------------------------------------------------------
  //  dati
  // ---------------------------------------------------------------
  // valore.json si scarica a parte: se manca o non arriva, il resto
  // dell'app funziona lo stesso
  function caricaValore() {
    return fetch("valore.json?t=" + Date.now(), { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) { stato.valore = d; })
      .catch(function () {
        if (typeof caches === "undefined") return;
        return caches.match("valore.json").then(function (r) {
          if (r) return r.json().then(function (d) { stato.valore = d; });
        });
      })
      .catch(function () {})
      .then(function () {
        stato.valoreCaricato = true;
        ridisegnaFermo();
      });
  }

  function carica() {
    caricaValore();
    return fetch("app.json?t=" + Date.now(), { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) { stato.dati = d; stato.fuoriLinea = false; stato.datiCaricati = true; ridisegnaFermo(); })
      .catch(function () {
        // senza rete: il componente offline restituisce l'ultima copia salvata
        var fine = function () { stato.datiCaricati = true; ridisegnaFermo(); };
        return typeof caches !== "undefined" ? caches.match("app.json").then(function (r) {
          if (r) return r.json().then(function (d) { stato.dati = d; stato.fuoriLinea = true; fine(); });
          fine();
        }).catch(fine) : fine();
      });
  }

  // aggiornamento: quando si riapre l'app e ogni cinque minuti
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") carica();
  });
  setInterval(function () { if (document.visibilityState === "visible") carica(); }, 5 * 60 * 1000);
  // in Oggi e in Live il tempo che resta per le giocate all'intervallo scorre da solo:
  // si ridisegna solo mentre ce n'e' una aperta, o appena scaduta
  setInterval(function () {
    var h = location.hash || "#/";
    if (document.visibilityState !== "visible" || (h !== "#/" && h !== "#" && h !== "#/live") || !stato.valore) return;
    var adesso = Date.now();
    var aperte = (stato.valore.oggi || []).some(function (r) {
      return r.intervallo && r.registrata && !r.esito && adesso - data(r.registrata).getTime() < 16 * 60000;
    });
    if (aperte) ridisegnaFermo();
  }, 30 * 1000);

  // ---------------------------------------------------------------
  //  notifiche
  // ---------------------------------------------------------------
  function avviso(testo) {
    var vecchio = document.getElementById("avviso-app");
    if (vecchio) vecchio.remove();
    var d = document.createElement("div");
    d.id = "avviso-app";
    d.setAttribute("role", "status");
    d.textContent = testo;
    document.body.appendChild(d);
    setTimeout(function () { if (d.parentNode) d.remove(); }, 5000);
  }

  function suIPhone() {
    return /iphone|ipad|ipod/i.test(navigator.userAgent) ||
           (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  }
  function installata() {
    return window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  }

  function controllaNotifiche() {
    if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
      stato.notifiche = suIPhone() && !installata() ? "installa" : "non-supportate";
      return ridisegnaFermo();
    }
    if (Notification.permission === "denied") { stato.notifiche = "negate"; return ridisegnaFermo(); }
    navigator.serviceWorker.ready
      .then(function (reg) { return reg.pushManager.getSubscription(); })
      .then(function (s) { stato.notifiche = s ? "attive" : "spente"; ridisegnaFermo(); })
      .catch(function () { stato.notifiche = "spente"; ridisegnaFermo(); });
  }

  function chiaveInByte(testo) {
    var b = atob((testo + "===".slice((testo.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
    var out = new Uint8Array(b.length);
    for (var i = 0; i < b.length; i++) out[i] = b.charCodeAt(i);
    return out;
  }

  function attivaNotifiche() {
    // il permesso va chiesto subito, dentro il tocco: iPhone lo esige
    Notification.requestPermission().then(function (permesso) {
      if (permesso !== "granted") {
        stato.notifiche = permesso === "denied" ? "negate" : "spente";
        ridisegnaFermo();
        if (permesso === "denied") avviso("Hai negato il permesso. Si riattiva dalle impostazioni del telefono, alla voce di questa app.");
        return;
      }
      return Promise.all([
        navigator.serviceWorker.ready,
        fetch("/api/notifiche/chiave", { cache: "no-store" }).then(function (r) {
          if (!r.ok) throw new Error("chiave");
          return r.json();
        })
      ]).then(function (v) {
        return v[0].pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: chiaveInByte(v[1].chiave) });
      }).then(function (iscrizione) {
        return fetch("/api/notifiche/iscrivi", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(iscrizione)
        }).then(function (r) { if (!r.ok) throw new Error("iscrizione"); });
      }).then(function () {
        stato.notifiche = "attive";
        ridisegnaFermo();
        avviso("Notifiche attivate: le giocate di valore del mattino, quelle all'intervallo e le partite che cambiano molto con le formazioni ufficiali.");
      });
    }).catch(function () {
      avviso("Non sono riuscito ad attivare le notifiche. Controlla la connessione e riprova.");
    });
  }

  function disattivaNotifiche() {
    navigator.serviceWorker.ready
      .then(function (reg) { return reg.pushManager.getSubscription(); })
      .then(function (s) {
        if (!s) return;
        var endpoint = s.endpoint;
        return s.unsubscribe().then(function () {
          return fetch("/api/notifiche/disiscrivi", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ endpoint: endpoint })
          }).catch(function () {});
        });
      })
      .then(function () { stato.notifiche = "spente"; ridisegnaFermo(); avviso("Notifiche disattivate."); })
      .catch(function () { avviso("Non sono riuscito a disattivare le notifiche. Riprova."); });
  }

  function gestisciCampana() {
    if (stato.notifiche === "attive") {
      if (window.confirm("Disattivare le notifiche?")) disattivaNotifiche();
    } else if (stato.notifiche === "spente" || stato.notifiche === "sconosciuto") {
      attivaNotifiche();
    } else if (stato.notifiche === "negate") {
      avviso("Le notifiche sono bloccate. Si riattivano dalle impostazioni del telefono, alla voce di questa app.");
    } else if (stato.notifiche === "installa") {
      avviso("Su iPhone le notifiche funzionano solo dall'app installata: aggiungila alla schermata Home e aprila da lì.");
    } else {
      avviso("Questo browser non supporta le notifiche.");
    }
  }

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(function () {});
  }
  carica();
  controllaNotifiche();
})();
