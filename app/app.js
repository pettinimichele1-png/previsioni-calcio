/* Previsioni - logica dell'app.
 * Legge app.json, prodotto ogni mezz'ora dal sistema sul server, e
 * costruisce le cinque schermate: palinsesto, dettaglio, giocate,
 * risultati esatti, verifica.
 */
(function () {
  "use strict";

  var VERSIONE_APP = "9";

  var stato = {
    dati: null,
    fuoriLinea: false,
    giorno: "tutti",
    lega: "tutte",
    scheda: "alta",
    aperta: false,
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
    else if (el.hasAttribute("data-azzera")) { stato.giorno = "tutti"; stato.lega = "tutte"; disegna(); }
    else if (el.hasAttribute("data-ricarica")) { carica(); }
    else if (el.hasAttribute("data-campana")) { gestisciCampana(); }
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
