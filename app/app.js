/* Previsioni - logica dell'app.
 * Legge app.json, prodotto ogni mezz'ora dal sistema sul server, e
 * costruisce le cinque schermate: palinsesto, dettaglio, giocate,
 * risultati esatti, verifica.
 */
(function () {
  "use strict";

  var VERSIONE_APP = "13";

  var stato = {
    dati: null,
    fuoriLinea: false,
    giorno: "tutti",
    lega: "tutte",
    scheda: "alta",
    aperta: false,
    mercati: {},               // quali gruppi di mercati sono aperti
    elenco: "corso",           // mie giocate: in corso o concluse
    giocataAperta: null,
    impostazioni: false,
    copia: false,
    nuova: { fid: null, esito: null, quota: "", puntata: "", toccata: false, cerca: "" },
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
  function quota(v, dec) {
    if (v == null || isNaN(v)) return "–";
    return Number(v).toFixed(dec == null ? 2 : dec);
  }
  function segno(v) {
    var n = Math.round(v * 100);
    return (n > 0 ? "+" : n < 0 ? "−" : "") + Math.abs(n);
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
  function relativo(chiave) {
    var oggi = new Date();
    var domani = new Date(); domani.setDate(oggi.getDate() + 1);
    if (chiave === chiaveGiorno(oggi)) return "Oggi";
    if (chiave === chiaveGiorno(domani)) return "Domani";
    return null;
  }
  function titoloGiorno(chiave) {
    var p = chiave.split("-");
    var d = new Date(+p[0], +p[1] - 1, +p[2]);
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
    "wales": "#gbwls", "scotland": "#gbsct", "england": "#gbeng"
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
  function trovaPartita(id) {
    var lista = (stato.dati && stato.dati.partite) || [];
    for (var i = 0; i < lista.length; i++) if (String(lista[i].id) === String(id)) return lista[i];
    return null;
  }

  // ---------------------------------------------------------------
  //  pezzi grafici ricorrenti
  // ---------------------------------------------------------------
  function testa(titolo, sotto, destra) {
    return '<header class="testa"><div class="testa-riga"><div class="testa-testi">' +
           '<div class="marchio">Previsioni</div>' +
           '<h1 class="titolo">' + esc(titolo) + '</h1>' +
           (sotto ? '<div class="sottotitolo">' + sotto + '</div>' : "") + "</div>" +
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
  var ICONA_AVVISO = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l9.5 17h-19z"></path><path d="M12 10v4M12 17.5v.5"></path></svg>';

  // ---------------------------------------------------------------
  //  PALINSESTO
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
    function etichettaLega(c) {
      var n = nomeLega(c);
      var b = bandiera(n.paese);
      if (b) return b + " " + n.nome;
      return Object.keys(perNome[n.nome]).length > 1 ? n.nome + " · " + n.paese : n.nome;
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

    var agg = stato.dati.generato ? oraDi(data(stato.dati.generato)) : "";
    var n = filtrate.length;
    var html = testa("Palinsesto", "Aggiornato alle " + agg + " · " + n + (n === 1 ? " partita" : " partite"), campana());

    html += '<div class="filtri"><div class="riga-chip nascosto-scroll">';
    html += '<button class="chip-giorno' + (stato.giorno === "tutti" ? " attivo" : "") +
            '" data-giorno="tutti"><span class="su">Tutti</span><span class="giu">' + giorni.length + ' gg</span></button>';
    giorni.forEach(function (k) {
      var p = k.split("-");
      var d = new Date(+p[0], +p[1] - 1, +p[2]);
      var su = relativo(k) === "Oggi" ? "Oggi" : (relativo(k) === "Domani" ? "Domani" : GIORNI[d.getDay()]);
      html += '<button class="chip-giorno' + (stato.giorno === k ? " attivo" : "") + '" data-giorno="' + k +
              '"><span class="su">' + su + '</span><span class="giu">' + d.getDate() + "</span></button>";
    });
    html += '</div><div class="riga-chip nascosto-scroll">';
    html += '<button class="chip' + (stato.lega === "tutte" ? " attivo" : "") + '" data-lega="tutte">Tutti</button>';
    leghe.forEach(function (c) {
      html += '<button class="chip' + (stato.lega === c ? " attivo" : "") + '" data-lega="' + esc(c) + '">' +
              esc(etichettaLega(c)) + "</button>";
    });
    html += "</div></div>" + avvisoFuoriLinea() + '<div class="corpo">';

    if (!filtrate.length) {
      html += '<div class="vuoto"><h3>Nessuna partita</h3>Con questi filtri non ci sono partite in programma.' +
              '<br><button data-azzera="1">Mostra tutto</button></div>';
    }
    gruppi.forEach(function (g) {
      html += '<section class="gruppo"><div class="gruppo-testa"><h2>' + esc(titoloGiorno(g.k)) + "</h2><span>" +
              g.partite.length + (g.partite.length === 1 ? " partita" : " partite") + "</span></div>";
      g.partite.forEach(function (m) {
        var d = data(m.data);
        var max = Math.max(m.p["1"], m.p["X"], m.p["2"]);
        html += '<a class="partita carta" href="#/partita/' + encodeURIComponent(m.id) + '">' +
          '<div class="riga"><div class="ora"><b>' + oraDi(d) + "</b><span>" + dataBreve(d) + "</span></div>" +
          '<div class="etichette"><span class="lega">' + bandiera(nomeLega(m.campionato).paese) + " " + esc(nomeLega(m.campionato).nome) + "</span>" + badge(m.formazioni) + "</div></div>" +
          '<div class="riga"><div class="squadre"><span>' + esc(m.casa) + "</span><span>" + esc(m.fuori) + "</span></div>" +
          '<div class="pillole">' + ["1", "X", "2"].map(function (k) {
            return '<div class="pillola' + (m.p[k] === max ? " forte" : "") + '"><small>' + k + "</small><b>" + pct(m.p[k]) + "</b></div>";
          }).join("") + "</div></div>" + barra3(m.p) + "</a>";
      });
      html += "</section>";
    });
    return html + "</div>";
  }

  // ---------------------------------------------------------------
  //  DETTAGLIO PARTITA
  // ---------------------------------------------------------------
  function soglia(etichettaA, a, etichettaB, b) {
    var vinceA = a >= b;
    return '<div class="soglia"><div class="testi">' +
      '<span class="' + (vinceA ? "vince" : "") + '">' + etichettaA + " <b>" + pct(a) + "</b></span>" +
      '<span class="' + (!vinceA ? "vince" : "") + '">' + etichettaB + " <b>" + pct(b) + "</b></span></div>" +
      '<div class="barra2"><div class="' + (Math.abs(a - b) < 0.02 ? "pari" : (vinceA ? "forte" : "")) + '" style="width:' + (a * 100).toFixed(1) + '%"></div>' +
      '<div class="' + (Math.abs(a - b) < 0.02 ? "pari" : (!vinceA ? "forte" : "")) + '" style="width:' + (b * 100).toFixed(1) + '%"></div></div></div>';
  }

  // Il contenuto di un gruppo di mercati, disegnato come il resto
  // della scheda: le barre a due teste della sezione Gol per gli esiti
  // che vanno a coppie, i riquadri dei risultati esatti per gli altri.
  function corpoMercati(g) {
    if (g.f === "coppie") {
      var barre = "";
      for (var i = 0; i + 1 < g.v.length; i += 2) {
        barre += soglia(g.v[i].n, g.v[i].p, g.v[i + 1].n, g.v[i + 1].p);
      }
      return '<div class="coppie">' + barre + "</div>";
    }
    var massimo = Math.max.apply(null, g.v.map(function (x) { return x.p; }));
    return '<div class="griglia3">' + g.v.map(function (x) {
      return '<div class="ris-tessera mercato' + (x.p === massimo ? " primo" : "") +
        '" title="' + esc(x.lungo) + '"><b>' + esc(x.n) + "</b><small>" +
        pct(x.p) + "</small><span class=\"eq\">" + quota(1 / x.p) + "</span></div>";
    }).join("") + "</div>";
  }

  function vistaDettaglio(id) {
    var m = trovaPartita(id);
    var indietro = '<a class="indietro" href="#/"><svg viewBox="0 0 24 24"><path d="M15 5l-7 7 7 7"></path></svg>Palinsesto</a>';
    if (!m) return indietro + '<div class="vuoto"><h3>Partita non trovata</h3>Potrebbe essere già stata giocata.</div>';

    var d = data(m.data);
    var lega = nomeLega(m.campionato);
    var max = Math.max(m.p["1"], m.p["X"], m.p["2"]);
    var etichettaForm = m.formazioni === "ufficiale" ? "Formazioni ufficiali" :
                        m.formazioni === "probabile" ? "Formazioni probabili" : "Formazioni non note";
    var html = indietro + '<div class="eroe"><div class="riga"><span class="lega">' + bandiera(lega.paese) + " " + esc(lega.nome) +
      (lega.paese ? " · " + esc(lega.paese) : "") + '</span><span class="badge' + (m.formazioni === "ufficiale" ? " uff" : "") + '">' +
      etichettaForm.toUpperCase() + '</span></div><div class="nomi"><div class="nome">' + esc(m.casa) +
      '</div><div class="contro">contro</div><div class="nome">' + esc(m.fuori) + '</div></div>' +
      '<div class="quando">' + GIORNI_LUNGHI[d.getDay()] + " " + d.getDate() + " " + MESI[d.getMonth()] + " · <b>" + oraDi(d) + "</b></div></div>";

    // esito finale e doppia chance
    html += '<section class="blocco carta"><h2>Esito finale</h2><div class="griglia3">' +
      ["1", "X", "2"].map(function (k) {
        return '<div class="tessera' + (m.p[k] === max ? " forte" : "") + '"><small>' + k + "</small><b>" + pct(m.p[k]) + "</b></div>";
      }).join("") + "</div>" + barra3(m.p) + '<div class="doppie">' +
      ["1X", "12", "X2"].map(function (k) { return "<div><small>" + k + "</small><b>" + pct(m.dc[k]) + "</b></div>"; }).join("") +
      "</div></section>";

    // gol
    html += '<section class="blocco carta"><h2>Gol</h2>' +
      soglia("Over 1.5", m.gol.over15, "Under 1.5", m.gol.under15) +
      soglia("Over 2.5", m.gol.over25, "Under 2.5", m.gol.under25) +
      soglia("Over 3.5", m.gol.over35, "Under 3.5", m.gol.under35) +
      '<div style="padding-top:12px;border-top:1px solid var(--linea)">' + soglia("Gol", m.gg.gol, "NoGol", m.gg.nogol) + "</div></section>";

    // risultati esatti
    if (m.esatti && m.esatti.length) {
      html += '<section class="blocco carta"><h2>Risultati più probabili</h2><div class="griglia3">' +
        m.esatti.map(function (e, i) {
          return '<div class="ris-tessera' + (i === 0 ? " primo" : "") + '"><b>' + esc(e.r) + "</b><small>" + pct(e.p) + "</small></div>";
        }).join("") + "</div></section>";
    }

    // tutti gli altri mercati giocabili, un gruppo per volta.
    // I nomi stanno nel vocabolario in cima ad app.json; qui dentro la
    // partita ci sono solo i numeri, nello stesso ordine.
    var vocab = (stato.dati && stato.dati.mercati) || [];
    if (m.altri && m.altri.length && vocab.length) {
      var righe = vocab.map(function (g, i) {
        var valori = m.altri[i] || [];
        return {
          chiave: g.t,
          t: g.t.replace("{casa}", m.casa).replace("{fuori}", m.fuori),
          f: g.f,
          v: g.n.map(function (nome, j) {
            return { n: nome, lungo: (g.lunghi || [])[j] || nome,
                     p: (valori[j] || 0) / 1000 };
          })
        };
      }).map(function (g) {
        // nelle tessere gli esiti sotto lo 0,5% si tolgono; nelle coppie
        // no, altrimenti si scompagnano e la barra non torna
        if (g.f !== "coppie") g.v = g.v.filter(function (x) { return x.p > 0; });
        return g;
      }).filter(function (g) { return g.v.length; });

      var totale = righe.reduce(function (n, g) { return n + g.v.length; }, 0);
      html += '<section class="blocco elenco"><h2>Altri mercati · ' + totale + '</h2>';
      righe.forEach(function (g) {
        var apertoG = !!stato.mercati[g.chiave];
        html += '<div class="carta apribile mercati' + (apertoG ? " aperta" : "") +
          '"><button data-mercato="' + esc(g.chiave) + '" aria-expanded="' + apertoG + '">' +
          '<span><span class="t1">' + esc(g.t) + '</span><span class="t2">' +
          g.v.length + ' esiti</span></span><span class="bottone">' +
          (apertoG ? "Chiudi" : "Apri") + "</span></button>";
        if (apertoG) html += '<div class="dentro">' + corpoMercati(g) + "</div>";
        html += "</div>";
      });
      html += '<div class="nota">Il numero piccolo sotto la percentuale è la quota equa: ' +
        'è solo il rovescio della percentuale, non un consiglio di gioco.</div></section>';
    }

    // prima e dopo le formazioni ufficiali
    if (m.prima && m.formazioni === "ufficiale") {
      html += '<section class="blocco carta"><h2>Prima e dopo le formazioni</h2><div class="confronto">' +
        '<span class="ti"></span><span class="ti dx">STIMATE</span><span class="ti dx">UFFICIALI</span><span class="ti dx">SCARTO</span>' +
        ["1", "X", "2"].map(function (k) {
          var s = m.p[k] - m.prima[k];
          return '<span class="v">' + k + '</span><span class="v dx fioco">' + pct(m.prima[k]) + '</span><span class="v dx">' + pct(m.p[k]) +
                 '</span><span class="v dx ' + (s > 0 ? "piu" : s < 0 ? "meno" : "fioco") + '">' + segno(s) + "</span>";
        }).join("") + "</div></section>";
    }

    // confronto con il mercato
    if (m.mercato) {
      var scarti = ["1", "X", "2"].map(function (k) { return m.p[k] - m.mercato[k]; });
      var massimo = Math.max.apply(null, scarti.map(Math.abs));
      html += '<section class="blocco carta"><h2>Confronto con i bookmaker</h2><div class="confronto">' +
        '<span class="ti"></span><span class="ti dx">NOI</span><span class="ti dx">MERCATO</span><span class="ti dx">SCARTO</span>' +
        ["1", "X", "2"].map(function (k, i) {
          return '<span class="v">' + k + '</span><span class="v dx">' + pct(m.p[k]) + '</span><span class="v dx fioco">' + pct(m.mercato[k]) +
                 '</span><span class="v dx ' + (scarti[i] > 0 ? "piu" : scarti[i] < 0 ? "meno" : "fioco") + '">' + segno(scarti[i]) + "</span>";
        }).join("") + "</div>" +
        '<div class="nota" style="padding:0">Media di ' + esc(m.mercato.bookmaker || "–") + " bookmaker, margine del " +
        pct(m.mercato.margine, 1) + " già tolto</div>" +
        (massimo >= 0.15 ? '<div class="avviso">' + ICONA_AVVISO + "<span>Divergenza forte. Nei nostri test, quando ci allontaniamo così dal mercato di solito sbagliamo noi.</span></div>" : "") +
        "</section>";
    }

    // quanto fidarsi
    var etichetta = m.affidabilita_etichetta || "bassa";
    html += '<section class="blocco carta"><h2>Quanto fidarsi</h2>' +
      '<div class="indice"><div class="testi"><span>Affidabilità <span class="tag ' + esc(etichetta) + '">' + esc(etichetta.toUpperCase()) +
      '</span></span><span class="num">' + Math.round(m.affidabilita || 0) + "<small>/100</small></span></div>" +
      '<div class="traccia"><div style="width:' + (m.affidabilita || 0) + '%"></div></div>' +
      '<span class="spiega">Storico di ' + esc(m.storia[0]) + " e " + esc(m.storia[1]) + " partite · " + etichettaForm.toLowerCase() + "</span></div>" +
      '<div class="indice"><div class="testi"><span>Nettezza</span><span class="num">' + Math.round(m.nettezza || 0) + "<small>/100</small></span></div>" +
      '<div class="traccia"><div class="spento" style="width:' + (m.nettezza || 0) + '%"></div></div>' +
      '<span class="spiega">Quanto il pronostico è sbilanciato</span></div>' +
      '<div class="attesi"><span>Gol attesi</span><b>' + quota(m.attesi[0]) + ' <span class="fioco">–</span> ' + quota(m.attesi[1]) + "</b></div></section>";

    if (d.getTime() > Date.now()) {
      html += '<div style="padding:0 16px 4px"><a class="bottone-grande" href="#/nuova/' + encodeURIComponent(m.id) + '">+ Registra una giocata</a></div>';
    }
    return html + '<p class="nota-piccola" style="text-align:center;padding:12px 24px 24px">Sono probabilità, non certezze: un esito al 60% non si verifica quattro volte su dieci.</p>';
  }

  // ---------------------------------------------------------------
  //  GIOCATE
  // ---------------------------------------------------------------
  // Singole consigliate e Valore sono state tolte: si basavano sul
  // "vantaggio stimato" sul mercato, e la verifica ha dimostrato che
  // quelle giocate perdevano (-14,6% per puntata su 466 casi).
  var SCHEDE = [
    { id: "alta", nome: "Alta probabilità", testo: "Esiti molto probabili, uno per partita, combinati fino a superare quota 1.45." },
    { id: "sistemi", nome: "Sistemi", testo: "Più esiti sulla stessa partita: basta che in ogni partita se ne avveri almeno uno." },
    { id: "miste", nome: "Miste", testo: "Schedine attorno a quota 5, 10 e 17, solo con esiti sopra il 30%." }
  ];

  function vistaGiocate() {
    var g = stato.dati.giocate || {};
    var fatte = g.generato ? "Proposte di oggi, fatte alle " + oraDi(data(g.generato)) : "";
    var html = testa("Giocate", fatte) + '<div class="filtri"><div class="riga-chip nascosto-scroll">' +
      SCHEDE.map(function (s) {
        return '<button class="chip' + (stato.scheda === s.id ? " attivo" : "") + '" data-scheda="' + s.id + '">' + s.nome + "</button>";
      }).join("") + "</div></div>" + avvisoFuoriLinea() + '<div class="corpo">';
    var scheda = SCHEDE.filter(function (s) { return s.id === stato.scheda; })[0];
    if (!scheda) { stato.scheda = "alta"; scheda = SCHEDE[0]; }
    html += '<div class="nota">' + scheda.testo + "</div>";

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
      c.eventi.forEach(function (e) {
        html += '<div class="evento"><div class="sx"><b>' + esc(e.partita) + "</b><small>" + esc(e.info) +
          '</small></div><div class="dx2"><span class="esito">' + esc(e.esito) + "</span><small>" +
          (e.almeno_uno ? "almeno uno " : "") + pct(e.p) + "</small></div></div>";
      });
      var piede, valore;
      if (stato.scheda === "singole") {
        piede = "noi " + pct(c.prob) + " · mercato " + pct(c.mercato) + " · vantaggio stimato";
        valore = (c.vantaggio >= 0 ? "+" : "−") + Math.abs(Math.round((c.vantaggio || 0) * 100)) + "%";
      } else if (sistema) {
        piede = "probabilità di vincere almeno una combinazione"; valore = pct(c.prob);
      } else {
        piede = "probabilità che esca tutto"; valore = pct(c.prob);
      }
      html += '<div class="piede' + (c.prob < 0.35 ? " rischio" : "") + '"><span>' + piede + "</span><b>" + valore + "</b></div></article>";
    });

    return html + '<p class="nota-piccola">Le proposte si fanno una volta al mattino e non cambiano durante la giornata, anche se le previsioni si aggiornano. Nessuna di queste proposte ha un guadagno dimostrato: la verifica mostra che il mercato è più preciso del modello. Nelle multiple il margine del bookmaker si moltiplica: circa 7% su una singola, 14% su una doppia, 22% su una tripla.</p></div>';
  }

  // ---------------------------------------------------------------
  //  RISULTATI ESATTI
  // ---------------------------------------------------------------
  function vistaEsatti() {
    var lista = stato.dati.esatti || [];
    var html = testa("Risultati esatti", "I cinque più probabili di tutto il palinsesto") + avvisoFuoriLinea() +
      '<div class="corpo"><div class="avviso">' + ICONA_AVVISO +
      "<span>La parte meno verificata del modello. Anche il punteggio più probabile esce raramente più di una volta su sette.</span></div>";
    if (!lista.length) html += '<div class="vuoto"><h3>Nessuna partita</h3>Non ci sono partite in programma.</div>';
    lista.forEach(function (r, i) {
      var netto = r.p >= 0.15;
      html += '<div class="esatto carta' + (netto ? " netto" : "") + '"><div class="pos' + (i === 0 ? " primo" : "") + '">' + (i + 1) +
        '</div><div class="punteggio">' + esc(r.ris) + '</div><div class="info"><b>' + esc(r.partita) + "</b><small>" + esc(r.info) +
        "</small><span>stacca il secondo di " + (r.distacco * 100).toFixed(1).replace(".", ",") + ' punti</span></div><div class="perc">' +
        pct(r.p, 1) + "</div></div>";
    });
    return html + '<p class="nota-piccola">Ordinati per probabilità: tendono a uscire le partite chiuse, dove pochi punteggi concentrano le possibilità.</p></div>';
  }

  // ---------------------------------------------------------------
  //  VERIFICA
  // ---------------------------------------------------------------
  var NOMI_CATEGORIE = { singole: "Singole", alta: "Alta probabilità", valore: "Valore", sistemi: "Sistemi", miste: "Miste" };

  function vistaVerifica() {
    var v = stato.dati.verifica;
    var s = stato.dati.schedine || { totale: { n: 0 }, categorie: {}, ieri: [] };
    var html = testa("Verifica", "Come sono andate le previsioni già giocate") + avvisoFuoriLinea() +
      '<div class="corpo"><h2 class="sezione">Partite</h2>';

    if (!v || !v.verificate) {
      html += '<div class="vuoto">Ancora nessuna partita verificata.</div>';
    } else {
      html += '<div class="griglia2"><div class="stat carta"><small>Verificate</small><b>' + v.verificate +
        "</b><span>in totale, dall'inizio</span></div>" +
        '<div class="stat carta"><small>Azzeccate</small><b class="verde">' + pct(v.azzeccate / v.verificate) +
        "</b><span>" + v.azzeccate + " su " + v.verificate + " · log loss " + Number(v.log_loss).toFixed(4).replace(".", ",") + "</span></div></div>";

      if (v.con_quote && v.intervallo) {
        var lo = v.intervallo[0], hi = v.intervallo[1];
        var verdetto = lo > 0 ? "Battiamo il mercato" : (hi < 0 ? "Il mercato è migliore" : "Non distinguibile");
        var frase = lo > 0 ? "Su " + v.con_quote + " partite con quote siamo avanti in modo dimostrato."
          : hi < 0 ? "Su " + v.con_quote + " partite con quote il mercato è più preciso di noi, in modo dimostrato."
          : "Su " + v.con_quote + " partite con quote " + (v.vantaggio >= 0 ? "siamo leggermente avanti" : "siamo leggermente indietro") +
            ", ma la fascia di incertezza attraversa lo zero: ancora troppo presto per dire chi è migliore.";
        function posiz(x) { return Math.max(2, Math.min(98, 50 + x / 0.06 * 50)); }
        var sx = posiz(lo), dx = posiz(hi);
        html += '<div class="riquadro carta"><div><span class="etichetta">Contro i bookmaker</span><div class="verdetto" style="margin-top:4px">' +
          verdetto + '</div></div><div><div class="scala"><div class="t"></div><div class="f" style="left:' + sx + "%;width:" + (dx - sx) +
          '%"></div><div class="z"></div><div class="p" style="left:' + posiz(v.vantaggio) + '%"></div></div>' +
          '<div class="scala-testi"><span>meglio il mercato</span><span>pari</span><span>meglio noi</span></div></div>' +
          '<span class="nota" style="padding:0">' + frase + "</span></div>";
      }
    }

    html += '<h2 class="sezione" style="padding-top:10px">Schedine</h2>';
    var t = s.totale || { n: 0 };
    if (!t.n) {
      html += '<div class="vuoto">Nessuna schedina ancora conclusa. I conteggi compariranno appena le prime saranno giocate.</div>';
    } else {
      html += '<div class="riquadro carta"><div class="tre"><div><b>' + t.n + "</b><span>concluse in totale</span></div>" +
        '<div><b class="verde">' + pct(t.vinte / t.n) + "</b><span>uscite</span></div>" +
        "<div><b>" + pct(t.attesa) + "</b><span>attese dal modello</span></div></div>" +
        '<div class="tabella"><span class="ti">Categoria</span><span class="ti dx">Gioc.</span><span class="ti dx">Uscite</span><span class="ti dx">Attese</span>';
      ["singole", "alta", "valore", "sistemi", "miste"].forEach(function (k) {
        var c = (s.categorie || {})[k];
        if (!c || !c.n) return;
        html += '<span class="c nome">' + NOMI_CATEGORIE[k] + '</span><span class="c dx">' + c.n + '</span><span class="c dx verde">' +
          pct(c.vinte / c.n) + '</span><span class="c dx">' + pct(c.attesa) + "</span>";
      });
      html += '</div><span class="nota" style="padding:0">Se "uscite" e "attese" restano vicine, le probabilità delle schedine sono oneste.'
        + (t.annullate ? " " + t.annullate + (t.annullate === 1 ? " schedina annullata" : " schedine annullate")
           + " per partite rinviate o mai arrivate." : "")
        + "</span></div>";
    }

    var ieri = s.ieri || [];
    var vinte = ieri.filter(function (x) { return x.vinta; }).length;
    html += '<div class="carta apribile' + (stato.aperta ? " aperta" : "") + '"><button data-apri="1" aria-expanded="' + stato.aperta + '">' +
      '<span><span class="t1">Concluse ieri</span><span class="t2">' +
      (ieri.length ? ieri.length + (ieri.length === 1 ? " schedina · " : " schedine · ") + vinte + (vinte === 1 ? " vinta" : " vinte") : "Nessuna schedina conclusa ieri") +
      '</span></span><span class="bottone">' + (stato.aperta ? "Chiudi" : "Apri") + "</span></button>";
    if (stato.aperta) {
      html += '<div class="dentro">';
      if (!ieri.length) html += '<div class="nota">Nessuna schedina si è chiusa ieri.</div>';
      ieri.forEach(function (x) {
        var q = x.categoria === "sistemi" ? "" : quota(x.quota);
        html += '<div class="conclusa"><div class="riga"><div class="etichette"><span class="esito-badge ' + (x.vinta ? "vinta" : "persa") + '">' +
          (x.vinta ? "VINTA" : "PERSA") + '</span><span class="lega" style="text-transform:none;letter-spacing:0;font-size:13px">' + esc(x.titolo) +
          '</span></div><b style="font-family:var(--titolo);font-size:22px;color:' + (x.vinta ? "var(--lime)" : "var(--testo3)") + '">' + q + "</b></div>";
        x.eventi.forEach(function (e) {
          html += '<div class="riga-evento"><span class="segno' + (e.preso ? "" : " no") + '">' + (e.preso ? "✓" : "✕") +
            '</span><span class="nome">' + esc(e.partita) + '</span><span class="es">' + esc(e.esito) + '</span><span class="rs">' + esc(e.ris) + "</span></div>";
        });
        html += '<span class="nota" style="padding:0;font-size:11px">Il modello dava il ' + pct(x.prob) + (x.categoria === "sistemi" ? " di vincere almeno una combinazione" : " che uscisse tutto") + "</span></div>";
      });
      html += "</div>";
    }
    html += "</div>";

    return html + '<p class="nota-piccola">Ogni partita e ogni schedina restano salvate: contano tutte nei totali qui sopra, anche se non vengono elencate.<br><br>App versione ' + VERSIONE_APP + '</p></div>';
  }

  // ---------------------------------------------------------------
  //  MIE GIOCATE: registro personale con puntate Kelly 1/4
  // ---------------------------------------------------------------
  // Il registro vive solo sul telefono (memoria del browser): budget,
  // obiettivo e giocate. I risultati arrivano da app.json, quindi le
  // giocate si chiudono da sole appena la partita finisce.
  //
  // La puntata segue il criterio di Kelly frazionato:
  //   vantaggio = p * quota - 1
  //   Kelly pieno = vantaggio / (quota - 1)
  //   puntata = un quarto del Kelly pieno, mai oltre il TETTO della banca
  // L'obiettivo di guadagno NON entra nel calcolo: serve solo a misurare
  // a che punto si e' e quanto manca, con i risultati veri.
  var CHIAVE_REGISTRO = "previsioni-registro-v1";
  var FRAZIONE_KELLY = 0.25;
  var TETTO = 0.03;                 // mai piu' del 3% della banca su una giocata
  var PUNTATA_MINIMA = 1;           // sotto, i bookmaker italiani non accettano
  var MIN_PER_STIMA = 20;           // giocate chiuse prima di proiettare

  // Gli esiti principali, sempre in vista nel modulo.
  var ESITI = [
    { k: "1", n: "1", g: "Esito finale", p: function (m) { return m.p["1"]; } },
    { k: "X", n: "X", g: "Esito finale", p: function (m) { return m.p["X"]; } },
    { k: "2", n: "2", g: "Esito finale", p: function (m) { return m.p["2"]; } },
    { k: "1X", n: "1X", g: "Doppia chance", p: function (m) { return m.dc["1X"]; } },
    { k: "12", n: "12", g: "Doppia chance", p: function (m) { return m.dc["12"]; } },
    { k: "X2", n: "X2", g: "Doppia chance", p: function (m) { return m.dc["X2"]; } },
    { k: "over15", n: "Over 1.5", g: "Under / Over", p: function (m) { return m.gol.over15; } },
    { k: "under15", n: "Under 1.5", g: "Under / Over", p: function (m) { return m.gol.under15; } },
    { k: "over25", n: "Over 2.5", g: "Under / Over", p: function (m) { return m.gol.over25; } },
    { k: "under25", n: "Under 2.5", g: "Under / Over", p: function (m) { return m.gol.under25; } },
    { k: "over35", n: "Over 3.5", g: "Under / Over", p: function (m) { return m.gol.over35; } },
    { k: "under35", n: "Under 3.5", g: "Under / Over", p: function (m) { return m.gol.under35; } },
    { k: "gol", n: "Gol", g: "Gol / NoGol", p: function (m) { return m.gg.gol; } },
    { k: "nogol", n: "NoGol", g: "Gol / NoGol", p: function (m) { return m.gg.nogol; } }
  ];
  var GRUPPI_PRINCIPALI = { "Esito finale": 1, "Doppia chance": 1, "Under / Over": 1, "Gol / NoGol": 1 };

  // Tutti gli esiti che il motore propone per una partita: i principali,
  // gli "Altri mercati" della scheda (multigol, gol di squadra, handicap,
  // combinazioni, altri totali) e i risultati esatti piu' probabili.
  function esitiPartita(m) {
    var lista = ESITI.map(function (e) {
      return { k: e.k, n: e.n, nome: e.n, g: e.g, p: e.p(m) };
    });
    var vocab = (stato.dati && stato.dati.mercati) || [];
    vocab.forEach(function (g, i) {
      if (!g.k) return;                        // app.json di una versione vecchia
      var titolo = g.t.replace("{casa}", m.casa).replace("{fuori}", m.fuori);
      var valori = (m.altri || [])[i] || [];
      g.k.forEach(function (k, j) {
        var p = (valori[j] || 0) / 1000;
        if (!(p > 0)) return;                  // sotto lo 0,5%: nessuno lo quota
        var lungo = ((g.lunghi || [])[j] || g.n[j])
          .replace(/^Casa\b/, m.casa).replace(/^Ospite\b/, m.fuori);
        lista.push({ k: k, n: g.n[j], nome: lungo, g: titolo, p: p });
      });
    });
    (m.esatti || []).forEach(function (e) {
      lista.push({ k: e.r, n: e.r, nome: "Risultato " + e.r, g: "Risultato esatto", p: e.p });
    });
    return lista;
  }
  function trovaEsito(m, k) {
    var lista = esitiPartita(m);
    for (var i = 0; i < lista.length; i++) if (lista[i].k === k) return lista[i];
    return null;
  }
  // l'esito opposto, per togliere il margine dalle quote a coppie
  var OPPOSTO = { over25: "under25", under25: "over25", gol: "nogol", nogol: "gol" };

  // Se un esito si e' verificato: la stessa regola del motore
  // (esito_avvenuto in previsioni.py), comprese combinazioni e
  // risultati esatti. null se l'esito non e' riconosciuto.
  function avvenuto(k, gc, ga) {
    k = String(k);
    if (k.indexOf("+") > 0) {
      var parti = k.split("+").map(function (x) { return avvenuto(x, gc, ga); });
      if (parti.some(function (v) { return v === null; })) return null;
      return parti.every(Boolean);
    }
    var esatto = /^(\d+)-(\d+)$/.exec(k);
    if (esatto) return gc === +esatto[1] && ga === +esatto[2];
    var t = gc + ga, s = gc - ga;
    var mg = /^(casa_|fuori_)?mg_(\d+)_(\d+)$/.exec(k);
    if (mg) {
      var v = mg[1] === "casa_" ? gc : mg[1] === "fuori_" ? ga : t;
      return v >= +mg[2] && v <= +mg[3];
    }
    var tabella = {
      "1": gc > ga, "X": gc === ga, "2": gc < ga,
      "1X": gc >= ga, "12": gc !== ga, "X2": gc <= ga,
      over05: t >= 1, under05: t < 1, over15: t >= 2, under15: t < 2,
      over25: t >= 3, under25: t < 3, over35: t >= 4, under35: t < 4,
      over45: t >= 5, under45: t < 5, pari: t % 2 === 0, dispari: t % 2 === 1,
      gol: gc > 0 && ga > 0, gol_gol: gc > 0 && ga > 0,
      nogol: !(gc > 0 && ga > 0), no_gol: !(gc > 0 && ga > 0),
      casa_segna: gc > 0, casa_nonsegna: gc === 0,
      fuori_segna: ga > 0, fuori_nonsegna: ga === 0,
      casa_over15: gc >= 2, casa_under15: gc < 2, casa_over25: gc >= 3, casa_under25: gc < 3,
      fuori_over15: ga >= 2, fuori_under15: ga < 2, fuori_over25: ga >= 3, fuori_under25: ga < 3,
      casa_2plus: s >= 2, casa_3plus: s >= 3, fuori_2plus: s <= -2, fuori_3plus: s <= -3,
      casa_h1: s >= -1, fuori_h1: s <= 1
    };
    return k in tabella ? tabella[k] : null;
  }

  // ---- memoria -----------------------------------------------------
  function registroVuoto() { return { budget: null, obiettivo: null, scadenza: null, giocate: [] }; }
  function leggiRegistro() {
    try {
      var r = JSON.parse(localStorage.getItem(CHIAVE_REGISTRO) || "null");
      if (r && Array.isArray(r.giocate)) return r;
    } catch (e) {}
    return registroVuoto();
  }
  function salvaRegistro() {
    try { localStorage.setItem(CHIAVE_REGISTRO, JSON.stringify(registro)); return true; }
    catch (e) { avviso("Non riesco a salvare sul telefono: la memoria del browser è piena o bloccata."); return false; }
  }
  var registro = leggiRegistro();

  // ---- numeri ------------------------------------------------------
  function numero(s) {
    var v = parseFloat(String(s == null ? "" : s).replace(",", ".").replace(/[^\d.\-]/g, ""));
    return isNaN(v) ? null : v;
  }
  function euro(v, segnoSempre) {
    if (v == null || isNaN(v)) return "–";
    var s = Math.abs(v).toFixed(2).replace(".", ",").replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    var pre = v < 0 ? "−" : (segnoSempre && v > 0 ? "+" : "");
    return pre + s + " €";
  }
  function arrotondaPuntata(v) { return Math.floor(v * 2) / 2; }   // a 50 centesimi, per difetto

  function profitto(g) {
    if (g.stato === "vinta") return g.puntata * (g.quota - 1);
    if (g.stato === "persa") return -g.puntata;
    return 0;
  }

  // chiude da sola ogni giocata la cui partita ha un risultato
  function chiudiGiocate() {
    var ris = (stato.dati && stato.dati.risultati) || {};
    var cambiate = 0;
    registro.giocate.forEach(function (g) {
      if (g.stato !== "attesa" || !(String(g.fid) in ris)) return;
      var r = ris[String(g.fid)];
      if (r === null) { g.stato = "annullata"; g.ris = "rinviata"; }
      else {
        var esito = avvenuto(g.esito, r[0], r[1]);
        if (esito === null) return;          // si segna a mano
        g.stato = esito ? "vinta" : "persa";
        g.ris = r[0] + "-" + r[1];
      }
      g.chiusa = new Date().toISOString();
      cambiate++;
    });
    if (cambiate) salvaRegistro();
  }

  function conti() {
    var budget = registro.budget || 0;
    var chiuse = registro.giocate.filter(function (g) { return g.stato === "vinta" || g.stato === "persa"; });
    var inCorso = registro.giocate.filter(function (g) { return g.stato === "attesa"; });
    // in ordine di partita: cosi' il grafico segue il calendario
    chiuse.sort(function (a, b) { return a.data < b.data ? -1 : a.data > b.data ? 1 : 0; });
    var prof = 0, giocato = 0, vinte = 0;
    var punti = [budget];
    chiuse.forEach(function (g) {
      prof += profitto(g); giocato += g.puntata;
      if (g.stato === "vinta") vinte++;
      punti.push(budget + prof);
    });
    var esposto = inCorso.reduce(function (s, g) { return s + g.puntata; }, 0);
    return {
      budget: budget, banca: budget + prof, disponibile: budget + prof - esposto,
      profitto: prof, giocato: giocato, roi: giocato ? prof / giocato : null,
      chiuse: chiuse, inCorso: inCorso, vinte: vinte, esposto: esposto, punti: punti
    };
  }

  // Kelly 1/4 con tetto. Restituisce tutto quello che serve al pannello.
  function kelly(p, q, banca) {
    if (!p || !q || q <= 1) return null;
    var vantaggio = p * q - 1;
    var pieno = vantaggio / (q - 1);
    var frazione = Math.max(0, Math.min(pieno * FRAZIONE_KELLY, TETTO));
    return {
      implicita: 1 / q, vantaggio: vantaggio, pieno: pieno,
      frazione: frazione, limitata: pieno * FRAZIONE_KELLY > TETTO,
      puntata: banca > 0 ? arrotondaPuntata(banca * frazione) : 0
    };
  }

  // probabilita' del mercato per quell'esito, margine tolto (se c'e')
  function probMercato(m, k) {
    var mk = m.mercato;
    if (mk && mk["1"] != null) {
      if (k === "1" || k === "X" || k === "2") return mk[k];
      if (k === "1X") return mk["1"] + mk["X"];
      if (k === "12") return mk["1"] + mk["2"];
      if (k === "X2") return mk["X"] + mk["2"];
    }
    var q = m.quote || {};
    var o = OPPOSTO[k];
    if (o && q[k] && q[o]) return (1 / q[k]) / (1 / q[k] + 1 / q[o]);
    return null;
  }

  // ---- grafici -----------------------------------------------------
  // Andamento della banca giocata dopo giocata, con la linea del budget
  // iniziale e quella dell'obiettivo.
  function graficoBanca(c) {
    var punti = c.punti;
    var W = 340, H = 170, sx = 44, dx = 10, su = 14, giu = 24;
    var meta = registro.obiettivo ? c.budget + registro.obiettivo : null;
    var valori = punti.slice();
    valori.push(c.budget);
    if (meta != null) valori.push(meta);
    var min = Math.min.apply(null, valori), max = Math.max.apply(null, valori);
    var margine = (max - min) * 0.12 || Math.max(1, c.budget * 0.05);
    min -= margine; max += margine;
    var n = Math.max(punti.length - 1, 1);
    function X(i) { return sx + (W - sx - dx) * i / n; }
    function Y(v) { return su + (H - su - giu) * (1 - (v - min) / (max - min)); }

    var svg = '<svg class="grafico" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Andamento della banca">';
    // griglia leggera: quattro valori
    for (var i = 0; i <= 3; i++) {
      var v = min + (max - min) * i / 3;
      svg += '<line class="g-griglia" x1="' + sx + '" x2="' + (W - dx) + '" y1="' + Y(v).toFixed(1) + '" y2="' + Y(v).toFixed(1) + '"/>' +
             '<text class="g-asse" x="' + (sx - 6) + '" y="' + (Y(v) + 4).toFixed(1) + '" text-anchor="end">' + Math.round(v) + "</text>";
    }
    // budget iniziale e obiettivo
    svg += '<line class="g-base" x1="' + sx + '" x2="' + (W - dx) + '" y1="' + Y(c.budget).toFixed(1) + '" y2="' + Y(c.budget).toFixed(1) + '"/>';
    if (meta != null) {
      svg += '<line class="g-meta" x1="' + sx + '" x2="' + (W - dx) + '" y1="' + Y(meta).toFixed(1) + '" y2="' + Y(meta).toFixed(1) + '"/>' +
             '<text class="g-meta-t" x="' + (W - dx) + '" y="' + (Y(meta) - 5).toFixed(1) + '" text-anchor="end">obiettivo ' + Math.round(meta) + " €</text>";
    }
    if (punti.length > 1) {
      var linea = punti.map(function (v, i) { return (i ? "L" : "M") + X(i).toFixed(1) + " " + Y(v).toFixed(1); }).join(" ");
      var sopra = punti[punti.length - 1] >= c.budget;
      svg += '<path class="g-area' + (sopra ? "" : " giu") + '" d="' + linea + " L" + X(punti.length - 1).toFixed(1) + " " + Y(c.budget).toFixed(1) +
             " L" + X(0).toFixed(1) + " " + Y(c.budget).toFixed(1) + ' Z"/>';
      svg += '<path class="g-linea' + (sopra ? "" : " giu") + '" d="' + linea + '"/>';
      var ux = X(punti.length - 1), uy = Y(punti[punti.length - 1]);
      svg += '<circle class="g-punto' + (sopra ? "" : " giu") + '" cx="' + ux.toFixed(1) + '" cy="' + uy.toFixed(1) + '" r="4.5"/>';
    }
    svg += '<text class="g-asse" x="' + sx + '" y="' + (H - 6) + '">inizio</text>' +
           '<text class="g-asse" x="' + (W - dx) + '" y="' + (H - 6) + '" text-anchor="end">' +
           (punti.length - 1) + (punti.length === 2 ? " giocata" : " giocate") + "</text></svg>";
    return svg;
  }

  // Probabilita' del modello contro probabilita' implicita nella quota,
  // sulla stessa scala da 0 a 100: lo spazio fra le due e' il vantaggio.
  function barraVantaggio(p, implicita, pMercato) {
    // la scala si stringe attorno ai valori, cosi' anche pochi punti
    // di differenza si vedono bene
    var valori = [p, implicita].concat(pMercato != null ? [pMercato] : []);
    var lo = Math.max(0, Math.floor((Math.min.apply(null, valori) - 0.08) * 20) / 20);
    var hi = Math.min(1, Math.ceil((Math.max.apply(null, valori) + 0.08) * 20) / 20);
    function pos(v) { return ((v - lo) / (hi - lo) * 100).toFixed(1); }
    var a = Math.min(p, implicita), b = Math.max(p, implicita);
    var buono = p > implicita;
    return '<div class="vantaggio-barra"><div class="vb-pista">' +
      '<div class="vb-fascia ' + (buono ? "buono" : "cattivo") + '" style="left:' + pos(a) + "%;width:" + (pos(b) - pos(a)).toFixed(1) + '%"></div>' +
      '<div class="vb-segno quota" style="left:' + pos(implicita) + '%"></div>' +
      (pMercato != null ? '<div class="vb-segno mercato" style="left:' + pos(pMercato) + '%"></div>' : "") +
      '<div class="vb-segno modello" style="left:' + pos(p) + '%"></div>' +
      '</div><div class="vb-scala"><span>' + pct(lo) + "</span><span>" + pct(hi) + "</span></div>" +
      '<div class="vb-leggenda"><span><i class="modello"></i>Modello ' + pct(p, 1) + "</span>" +
      '<span><i class="quota"></i>Quota ' + pct(implicita, 1) + "</span>" +
      (pMercato != null ? '<span><i class="mercato"></i>Mercato ' + pct(pMercato, 1) + "</span>" : "") +
      "</div></div>";
  }

  // Quanto della banca va sulla giocata: Kelly pieno, 1/4 e tetto.
  function barraPuntata(k) {
    var scala = 0.12;   // la barra arriva al 12% della banca
    function w(v) { return Math.max(0, Math.min(v / scala, 1)) * 100; }
    return '<div class="puntata-barra"><div class="pb-pista">' +
      '<div class="pb-pieno" style="width:' + w(Math.max(k.pieno, 0)).toFixed(1) + '%"></div>' +
      '<div class="pb-quarto" style="width:' + w(k.frazione).toFixed(1) + '%"></div>' +
      '<div class="pb-tetto" style="left:' + w(TETTO).toFixed(1) + '%"></div></div>' +
      '<div class="vb-leggenda"><span><i class="quarto"></i>Kelly 1/4 ' + pct(k.frazione, 1) + "</span>" +
      '<span><i class="pieno"></i>Kelly pieno ' + pct(Math.max(k.pieno, 0), 1) + "</span>" +
      '<span><i class="tetto"></i>Tetto ' + pct(TETTO, 0) + "</span></div></div>";
  }

  // ---- schermata principale ---------------------------------------
  function vistaMie() {
    chiudiGiocate();
    var c = conti();
    var html = testa("Mie giocate", "Kelly 1/4 · registro salvato sul telefono") + avvisoFuoriLinea() + '<div class="corpo">';

    if (!registro.budget) {
      return html + '<div class="riquadro carta"><span class="etichetta">Per iniziare</span>' +
        '<div class="verdetto">Imposta il budget</div>' +
        '<span class="nota" style="padding:0">Soldi che puoi permetterti di perdere tutti. Su questa cifra si calcolano le puntate.</span>' +
        moduloImpostazioni() + "</div></div>";
    }

    // riepilogo
    html += '<div class="riquadro carta banca-carta"><div class="riga"><div><span class="etichetta">Banca</span>' +
      '<div class="banca-num">' + euro(c.banca) + "</div></div>" +
      '<div class="dx"><span class="etichetta">Profitto</span><div class="banca-prof ' + (c.profitto > 0 ? "piu" : c.profitto < 0 ? "meno" : "fioco") + '">' +
      euro(c.profitto, true) + "</div></div></div>" + graficoBanca(c) +
      '<div class="tre"><div><b>' + (c.roi == null ? "–" : (c.roi >= 0 ? "+" : "−") + Math.abs(c.roi * 100).toFixed(1).replace(".", ",") + "%") +
      '</b><span>rendimento sul giocato</span></div><div><b>' + c.chiuse.length + "</b><span>chiuse · " + c.vinte + (c.vinte === 1 ? " vinta" : " vinte") +
      "</span></div><div><b>" + c.inCorso.length + "</b><span>in corso · " + euro(c.esposto) + "</span></div></div></div>";

    html += '<a class="bottone-grande" href="#/nuova">+ Nuova giocata</a>';

    // obiettivo
    html += cartaObiettivo(c);

    // elenco giocate
    var inCorso = registro.giocate.filter(function (g) { return g.stato === "attesa"; })
      .sort(function (a, b) { return a.data < b.data ? -1 : 1; });
    var concluse = registro.giocate.filter(function (g) { return g.stato !== "attesa"; })
      .sort(function (a, b) { return a.data > b.data ? -1 : 1; });
    var mostra = stato.elenco === "concluse" ? concluse : inCorso;
    html += '<div class="riga-chip" style="padding:4px 0 0 0">' +
      '<button class="chip' + (stato.elenco !== "concluse" ? " attivo" : "") + '" data-elenco="corso">In corso · ' + inCorso.length + "</button>" +
      '<button class="chip' + (stato.elenco === "concluse" ? " attivo" : "") + '" data-elenco="concluse">Concluse · ' + concluse.length + "</button></div>";
    if (!mostra.length) {
      html += '<div class="nota" style="padding:6px 2px">' + (stato.elenco === "concluse" ? "Nessuna giocata conclusa." : "Nessuna giocata in corso.") + "</div>";
    }
    mostra.forEach(function (g) { html += cartaGiocata(g); });

    // impostazioni e copia di sicurezza
    html += '<div class="carta apribile' + (stato.impostazioni ? " aperta" : "") + '"><button data-impostazioni="1" aria-expanded="' + !!stato.impostazioni + '">' +
      '<span><span class="t1">Budget e obiettivo</span><span class="t2">Budget ' + euro(registro.budget) +
      (registro.obiettivo ? " · obiettivo +" + euro(registro.obiettivo) : " · nessun obiettivo") + '</span></span><span class="bottone">' +
      (stato.impostazioni ? "Chiudi" : "Modifica") + "</span></button>" +
      (stato.impostazioni ? '<div class="dentro">' + moduloImpostazioni() + "</div>" : "") + "</div>";
    html += '<div class="carta apribile' + (stato.copia ? " aperta" : "") + '"><button data-copia="1" aria-expanded="' + !!stato.copia + '">' +
      '<span><span class="t1">Copia di sicurezza</span><span class="t2">Il registro sta solo su questo telefono</span></span><span class="bottone">' +
      (stato.copia ? "Chiudi" : "Apri") + "</span></button>" +
      (stato.copia ? '<div class="dentro"><span class="nota">Se cancelli i dati del browser o cambi telefono il registro si perde. Esportalo ogni tanto e reimportalo quando serve.</span>' +
        '<div class="due-bottoni"><button class="bottone-secondario" data-esporta="1">Esporta</button>' +
        '<label class="bottone-secondario">Importa<input type="file" accept="application/json,.json" data-importa="1" hidden></label></div></div>' : "") + "</div>";

    return html + '<p class="nota-piccola">La puntata è un quarto del criterio di Kelly, mai oltre il ' + pct(TETTO) +
      ' della banca disponibile. Kelly presuppone che le probabilità del modello siano giuste: la verifica dice che sull\'1X2 il mercato è più preciso, quindi i vantaggi mostrati vanno presi con cautela.</p></div>';
  }

  function cartaObiettivo(c) {
    if (!registro.obiettivo) {
      return '<div class="riquadro carta"><span class="etichetta">Obiettivo</span><span class="nota" style="padding:0">Nessun obiettivo impostato. Aggiungilo da "Budget e obiettivo" qui sotto per vedere a che punto sei.</span></div>';
    }
    var ob = registro.obiettivo;
    var fatto = Math.max(0, Math.min(c.profitto / ob, 1));
    var html = '<div class="riquadro carta"><div class="riga"><div><span class="etichetta">Obiettivo</span>' +
      '<div class="verdetto" style="margin-top:4px">+' + euro(ob) + "</div></div>" +
      '<div class="dx"><span class="etichetta">' + (registro.scadenza ? "entro il" : "") + "</span>" +
      (registro.scadenza ? '<div class="scad">' + dataLunga(registro.scadenza) + "</div>" : "") + "</div></div>" +
      '<div class="traccia obiettivo"><div style="width:' + (fatto * 100).toFixed(1) + '%"></div></div>' +
      '<div class="riga nota" style="padding:0"><span>' + (c.profitto >= ob ? "Raggiunto" : "Fatto " + pct(fatto)) + "</span><span>" +
      (c.profitto >= ob ? "" : "mancano " + euro(ob - c.profitto)) + "</span></div>";

    // proiezione: solo sui risultati veri, e solo con abbastanza giocate
    var testo;
    if (c.profitto >= ob) {
      testo = "Obiettivo raggiunto. Non alzare le puntate per \"fare di più\": il metodo resta lo stesso.";
    } else if (c.chiuse.length < MIN_PER_STIMA) {
      testo = "Servono almeno " + MIN_PER_STIMA + " giocate chiuse per una stima credibile: ne hai " + c.chiuse.length + ".";
    } else {
      var primo = new Date(c.chiuse[0].data), oggi = new Date();
      var giorni = Math.max(7, (oggi - primo) / 86400000);
      var alGiorno = c.profitto / giorni;
      if (alGiorno <= 0) {
        testo = "Finora il saldo è negativo: a questo ritmo l'obiettivo non si raggiunge. Non aumentare le puntate per recuperare.";
      } else {
        var servono = Math.ceil((ob - c.profitto) / alGiorno);
        var arrivo = new Date(); arrivo.setDate(arrivo.getDate() + servono);
        testo = "Al ritmo attuale (" + euro(alGiorno) + " al giorno) ci arrivi in circa " + servono + " giorni, verso il " +
          arrivo.getDate() + " " + MESI[arrivo.getMonth()] + ".";
        if (registro.scadenza && chiaveGiorno(arrivo) > registro.scadenza) testo += " Dopo la scadenza che ti sei dato.";
      }
    }
    return html + '<span class="nota" style="padding:0">' + esc(testo) + "</span></div>";
  }
  function dataLunga(chiave) {
    var p = chiave.split("-");
    return +p[2] + " " + MESI[+p[1] - 1];
  }

  function moduloImpostazioni() {
    return '<div class="modulo">' +
      '<label class="campo"><span>Budget iniziale (€)</span><input inputmode="decimal" data-campo="budget" value="' + (registro.budget || "") + '" placeholder="es. 300"></label>' +
      '<label class="campo"><span>Obiettivo di guadagno (€)</span><input inputmode="decimal" data-campo="obiettivo" value="' + (registro.obiettivo || "") + '" placeholder="facoltativo"></label>' +
      '<label class="campo"><span>Entro il</span><input type="date" data-campo="scadenza" value="' + (registro.scadenza || "") + '"></label>' +
      '<button class="bottone-grande" data-salva-impostazioni="1">Salva</button></div>';
  }

  function cartaGiocata(g) {
    var d = data(g.data);
    var aperta = stato.giocataAperta === g.id;
    var badgeCls = g.stato === "vinta" ? "vinta" : g.stato === "persa" ? "persa" : g.stato === "annullata" ? "annullata" : "attesa";
    var badgeTxt = { vinta: "VINTA", persa: "PERSA", annullata: "ANNULLATA", attesa: "IN CORSO" }[g.stato];
    var p = profitto(g);
    var html = '<article class="giocata carta mia"><button class="mia-testa" data-giocata="' + esc(g.id) + '">' +
      '<div class="riga"><div class="etichette"><span class="esito-badge ' + badgeCls + '">' + badgeTxt + "</span>" +
      '<span class="lega">' + esc(nomeLega(g.campionato).nome) + " · " + dataBreve(d) + " " + oraDi(d) + "</span></div>" +
      (g.ris ? '<span class="rs-mio">' + esc(g.ris) + "</span>" : "") + "</div>" +
      '<div class="riga"><b class="mia-partita">' + esc(g.partita) + '</b><span class="esito">' + esc(g.nome) + "</span></div>" +
      '<div class="mia-numeri"><span>quota <b>' + quota(g.quota) + "</b></span><span>puntata <b>" + euro(g.puntata) + "</b></span>" +
      (g.stato === "vinta" || g.stato === "persa" ? '<span class="' + (p >= 0 ? "piu" : "meno") + '"><b>' + euro(p, true) + "</b></span>"
        : g.stato === "attesa" ? "<span>vincita <b>" + euro(g.puntata * g.quota) + "</b></span>" : "<span>rimborsata</span>") + "</div></button>";
    if (aperta) {
      html += '<div class="mia-azioni"><span class="nota">Modello ' + pct(g.prob, 1) + " · vantaggio stimato " + segno(g.prob * g.quota - 1) +
        "% · puntata consigliata " + euro(g.consigliata) + "</span><div class=\"quattro\">";
      if (g.stato === "attesa") {
        html += '<button data-segna="vinta" data-id="' + esc(g.id) + '">Vinta</button>' +
                '<button data-segna="persa" data-id="' + esc(g.id) + '">Persa</button>' +
                '<button data-segna="annullata" data-id="' + esc(g.id) + '">Annullata</button>';
      } else {
        html += '<button data-segna="attesa" data-id="' + esc(g.id) + '">Riapri</button>';
      }
      html += '<button class="elimina" data-elimina="' + esc(g.id) + '">Elimina</button></div></div>';
    }
    return html + "</article>";
  }

  // ---- nuova giocata ----------------------------------------------
  function partiteGiocabili() {
    var ora = Date.now();
    return ((stato.dati && stato.dati.partite) || []).filter(function (m) { return data(m.data).getTime() > ora; });
  }
  function normalizza(s) {
    return String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  }

  function listaRicerca() {
    var q = normalizza(stato.nuova.cerca).trim();
    var tutte = partiteGiocabili();
    var trovate = q ? tutte.filter(function (m) {
      return normalizza(m.casa + " " + m.fuori + " " + m.campionato).indexOf(q) >= 0;
    }) : tutte;
    var html = "";
    if (!trovate.length) return '<div class="nota" style="padding:8px 2px">Nessuna partita in programma con questo nome.</div>';
    trovate.slice(0, 40).forEach(function (m) {
      var d = data(m.data);
      html += '<button class="scelta-partita" data-scegli="' + esc(m.id) + '"><div class="ora"><b>' + oraDi(d) + "</b><span>" + dataBreve(d) + "</span></div>" +
        '<div class="sp-testi"><b>' + esc(m.casa) + " – " + esc(m.fuori) + "</b><small>" + bandiera(nomeLega(m.campionato).paese) + " " +
        esc(nomeLega(m.campionato).nome) + "</small></div></button>";
    });
    if (trovate.length > 40) html += '<div class="nota" style="padding:6px 2px">Altre ' + (trovate.length - 40) + " partite: scrivi il nome di una squadra per trovarle.</div>";
    return html;
  }

  function vistaNuova(id) {
    var n = stato.nuova;
    if (id && String(n.fid) !== String(id)) {
      stato.nuova = n = { fid: id, esito: null, quota: "", puntata: "", toccata: false, cerca: "" };
    }
    var indietro = '<a class="indietro" href="#/mie"><svg viewBox="0 0 24 24"><path d="M15 5l-7 7 7 7"></path></svg>Mie giocate</a>';
    var html = indietro + '<header class="testa" style="padding-top:4px"><h1 class="titolo">Nuova giocata</h1></header><div class="corpo">';
    if (!registro.budget) {
      return html + '<div class="vuoto"><h3>Prima il budget</h3>Imposta il budget nella sezione Mie giocate: serve per calcolare la puntata.<br><button data-vai="#/mie">Imposta</button></div></div>';
    }
    var m = n.fid ? trovaPartita(n.fid) : null;
    if (m && data(m.data).getTime() <= Date.now()) m = null;

    // 1. la partita
    if (!m) {
      html += '<h2 class="sezione">1 · Partita</h2>' +
        '<input class="cerca" type="search" data-cerca="1" placeholder="Cerca una squadra o un campionato" value="' + esc(n.cerca) + '" autocomplete="off">' +
        '<div id="lista-ricerca" class="lista-ricerca">' + listaRicerca() + "</div></div>";
      return html;
    }
    var d = data(m.data);
    html += '<div class="carta partita-scelta"><div><span class="lega">' + bandiera(nomeLega(m.campionato).paese) + " " + esc(nomeLega(m.campionato).nome) +
      " · " + dataBreve(d) + " " + oraDi(d) + '</span><b>' + esc(m.casa) + " – " + esc(m.fuori) + "</b></div>" +
      '<button class="bottone-secondario piccolo" data-cambia="1">Cambia</button></div>';

    // 2. l'esito
    html += '<h2 class="sezione">2 · Esito</h2>';
    var gruppi = [], perTitolo = {};
    esitiPartita(m).forEach(function (e) {
      if (!perTitolo[e.g]) { perTitolo[e.g] = { t: e.g, v: [] }; gruppi.push(perTitolo[e.g]); }
      perTitolo[e.g].v.push(e);
    });
    function tessera(e) {
      var qm = (m.quote || {})[e.k];
      return '<button class="esito-scelta' + (n.esito === e.k ? " attivo" : "") + '" data-esito="' + esc(e.k) + '" title="' + esc(e.nome) + '"><b>' + esc(e.n) +
        "</b><small>" + pct(e.p) + "</small>" + (qm ? '<span class="qm">' + quota(qm) + "</span>" : "") + "</button>";
    }
    var altri = 0;
    gruppi.forEach(function (g) {
      if (GRUPPI_PRINCIPALI[g.t]) {
        html += '<div class="etichetta" style="padding:2px 2px 0">' + esc(g.t) + '</div><div class="griglia-esiti">' +
          g.v.map(tessera).join("") + "</div>";
        return;
      }
      if (!altri++) html += '<div class="etichetta" style="padding:8px 2px 0">Altri mercati del modello</div>';
      var dentro = g.v.some(function (e) { return e.k === n.esito; });
      var aperto = !!(n.aperti || {})[g.t] || dentro;
      html += '<div class="carta apribile mercati' + (aperto ? " aperta" : "") + '"><button data-gruppo-esiti="' + esc(g.t) + '" aria-expanded="' + aperto + '">' +
        '<span><span class="t1">' + esc(g.t) + '</span><span class="t2">' + g.v.length + (g.v.length === 1 ? " esito" : " esiti") +
        (dentro ? " · scelto " + esc(trovaEsito(m, n.esito).n) : "") + '</span></span><span class="bottone">' + (aperto ? "Chiudi" : "Apri") + "</span></button>" +
        (aperto ? '<div class="dentro"><div class="griglia-esiti">' + g.v.map(tessera).join("") + "</div></div>" : "") + "</div>";
    });
    if (m.quote) html += '<div class="nota">Il numero in basso è la quota media dei bookmaker, quando disponibile.</div>';

    if (!n.esito) return html + "</div>";

    // 3. quota e puntata
    html += '<h2 class="sezione">3 · Quota e puntata</h2>' +
      '<label class="campo"><span>Quota del tuo bookmaker</span><input inputmode="decimal" data-quota="1" value="' + esc(n.quota) + '" placeholder="es. 2,10"></label>' +
      '<div id="calcolo">' + pannelloCalcolo(m) + "</div>" +
      '<label class="campo"><span>Puntata (€)</span><input inputmode="decimal" data-puntata="1" value="' + esc(n.puntata) + '"></label>' +
      '<div id="salva-box">' + boxSalva(m) + "</div>";
    return html + "</div>";
  }

  function calcoloCorrente(m) {
    var n = stato.nuova, e = n.esito ? trovaEsito(m, n.esito) : null;
    if (!e) return null;
    var p = e.p, q = numero(n.quota);
    var k = kelly(p, q, conti().disponibile);
    return { e: e, p: p, q: q, k: k, pm: probMercato(m, e.k) };
  }

  function pannelloCalcolo(m) {
    var c = calcoloCorrente(m);
    if (!c) return "";
    if (!c.k) return '<div class="nota" style="padding:4px 2px">Scrivi la quota per vedere quanto puntare.</div>';
    var k = c.k, buono = k.vantaggio > 0;
    var html = '<div class="riquadro carta calcolo ' + (buono ? "buono" : "cattivo") + '">' +
      '<div class="riga"><div><span class="etichetta">Vantaggio stimato</span><div class="verdetto ' + (buono ? "verde" : "arancio") + '">' +
      (k.vantaggio >= 0 ? "+" : "−") + Math.abs(k.vantaggio * 100).toFixed(1).replace(".", ",") + "%</div></div>" +
      '<div class="dx"><span class="etichetta">' + (buono ? "Punta" : "Consiglio") + '</span><div class="verdetto ' + (buono ? "" : "arancio") + '">' +
      (buono ? euro(k.puntata) : "Non giocare") + "</div></div></div>" +
      barraVantaggio(c.p, k.implicita, c.pm);
    if (buono) html += barraPuntata(k);
    html += '<span class="nota" style="padding:0">';
    if (buono) {
      html += "Il modello dà " + pct(c.p, 1) + ", la quota ne paga " + pct(k.implicita, 1) + ". Kelly 1/4 = " + pct(k.frazione, 1) +
        " della banca disponibile (" + euro(conti().disponibile) + ")" + (k.limitata ? ", ridotto al tetto del " + pct(TETTO) : "") + ".";
      if (k.puntata < PUNTATA_MINIMA) html += " Viene meno della puntata minima di " + euro(PUNTATA_MINIMA) + ": con questo budget conviene lasciarla.";
    } else {
      html += "La quota paga meno di quanto il modello pensa che l'esito valga: nel lungo periodo è una giocata in perdita.";
    }
    html += "</span>";
    if (c.pm != null && Math.abs(c.p - c.pm) >= 0.10) {
      html += '<div class="avviso">' + ICONA_AVVISO + "<span>Il modello si allontana di " + Math.round(Math.abs(c.p - c.pm) * 100) +
        " punti dal mercato (" + pct(c.pm) + "). Nei test, con divergenze così, di solito sbaglia il modello.</span></div>";
    }
    var qm = (m.quote || {})[c.e.k];
    if (qm && c.q && Math.abs(c.q / qm - 1) > 0.2) {
      html += '<div class="avviso">' + ICONA_AVVISO + "<span>La quota scritta è molto diversa dalla media dei bookmaker (" + quota(qm) + "). Controlla di non aver sbagliato.</span></div>";
    }
    return html + "</div>";
  }

  function boxSalva(m) {
    var c = calcoloCorrente(m);
    var puntata = numero(stato.nuova.puntata);
    var pronto = c && c.k && puntata > 0;
    var buono = c && c.k && c.k.vantaggio > 0;
    return '<button class="bottone-grande' + (buono ? "" : " spento") + '" data-registra="1"' + (pronto ? "" : " disabled") + ">" +
      (buono || !pronto ? "Registra giocata" : "Registra comunque") + "</button>";
  }

  // aggiorna solo i pezzi che cambiano, senza togliere la tastiera
  function aggiornaCalcolo() {
    var m = trovaPartita(stato.nuova.fid);
    if (!m) return;
    var c = calcoloCorrente(m);
    if (!stato.nuova.toccata) {
      stato.nuova.puntata = c && c.k && c.k.vantaggio > 0 ? String(c.k.puntata).replace(".", ",") : "";
      var campo = schermo.querySelector("[data-puntata]");
      if (campo) campo.value = stato.nuova.puntata;
    }
    var box = document.getElementById("calcolo");
    if (box) box.innerHTML = pannelloCalcolo(m);
    var s = document.getElementById("salva-box");
    if (s) s.innerHTML = boxSalva(m);
  }

  function registraGiocata() {
    var m = trovaPartita(stato.nuova.fid);
    if (!m) return;
    if (data(m.data).getTime() <= Date.now()) { avviso("La partita è già iniziata."); return; }
    var c = calcoloCorrente(m);
    var puntata = numero(stato.nuova.puntata);
    if (!c || !c.k || !(puntata > 0)) return;
    registro.giocate.push({
      id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
      creata: new Date().toISOString(),
      fid: m.id, data: m.data, campionato: m.campionato,
      partita: m.casa + " – " + m.fuori,
      esito: c.e.k, nome: c.e.nome, quota: c.q, prob: c.p, mercato: c.pm,
      puntata: puntata, consigliata: c.k.vantaggio > 0 ? c.k.puntata : 0,
      stato: "attesa"
    });
    if (!salvaRegistro()) return;
    stato.nuova = { fid: null, esito: null, quota: "", puntata: "", toccata: false, cerca: "" };
    stato.elenco = "corso";
    location.hash = "#/mie";
    avviso("Giocata registrata: si chiude da sola quando arriva il risultato.");
  }

  function salvaImpostazioni() {
    function val(nome) { var el = schermo.querySelector('[data-campo="' + nome + '"]'); return el ? el.value : ""; }
    var b = numero(val("budget"));
    if (!(b > 0)) { avviso("Scrivi un budget maggiore di zero."); return; }
    var o = numero(val("obiettivo"));
    registro.budget = b;
    registro.obiettivo = o > 0 ? o : null;
    registro.scadenza = val("scadenza") || null;
    if (salvaRegistro()) { stato.impostazioni = false; disegna(); avviso("Salvato."); }
  }

  function esportaRegistro() {
    var testo = JSON.stringify(registro, null, 1);
    var nome = "giocate-" + chiaveGiorno(new Date()) + ".json";
    try {
      var file = new File([testo], nome, { type: "application/json" });
      if (navigator.canShare && navigator.canShare({ files: [file] })) {
        navigator.share({ files: [file], title: "Registro giocate" }).catch(function () {});
        return;
      }
    } catch (e) {}
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([testo], { type: "application/json" }));
    a.download = nome;
    document.body.appendChild(a); a.click(); a.remove();
  }

  function importaRegistro(file) {
    var r = new FileReader();
    r.onload = function () {
      try {
        var d = JSON.parse(r.result);
        if (!d || !Array.isArray(d.giocate)) throw new Error("formato");
        if (!window.confirm("Sostituire il registro attuale con quello del file (" + d.giocate.length + " giocate)?")) return;
        registro = d;
        salvaRegistro(); disegna(); avviso("Registro importato.");
      } catch (e) { avviso("Il file non è un registro valido."); }
    };
    r.readAsText(file);
  }

  // ---------------------------------------------------------------
  //  navigazione
  // ---------------------------------------------------------------
  function disegna() {
    var h = location.hash || "#/";
    var vista = "partite";
    var html;
    if (!stato.dati) {
      html = '<div class="vuoto"><h3>Nessun dato</h3>Non riesco a scaricare le previsioni. Controlla la connessione e riprova.' +
             '<br><button data-ricarica="1">Riprova</button></div>';
    } else if (h.indexOf("#/partita/") === 0) {
      html = vistaDettaglio(decodeURIComponent(h.slice(10)));
    } else if (h === "#/giocate") {
      vista = "giocate"; html = vistaGiocate();
    } else if (h === "#/esatti") {
      vista = "esatti"; html = vistaEsatti();
    } else if (h === "#/verifica") {
      vista = "verifica"; html = vistaVerifica();
    } else if (h === "#/mie") {
      vista = "mie"; html = vistaMie();
    } else if (h === "#/nuova" || h.indexOf("#/nuova/") === 0) {
      vista = "mie"; html = vistaNuova(h.length > 8 ? decodeURIComponent(h.slice(8)) : null);
    } else {
      html = vistaPartite();
    }
    schermo.innerHTML = html;
    document.querySelectorAll("#barra a").forEach(function (a) {
      a.classList.toggle("attiva", a.getAttribute("data-vista") === vista);
    });
  }

  schermo.addEventListener("click", function (ev) {
    var el = ev.target.closest("button");
    if (!el) return;
    if (el.hasAttribute("data-giorno")) { stato.giorno = el.getAttribute("data-giorno"); disegna(); }
    else if (el.hasAttribute("data-lega")) { stato.lega = el.getAttribute("data-lega"); disegna(); }
    else if (el.hasAttribute("data-scheda")) { stato.scheda = el.getAttribute("data-scheda"); disegna(); }
    else if (el.hasAttribute("data-apri")) { stato.aperta = !stato.aperta; disegna(); }
    else if (el.hasAttribute("data-mercato")) {
      var g = el.getAttribute("data-mercato");
      stato.mercati[g] = !stato.mercati[g];
      var y = window.scrollY;
      disegna();
      window.scrollTo(0, y);          // si riapre dove si era rimasti
    }
    else if (el.hasAttribute("data-azzera")) { stato.giorno = "tutti"; stato.lega = "tutte"; disegna(); }
    else if (el.hasAttribute("data-ricarica")) { carica(); }
    else if (el.hasAttribute("data-campana")) { gestisciCampana(); }
    // mie giocate
    else if (el.hasAttribute("data-elenco")) { stato.elenco = el.getAttribute("data-elenco"); stato.giocataAperta = null; disegna(); }
    else if (el.hasAttribute("data-giocata")) {
      var gid = el.getAttribute("data-giocata");
      stato.giocataAperta = stato.giocataAperta === gid ? null : gid;
      var y2 = window.scrollY; disegna(); window.scrollTo(0, y2);
    }
    else if (el.hasAttribute("data-segna")) {
      var gs = registro.giocate.filter(function (x) { return x.id === el.getAttribute("data-id"); })[0];
      if (gs) {
        gs.stato = el.getAttribute("data-segna");
        gs.ris = gs.stato === "attesa" ? null : (gs.ris || null);
        gs.manuale = gs.stato !== "attesa";
        // riaperta, si richiude da sola se il risultato e' gia' arrivato
        salvaRegistro();
        var y3 = window.scrollY; disegna(); window.scrollTo(0, y3);
      }
    }
    else if (el.hasAttribute("data-elimina")) {
      if (window.confirm("Eliminare questa giocata dal registro?")) {
        var eid = el.getAttribute("data-elimina");
        registro.giocate = registro.giocate.filter(function (x) { return x.id !== eid; });
        salvaRegistro(); disegna();
      }
    }
    else if (el.hasAttribute("data-impostazioni")) { stato.impostazioni = !stato.impostazioni; disegna(); }
    else if (el.hasAttribute("data-copia")) { stato.copia = !stato.copia; disegna(); }
    else if (el.hasAttribute("data-salva-impostazioni")) { salvaImpostazioni(); }
    else if (el.hasAttribute("data-esporta")) { esportaRegistro(); }
    else if (el.hasAttribute("data-scegli")) {
      stato.nuova = { fid: el.getAttribute("data-scegli"), esito: null, quota: "", puntata: "", toccata: false, cerca: "" };
      disegna(); window.scrollTo(0, 0);
    }
    else if (el.hasAttribute("data-cambia")) {
      stato.nuova = { fid: null, esito: null, quota: "", puntata: "", toccata: false, cerca: "" };
      if (location.hash !== "#/nuova") location.hash = "#/nuova"; else disegna();
    }
    else if (el.hasAttribute("data-esito")) {
      var mq = trovaPartita(stato.nuova.fid);
      stato.nuova.esito = el.getAttribute("data-esito");
      var qm = mq && mq.quote && mq.quote[stato.nuova.esito];
      stato.nuova.quota = qm ? String(qm).replace(".", ",") : "";
      stato.nuova.toccata = false;
      var y4 = window.scrollY; disegna(); window.scrollTo(0, y4);
      aggiornaCalcolo();
    }
    else if (el.hasAttribute("data-gruppo-esiti")) {
      var tg = el.getAttribute("data-gruppo-esiti");
      stato.nuova.aperti = stato.nuova.aperti || {};
      stato.nuova.aperti[tg] = !stato.nuova.aperti[tg];
      var y5 = window.scrollY; disegna(); window.scrollTo(0, y5);
    }
    else if (el.hasAttribute("data-registra")) { registraGiocata(); }
    else if (el.hasAttribute("data-vai")) { location.hash = el.getAttribute("data-vai"); }
  });

  schermo.addEventListener("input", function (ev) {
    var el = ev.target;
    if (el.hasAttribute("data-cerca")) {
      stato.nuova.cerca = el.value;
      var l = document.getElementById("lista-ricerca");
      if (l) l.innerHTML = listaRicerca();
    } else if (el.hasAttribute("data-quota")) {
      stato.nuova.quota = el.value; aggiornaCalcolo();
    } else if (el.hasAttribute("data-puntata")) {
      stato.nuova.puntata = el.value; stato.nuova.toccata = el.value !== "";
      var m = trovaPartita(stato.nuova.fid);
      var s = document.getElementById("salva-box");
      if (m && s) s.innerHTML = boxSalva(m);
    }
  });
  schermo.addEventListener("change", function (ev) {
    var el = ev.target;
    if (el.hasAttribute("data-importa") && el.files && el.files[0]) importaRegistro(el.files[0]);
  });

  window.addEventListener("hashchange", function () { disegna(); window.scrollTo(0, 0); });

  // ---------------------------------------------------------------
  //  dati
  // ---------------------------------------------------------------
  function carica() {
    return fetch("app.json?t=" + Date.now(), { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) { stato.dati = d; stato.fuoriLinea = false; disegna(); })
      .catch(function () {
        // senza rete: il componente offline restituisce l'ultima copia salvata
        return typeof caches !== "undefined" ? caches.match("app.json").then(function (r) {
          if (r) return r.json().then(function (d) { stato.dati = d; stato.fuoriLinea = true; disegna(); });
          disegna();
        }) : disegna();
      });
  }

  // aggiornamento: quando si riapre l'app e ogni cinque minuti
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") carica();
  });
  setInterval(function () { if (document.visibilityState === "visible") carica(); }, 5 * 60 * 1000);

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
      return disegna();
    }
    if (Notification.permission === "denied") { stato.notifiche = "negate"; return disegna(); }
    navigator.serviceWorker.ready
      .then(function (reg) { return reg.pushManager.getSubscription(); })
      .then(function (s) { stato.notifiche = s ? "attive" : "spente"; disegna(); })
      .catch(function () { stato.notifiche = "spente"; disegna(); });
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
        disegna();
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
        disegna();
        avviso("Notifiche attivate. Ti avviso quando una previsione cambia molto con le formazioni ufficiali.");
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
      .then(function () { stato.notifiche = "spente"; disegna(); avviso("Notifiche disattivate."); })
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
