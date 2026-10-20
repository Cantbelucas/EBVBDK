/* Songwars - lobbyen.

   Henter /state hvert 2,5 sekund og viser den fase serveren siger. Klienten
   bestemmer ingenting: nedtaellingen her er kun en visning af serverens
   frist, og en upload efter fristen bliver afvist af serveren uanset hvad
   uret her viser.

   Al tekst fra andre spillere saettes med textContent - aldrig som HTML. */

(function () {
  "use strict";

  var game = document.getElementById("game");
  if (!game) return;

  var STATE_URL = game.dataset.stateUrl;
  var KICK_URL = game.dataset.kickUrl;
  var WAS_OWNER = game.dataset.owner === "1";
  var CSRF = document.querySelector('meta[name="csrf"]').content;
  var POLL_MS = 2500;

  var state = null;
  var offset = 0;              // serverens ur minus vores
  var failures = 0;
  var pollTimer = null;
  var phase = null;
  var signatures = {};         // hvad der er tegnet, saa det ikke tegnes om
  var lastAnnounced = null;

  function $(id) { return document.getElementById(id); }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function serverNow() { return Date.now() + offset; }

  function clock(ms) {
    var total = Math.max(0, Math.ceil(ms / 1000));
    var h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
    var mm = (h ? (m < 10 ? "0" : "") : "") + m;
    return (h ? h + ":" : "") + mm + ":" + (s < 10 ? "0" : "") + s;
  }

  function length(seconds) {
    if (!seconds) return "";
    return clock(seconds * 1000);
  }

  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }

  /* "Mads Hansen" -> "MH", "Lucas" -> "L". */
  function initials(name) {
    var parts = name.trim().split(/\s+/).filter(Boolean);
    var letters = parts.slice(0, 2).map(function (w) { return Array.from(w)[0]; });
    return letters.join("").toUpperCase() || "?";
  }

  function timeOfDay(ms) {
    var d = new Date(ms);                    // et tidspunkt, ikke en varighed
    return d.toLocaleTimeString("da-DK", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function teamLabel(team, solo, members) {
    if (solo) return members && members.length ? members[0] : "Solo";
    return "Hold " + team.number;
  }

  /* ---------- Polling ---------- */

  function poll() {
    clearTimeout(pollTimer);
    fetch(STATE_URL, { credentials: "same-origin", cache: "no-store",
                       headers: { "Accept": "application/json" } })
      .then(function (res) {
        return res.json().then(function (data) { return { res: res, data: data }; },
                               function () { return { res: res, data: null }; });
      })
      .then(function (r) {
        if (r.res.status === 403 || r.res.status === 404) {
          // Smidt ud, eller lobbyen er slettet.
          location.href = "/";
          return;
        }
        if (!r.res.ok || !r.data) throw new Error("state " + r.res.status);
        failures = 0;
        apply(r.data);
      })
      .catch(function () {
        failures += 1;
        $("phase-pill").textContent = "Mister forbindelsen … prøver igen";
        $("phase-pill").classList.add("is-bad");
      })
      .then(schedule);
  }

  function schedule() {
    clearTimeout(pollTimer);
    var delay = POLL_MS;
    if (document.hidden) delay = 15000;
    else if (waitingForServer()) delay = 1000;
    else if (phase === "spin") delay = 1000;     // alle skal se hjulet dreje paa samme tid
    if (failures) delay = Math.min(15000, POLL_MS * (1 + failures));
    pollTimer = setTimeout(poll, delay);
  }

  /* Uret her er naaet nul, men serveren har ikke skiftet fase endnu. */
  function waitingForServer() {
    if (!state || !state.round) return false;
    var t = serverNow();
    if (phase === "reveal") return t >= state.round.make_at - 500;
    if (phase === "make") return t >= state.round.deadline - 1000;
    if (phase === "spin" && state.spin) return true;
    return false;
  }

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) poll();
  });

  /* ---------- Faser ---------- */

  var PILLS = {
    lobby: "Lobby · venter på spillere",
    spin: "Genrehjulet",
    reveal: "Jeres genre og beat",
    make: "Runden er i gang",
    vote: "Afstemning",
    result: "Resultat",
    closed: "Udløbet"
  };

  function apply(data) {
    offset = data.now - Date.now();

    // Ejerskabet er gaaet videre. Ejerknapperne tegnes af serveren.
    if (data.me.owner !== WAS_OWNER) {
      location.reload();
      return;
    }

    state = data;
    if (data.phase !== phase) {
      var first = phase === null;
      phase = data.phase;
      document.querySelectorAll(".phase").forEach(function (node) {
        node.hidden = node.dataset.phase !== phase;
      });
      signatures = {};
      document.body.dataset.phase = phase;
      // Reveal er en cutscene: sorte bjaelker over og under.
      document.body.classList.toggle("is-letterboxed", phase === "reveal");
      captionFor(phase, data, first);
      if (!first) {
        // Ny fase: flyt fokus til den, saa en skaermlaeser hoerer det.
        var heading = document.querySelector('.phase[data-phase="' + phase + '"] h1');
        if (heading) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); }
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
    }
    noteArrivals(data);

    var pill = $("phase-pill");
    pill.textContent = PILLS[phase] || "";
    pill.className = "phase-pill phase-pill--" + phase;

    renderPlayers(data);
    if (phase === "lobby") renderLobby(data);
    if (phase === "spin") renderSpin(data);
    if (phase === "reveal") renderReveal(data);
    if (phase === "make") renderMake(data);
    if (phase === "vote") renderVote(data);
    if (phase === "result") renderResult(data);
    tick();
  }

  function changed(key, value) {
    var sig = JSON.stringify(value);
    if (signatures[key] === sig) return false;
    signatures[key] = sig;
    return true;
  }

  /* ---------- Spillerne ---------- */

  var seenPlayers = null;       // hvem der er tegnet foer - til "glider ind"

  function renderPlayers(data) {
    var rows = data.players.map(function (p) {
      return [p.id, p.name, p.owner, p.me, p.team];
    });
    $("player-count").textContent = plural(data.players.length, "spiller", "spillere");
    if (!changed("players", [rows, data.phase, data.me.owner])) return;

    var list = $("player-list");
    list.textContent = "";
    var firstDraw = seenPlayers === null;
    seenPlayers = seenPlayers || {};
    data.players.forEach(function (p) {
      var li = el("li", "crew__item" + (!firstDraw && !seenPlayers[p.id] ? " is-new" : ""));
      seenPlayers[p.id] = true;
      var face = el("span", "avatar" + (p.owner ? " avatar--owner" : ""), initials(p.name));
      face.setAttribute("aria-hidden", "true");
      li.appendChild(face);
      var who = el("span", "crew__who");
      who.appendChild(el("span", "crew__name", p.name));
      var meta = [];
      if (p.owner) meta.push("ejer");
      if (p.me) meta.push("dig");
      if (p.team && !(data.round && data.round.solo)) meta.push("hold " + p.team);
      if (meta.length) who.appendChild(el("span", "crew__meta" + (p.owner ? " tag--fire" : ""), meta.join(" · ")));
      li.appendChild(who);

      if (data.me.owner && data.phase === "lobby" && !p.me) {
        var form = el("form", "crew__kick");
        form.method = "post";
        form.action = KICK_URL;
        form.dataset.confirm = "Fjern " + p.name + " fra lobbyen?";
        var csrf = el("input"); csrf.type = "hidden"; csrf.name = "csrf"; csrf.value = CSRF;
        var who = el("input"); who.type = "hidden"; who.name = "player"; who.value = String(p.id);
        var btn = el("button", "btn btn--quiet", "Fjern");
        btn.type = "submit";
        btn.setAttribute("aria-label", "Fjern " + p.name);
        form.appendChild(csrf); form.appendChild(who); form.appendChild(btn);
        li.appendChild(form);
      }
      list.appendChild(li);
    });

    // Under og efter en runde kan man ikke forlade lobbyen.
    $("leave-form").hidden = ["spin", "reveal", "make", "vote"].indexOf(data.phase) !== -1;
  }

  /* ---------- 1. Lobby ---------- */

  function renderLobby(data) {
    var problem = $("start-problem");
    if (problem) {
      problem.hidden = !data.start_problem;
      problem.textContent = data.start_problem || "";
      $("start-btn").disabled = !!data.start_problem;
    }
    var waiting = $("waiting-line");
    if (waiting) {
      var owner = data.players.filter(function (p) { return p.owner; })[0];
      waiting.textContent = "Venter på at " + (owner ? owner.name : "ejeren") + " starter runden.";
    }
  }

  /* ---------- Hold-kortet ---------- */

  function teamCard(node, round) {
    var mine = round.teams.filter(function (t) { return t.mine; })[0];
    if (!changed("team-" + node.id, mine)) return;
    node.textContent = "";
    if (!mine) return;
    if (round.solo) {
      node.appendChild(el("p", "teamcard__head", "Du spiller solo"));
      return;
    }
    node.appendChild(el("p", "teamcard__head", "Dit hold · Hold " + mine.number));
    var ul = el("ul", "teamcard__list");
    mine.members.forEach(function (name) { ul.appendChild(el("li", "", name)); });
    node.appendChild(ul);
  }

  /* ---------- 2. Hjulet ----------
     Serveren har allerede trukket genren, naar hjulet drejer - hjulet viser
     bare hvor den lander.

     Hjulets vinkel er en ren funktion af serverens ur: hvert spin starter
     hvor det forrige landede og lander paa sin genre efter TURNS hele
     omgange. Slutvinklen regnes ud fra genren og tidspunktet for spinnet,
     saa alle klienter - ogsaa en der aabner siden midt i et spin - ser
     hjulet staa samme sted paa samme tid.

     Det drejes med requestAnimationFrame, ikke med en CSS-transition: det
     er selve spillet, ikke pynt, saa det drejer ogsaa ved reduceret
     bevaegelse. Pynten (lamper der blinker i hvile, spotlyset) respekterer
     den stadig. */

  var SVGNS = "http://www.w3.org/2000/svg";
  var TURNS = 7;               // hele omgange pr. spin
  var WIND_MS = 380;           // hjulet traekkes lidt tilbage foer det slippes
  var WIND_DEG = 9;
  var BULBS = 36;
  var wheel = { names: null, landed: null, raf: 0, peg: null, flapAt: -1e9, bulbs: [],
                shown: null, wonUntil: 0 };

  function svg(tag, attrs, parent) {
    var node = document.createElementNS(SVGNS, tag);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    if (parent) parent.appendChild(node);
    return node;
  }

  // Vinkel i grader med uret fra toppen -> punkt paa cirklen.
  function xy(deg, r) {
    var a = deg * Math.PI / 180;
    return [(r * Math.sin(a)).toFixed(2), (-r * Math.cos(a)).toFixed(2)];
  }
  function point(deg, r) { return xy(deg, r).join(" "); }

  function shortName(name) {
    var chars = Array.from(name);
    return chars.length > 12 ? chars.slice(0, 11).join("") + "…" : name;
  }

  function gradient(defs, kind, id, attrs, stops) {
    var g = svg(kind, Object.assign({ id: id }, attrs), defs);
    stops.forEach(function (s) { svg("stop", { offset: s[0], "stop-color": s[1], "stop-opacity": s[2] === undefined ? 1 : s[2] }, g); });
    return g;
  }

  function drawWheel(names) {
    var disc = $("wheel-disc");
    disc.textContent = "";
    var n = names.length, step = 360 / n, R = 94;

    var defs = svg("defs", {}, disc);
    var user = { gradientUnits: "userSpaceOnUse", cx: 0, cy: 0, r: R };
    gradient(defs, "radialGradient", "sw-y", user,
             [[0, "#fff6c2"], [0.35, "#fbe36a"], [0.75, "#f4d43a"], [1, "#d6b01b"]]);
    gradient(defs, "radialGradient", "sw-b", user,
             [[0, "#3a3936"], [0.5, "#1b1b1a"], [1, "#070707"]]);
    gradient(defs, "radialGradient", "sw-g", user,
             [[0, "#4a463e"], [1, "#1c1a16"]]);
    gradient(defs, "linearGradient", "sw-rim", { x1: 0, y1: 0, x2: 1, y2: 1 },
             [[0, "#fff3a8"], [0.22, "#d7ad2c"], [0.45, "#6e5410"], [0.62, "#f0d061"],
              [0.8, "#a07c16"], [1, "#fbe792"]]);
    gradient(defs, "linearGradient", "sw-gloss", { x1: 0, y1: 0, x2: 0, y2: 1 },
             [[0, "#ffffff", 0.34], [1, "#ffffff", 0]]);
    gradient(defs, "radialGradient", "sw-hubg", { cx: 0.38, cy: 0.32, r: 0.75 },
             [[0, "#fff3a8"], [0.45, "#d9b12c"], [1, "#6e5410"]]);

    // Skygge under hjulet.
    svg("circle", { r: 108, cy: 4, "class": "wheel__shadow" }, disc);

    // Det der drejer.
    var rot = svg("g", { id: "wheel-rot" }, disc);
    names.forEach(function (name, i) {
      // Sort og gul paa skift. Et ulige antal ville give to ens ved siden af
      // hinanden, saa den sidste bliver graa.
      var tone = n > 1 && n % 2 === 1 && i === n - 1 ? "g" : (i % 2 ? "b" : "y");
      var g = svg("g", { "class": "slice slice--" + tone }, rot);
      g.dataset.genre = name;
      if (n === 1) svg("circle", { r: R, "class": "slice__face" }, g);
      else svg("path", {
        "class": "slice__face",
        d: "M0 0L" + point(i * step, R) + "A" + R + " " + R + " 0 " + (step > 180 ? 1 : 0) +
           " 1 " + point((i + 1) * step, R) + "Z"
      }, g);
      // En tynd lysere kant langs yderkanten af skiven.
      if (n > 1) svg("path", {
        "class": "slice__edge",
        d: "M" + point(i * step + 0.6, R - 3.5) + "A" + (R - 3.5) + " " + (R - 3.5) + " 0 0 1 " +
           point((i + 1) * step - 0.6, R - 3.5)
      }, g);
      // Navnet staar langs radien, fra pindene og ind mod navet. Skriften
      // skal baade kunne vaere i skivens bredde og i plads mellem pindene
      // (r 84) og ringen ved navet (r 34), saa lange navne bliver mindre.
      var mid = (i + 0.5) * step;
      var text = shortName(name).toUpperCase();
      var byArc = 60 * step * Math.PI / 180 * 0.5;
      var byLength = 48 / (Array.from(text).length * 0.72);
      var size = Math.max(5, Math.min(11, byArc, byLength));
      var label = svg("text", {
        "class": "slice__label",
        transform: "rotate(" + (mid - 90).toFixed(2) + ") translate(83 0)",
        "text-anchor": "end", "dominant-baseline": "central",
        "font-size": size.toFixed(1)
      }, g);
      label.textContent = text;
    });
    // Skillelinjer og pinde mellem skiverne.
    if (n > 1) names.forEach(function (_n, i) {
      var a = xy(i * step, 23), b = xy(i * step, R);
      svg("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], "class": "wheel__divider" }, rot);
      var pin = xy(i * step, R - 7);
      svg("circle", { cx: pin[0], cy: pin[1], r: 2.3, "class": "wheel__peg" }, rot);
      svg("circle", { cx: (pin[0] - 0.6).toFixed(2), cy: (pin[1] - 0.6).toFixed(2), r: 0.8, "class": "wheel__peg-shine" }, rot);
    });
    // Ringene inde ved navet.
    svg("circle", { r: 31, "class": "wheel__ring" }, rot);
    svg("circle", { r: 27, "class": "wheel__ring wheel__ring--dark" }, rot);

    // Det der staar stille: kanten med lamper, glansen og navet.
    svg("circle", { r: R + 0.5, "class": "wheel__lip" }, disc);
    svg("circle", { r: 102, "class": "wheel__rim" }, disc);
    svg("circle", { r: 108.5, "class": "wheel__lip" }, disc);
    wheel.bulbs = [];
    for (var k = 0; k < BULBS; k++) {
      var at = xy(k * 360 / BULBS + 360 / BULBS / 2, 102);
      svg("circle", { cx: at[0], cy: at[1], r: 3.1, "class": "wheel__socket" }, disc);
      var bulb = svg("circle", { cx: at[0], cy: at[1], r: 2.2,
                                 "class": "wheel__bulb" + (k % 2 ? " is-odd" : "") }, disc);
      wheel.bulbs.push(bulb);
    }
    svg("path", { d: "M-88 -6A88 88 0 0 1 88 -6C50 -30 -50 -30 -88 -6Z", "class": "wheel__gloss" }, disc);
    svg("circle", { r: 22, "class": "wheel__hubring" }, disc);
    wheel.peg = null;
  }

  // Et tal mellem 0 og 1, ens hos alle, ud fra spinnets tidspunkt.
  function jitter(seed) {
    var x = Math.sin(seed / 997) * 10000;
    return x - Math.floor(x);
  }

  function spinsOf(s) {
    return s.order.filter(function (o) { return o.spun_at; })
      .sort(function (a, b) { return a.spun_at - b.spun_at; });
  }

  // Hvert spin: fra hvor det forrige landede, til sin genre + TURNS omgange.
  function plan(s) {
    var at = 0, step = 360 / wheel.names.length;
    return spinsOf(s).map(function (o) {
      var i = Math.max(0, wheel.names.indexOf(o.genre));
      var target = -(i + 0.5) * step + (jitter(o.spun_at) - 0.5) * step * 0.72;
      var delta = ((target - at) % 360 + 360) % 360;
      var p = { o: o, from: at, to: at + TURNS * 360 + delta };
      at = p.to;
      return p;
    });
  }

  // Hurtigt ud af start, langsomt ind til sidst - som et rigtigt hjul.
  function ease(p) { return 1 - Math.pow(1 - p, 3.6); }

  function angleAt(plans, t, ms) {
    var cur = null;
    plans.forEach(function (p) { if (p.o.spun_at <= t) cur = p; });
    if (!cur) return { a: 0, moving: false, plan: null };
    var e = t - cur.o.spun_at;
    if (e >= ms) return { a: cur.to, moving: false, plan: cur };
    if (e < WIND_MS) {
      return { a: cur.from - WIND_DEG * Math.sin(e / WIND_MS * Math.PI / 2), moving: true, plan: cur };
    }
    var p = (e - WIND_MS) / (ms - WIND_MS);
    return { a: cur.from - WIND_DEG + (cur.to - cur.from + WIND_DEG) * ease(p), moving: true, plan: cur };
  }

  function frame() {
    wheel.raf = 0;
    if (phase !== "spin" || !state || !state.spin || !wheel.names) return;
    var s = state.spin, t = serverNow(), clock = performance.now();
    var r = angleAt(plan(s), t, s.spin_ms);
    var rot = $("wheel-rot");
    if (rot) rot.setAttribute("transform", "rotate(" + r.a.toFixed(3) + ")");

    // Viseren slaar mod en pind hver gang en skillelinje passerer toppen.
    var step = 360 / wheel.names.length;
    var peg = Math.floor((r.a + 0.0001) / step);
    if (wheel.peg !== null && peg !== wheel.peg && r.moving) wheel.flapAt = clock;
    wheel.peg = peg;
    var since = clock - wheel.flapAt;
    var flap = since < 500 ? -24 * Math.exp(-since / 75) * Math.cos(since / 34) : 0;
    $("wheel-pointer").style.setProperty("--flap", flap.toFixed(2) + "deg");

    // Lamperne loeber rundt, mens det drejer - hurtigere jo hurtigere det gaar.
    var box = $("wheel");
    box.classList.toggle("is-spinning", r.moving);
    box.classList.toggle("is-won", t < wheel.wonUntil);
    if (r.moving) {
      var chase = Math.floor(t / 65);
      wheel.bulbs.forEach(function (b, k) { b.classList.toggle("is-lit", (k + chase) % 4 === 0); });
    } else {
      wheel.bulbs.forEach(function (b) { b.classList.remove("is-lit"); });
    }
    if (r.moving || flap !== 0) wheel.raf = requestAnimationFrame(frame);
  }

  function kick() { if (!wheel.raf) wheel.raf = requestAnimationFrame(frame); }

  function genitive(name) { return /[sxz]$/i.test(name) ? name + "'" : name + "s"; }

  function turnName(o, solo) {
    if (solo) return o.members[0] || "Nogen";
    return "Hold " + o.number;
  }

  function renderSpin(data) {
    var s = data.spin, solo = data.round.solo;

    if (changed("wheel", s.wheel)) {
      wheel.names = s.wheel;
      drawWheel(s.wheel);
    }
    if (wheel.landed === null) {
      // Det der allerede var landet, da siden blev aabnet, siges ikke hoejt.
      wheel.landed = {};
      s.order.forEach(function (o) {
        if (o.spun_at && serverNow() >= o.spun_at + s.spin_ms) wheel.landed[o.spun_at] = true;
      });
    }

    // Raekkefoelgen. Bygges kun om, naar den skifter.
    var list = $("spin-order");
    if (changed("order", s.order.map(function (o) { return [o.number, o.members, o.mine]; }))) {
      list.textContent = "";
      s.order.forEach(function (o, i) {
        var li = el("li", "turn" + (o.mine ? " is-mine" : ""));
        li.dataset.n = String(o.number);
        li.appendChild(el("span", "turn__n", String(i + 1)));
        var who = el("span", "turn__who");
        who.appendChild(el("span", "turn__name", turnName(o, solo) + (o.mine ? (solo ? " · dig" : " · jer") : "")));
        if (!solo) who.appendChild(el("span", "turn__members", o.members.join(", ")));
        li.appendChild(who);
        li.appendChild(el("span", "turn__genre", ""));
        list.appendChild(li);
      });
    }
    tickSpin();
  }

  function setText(node, text) { if (node.textContent !== text) node.textContent = text; }

  /* Det der afhaenger af tiden: hvornaar et hjul er landet, hvem der har
     tur, og hvor laenge til serveren drejer selv. Kaldes hvert kvarte
     sekund - selve drejningen tegnes i frame(). */
  function tickSpin() {
    var s = state.spin, solo = state.round.solo, t = serverNow();
    var spins = spinsOf(s);
    var last = spins[spins.length - 1] || null;
    var spinning = last && t < last.spun_at + s.spin_ms;
    kick();

    s.order.forEach(function (o) {
      var li = $("spin-order").querySelector('[data-n="' + o.number + '"]');
      var landed = o.spun_at && t >= o.spun_at + s.spin_ms;
      var slice = o.genre ? $("wheel-disc").querySelector('.slice[data-genre="' + CSS.escape(o.genre) + '"]') : null;
      if (landed && !wheel.landed[o.spun_at]) {
        wheel.landed[o.spun_at] = true;
        wheel.wonUntil = t + 1800;
        if (slice) slice.classList.add("is-hit");
        var who = o.mine ? (solo ? "du" : "i") : turnName(o, solo).toLowerCase();
        say(who + " fik " + o.genre.toLowerCase() + (o.auto ? " — hjulet drejede selv." : "."));
        kick();
      }
      if (landed && slice) slice.classList.add("is-taken");
      if (!li) return;
      var now = !spinning && o.number === s.current;
      li.classList.toggle("is-now", now || (spinning && o === last));
      li.classList.toggle("is-done", !!landed);
      setText(li.querySelector(".turn__genre"),
        landed ? o.genre : (spinning && o === last ? "drejer …" : (now ? "har tur" : "")));
    });

    // Resultatet under hjulet: det seneste spin, naar hjulet er landet.
    var result = $("spin-result");
    var showing = last && !spinning ? last.spun_at : null;
    if (showing !== wheel.shown) {
      wheel.shown = showing;
      result.classList.remove("is-on");
      if (showing) {
        setText($("spin-who"), (last.mine ? (solo ? "Du" : "I") : turnName(last, solo)) + " fik");
        if (window.SW) { delete $("spin-genre").dataset.text; window.SW.waveText($("spin-genre"), last.genre, false); }
        else setText($("spin-genre"), last.genre);
        void result.offsetWidth;                // start animationen forfra
        result.classList.add("is-on");
      }
    }

    var btn = $("spin-btn"), nowLine = $("spin-now"), auto = $("spin-auto");
    var cur = s.order.filter(function (o) { return o.number === s.current; })[0];
    btn.hidden = !s.my_turn;
    if (spinning) {
      setText(nowLine, last.mine ? (solo ? "Du drejer hjulet …" : "I drejer hjulet …")
                                 : turnName(last, solo) + " drejer hjulet …");
      setText(auto, "");
      btn.disabled = true;
      return;
    }
    if (!cur) {
      setText(nowLine, "Alle har fået en genre.");
      setText(auto, "");
      return;
    }
    var waiting = t < s.turn_at;
    var leftMs = s.turn_at + s.turn_seconds * 1000 - t;
    if (s.my_turn) setText(nowLine, solo ? "Din tur — spin hjulet." : "Jeres tur — spin hjulet.");
    else setText(nowLine, "Det er " + genitive(turnName(cur, solo)) + " tur.");
    btn.disabled = waiting || spinBusy;
    setText(auto, waiting ? "" : leftMs > 0
      ? "Hjulet drejer selv om " + Math.ceil(leftMs / 1000) + " s"
      : "Hjulet drejer selv …");
  }

  var spinBusy = false;
  $("spin-btn").addEventListener("click", function () {
    if (!state || !state.spin || spinBusy) return;
    spinBusy = true;
    $("spin-btn").disabled = true;
    fetch(state.spin.spin_url, {
      method: "POST", credentials: "same-origin",
      headers: { "Accept": "application/json", "X-CSRF-Token": CSRF, "X-Requested-With": "fetch" }
    }).then(function (res) {
      return res.json().then(function (d) { return { ok: res.ok, d: d }; },
                             function () { return { ok: false, d: null }; });
    }).then(function (r) {
      if (!r.ok) setText($("spin-auto"), (r.d && r.d.error) || "Hjulet ville ikke dreje. Prøv igen.");
    }).catch(function () {
      setText($("spin-auto"), "Forbindelsen svigtede. Prøv igen.");
    }).then(function () {
      spinBusy = false;
      poll();
    });
  });

  /* ---------- 3. Reveal ---------- */

  function renderReveal(data) {
    $("reveal-label").textContent = data.round.solo ? "Din genre er" : "Jeres genre er";
    // Genren popper ind bogstav for bogstav og bliver ved med at boelge.
    if (window.SW) window.SW.waveText($("reveal-genre"), data.round.genre, true);
    else $("reveal-genre").textContent = data.round.genre;
    $("reveal-beat").textContent = data.round.beat;
    teamCard($("reveal-team"), data.round);
  }

  /* ---------- 3. Lav ---------- */

  function setSrc(audio, url) {
    if (audio.dataset.src !== url) {
      audio.dataset.src = url || "";
      if (url) audio.src = url; else audio.removeAttribute("src");
    }
  }

  function renderMake(data) {
    var round = data.round, make = data.make;
    $("make-genre").textContent = round.genre;
    $("make-beat").textContent = round.beat;
    if (round.beat_url) {
      setSrc($("beat-audio"), round.beat_url);
      $("beat-dl").href = round.beat_url + "?hent=1";
    }
    teamCard($("make-team"), round);

    var mine = make.mine;
    $("my-sub").hidden = !mine;
    if (mine) {
      $("my-sub-text").textContent = "Afleveret af " + (mine.by || "holdet") + " kl. " +
        timeOfDay(mine.at) + " · " + length(mine.seconds);
      setSrc($("my-sub-audio"), mine.url);
      $("drop-main").textContent = "Vælg en ny version";
      $("upload-btn").textContent = "Erstat afleveringen";
    }

    var delivered = $("delivered");
    if (changed("delivered", [make.delivered, make.teams])) {
      delivered.textContent = "";
      delivered.appendChild(document.createTextNode(
        make.delivered.length + " af " + plural(make.teams, "hold", "hold") + " har afleveret"));
      if (!round.solo && make.delivered.length) {
        delivered.appendChild(document.createTextNode(": "));
        make.delivered.forEach(function (n, i) {
          delivered.appendChild(el("span", "chip", "Hold " + n));
          if (i < make.delivered.length - 1) delivered.appendChild(document.createTextNode(" "));
        });
      }
    }
  }

  /* Upload med fremdrift. fetch kan ikke melde hvor langt den er. */
  var uploadForm = $("upload-form");
  var trackInput = $("track-input");

  trackInput.addEventListener("change", function () {
    var file = trackInput.files[0];
    $("drop-main").textContent = file ? file.name : "Vælg dit nummer";
    $("upload-msg").textContent = "";
    $("upload-msg").className = "upload__msg";
  });

  uploadForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var file = trackInput.files[0];
    var msg = $("upload-msg");
    if (!file) return;
    if (state && state.make && file.size > state.make.max_bytes) {
      msg.textContent = "Filen er over " + Math.round(state.make.max_bytes / 1048576) + " MB.";
      msg.className = "upload__msg is-bad";
      return;
    }

    var body = new FormData(uploadForm);
    var xhr = new XMLHttpRequest();
    var meter = $("upload-meter"), fill = meter.firstElementChild, btn = $("upload-btn");
    xhr.open("POST", uploadForm.action);
    xhr.setRequestHeader("X-CSRF-Token", CSRF);
    xhr.setRequestHeader("X-Requested-With", "fetch");
    xhr.setRequestHeader("Accept", "application/json");

    meter.hidden = false;
    fill.style.width = "0%";
    btn.disabled = true;
    msg.className = "upload__msg";
    msg.textContent = "Sender …";

    xhr.upload.onprogress = function (e) {
      if (!e.lengthComputable) return;
      var pct = Math.round((e.loaded / e.total) * 100);
      fill.style.width = pct + "%";
      msg.textContent = pct < 100 ? "Sender … " + pct + " %" : "Tjekker filen …";
    };
    xhr.onload = function () {
      var data = null;
      try { data = JSON.parse(xhr.responseText); } catch (e) {}
      btn.disabled = false;
      meter.hidden = true;
      if (xhr.status === 200 && data && data.ok) {
        msg.textContent = "Afleveret.";
        msg.className = "upload__msg is-good";
        uploadForm.reset();
        $("drop-main").textContent = "Vælg en ny version";
        poll();
      } else {
        msg.textContent = (data && data.error) ||
          (xhr.status === 413 ? "Filen er for stor." : "Det gik ikke (" + xhr.status + "). Prøv igen.");
        msg.className = "upload__msg is-bad";
      }
    };
    xhr.onerror = function () {
      btn.disabled = false;
      meter.hidden = true;
      msg.textContent = "Forbindelsen blev afbrudt. Prøv igen.";
      msg.className = "upload__msg is-bad";
    };
    xhr.send(body);
  });

  /* ---------- 4. Afstemning ---------- */

  var pending = {};            // stemmer der er paa vej til serveren

  function flameRow(track) {
    var group = el("div", "rate");
    group.setAttribute("role", "radiogroup");
    group.setAttribute("aria-label", "Flammer til " + track.label);
    for (var n = 1; n <= 5; n++) {
      var b = el("button", "rate__btn");
      b.type = "button";
      b.dataset.flames = String(n);
      b.setAttribute("role", "radio");
      b.setAttribute("aria-label", plural(n, "flamme", "flammer"));
      b.appendChild(el("span", "flame", ""));
      group.appendChild(b);
    }
    group.addEventListener("click", function (event) {
      var b = event.target.closest(".rate__btn");
      if (b) cast(track.id, parseInt(b.dataset.flames, 10), group);
    });
    return group;
  }

  function paintRate(group, flames) {
    group.querySelectorAll(".rate__btn").forEach(function (b) {
      var n = parseInt(b.dataset.flames, 10);
      b.classList.toggle("is-lit", !!flames && n <= flames);
      b.setAttribute("aria-checked", flames === n ? "true" : "false");
    });
  }

  function cast(id, flames, group) {
    var before = pending[id] !== undefined ? pending[id] : currentVote(id);
    pending[id] = flames;
    paintRate(group, flames);
    var status = group.parentNode.querySelector(".track__status");
    status.textContent = "Gemmer …";
    fetch(state.vote.vote_url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "Accept": "application/json",
                 "X-CSRF-Token": CSRF, "X-Requested-With": "fetch" },
      body: JSON.stringify({ track: id, flames: flames })
    }).then(function (res) {
      return res.json().then(function (d) { return { ok: res.ok, d: d }; },
                             function () { return { ok: false, d: null }; });
    }).then(function (r) {
      if (!r.ok) throw new Error((r.d && r.d.error) || "Stemmen blev ikke gemt.");
      status.textContent = plural(flames, "flamme", "flammer") + " · gemt";
      delete pending[id];
      poll();
    }).catch(function (err) {
      delete pending[id];
      paintRate(group, before);
      status.textContent = err.message || "Stemmen blev ikke gemt. Prøv igen.";
    });
  }

  function currentVote(id) {
    var t = state.vote.tracks.filter(function (x) { return x.id === id; })[0];
    return t ? t.flames : null;
  }

  function renderVote(data) {
    var v = data.vote;
    var line = v.my_need
      ? "Du har stemt på " + v.my_have + " af " + v.my_need + " · "
      : "Der er ikke noget for dig at stemme på. ";
    $("vote-progress").textContent = line + v.done_players + " af " +
      plural(v.voters, "spiller", "spillere") + " er færdige";

    var list = $("vote-tracks");
    // Listen bygges en gang pr. afstemning. Bygges den om, stopper den
    // lyd der spiller.
    if (changed("tracks", v.tracks.map(function (t) { return [t.id, t.mine]; }))) {
      list.textContent = "";
      v.tracks.forEach(function (t) {
        var li = el("li", "track" + (t.mine ? " track--mine" : ""));
        li.dataset.id = t.id;
        var head = el("div", "track__head");
        head.appendChild(el("span", "track__label", t.label));
        head.appendChild(el("span", "track__len", length(t.seconds)));
        li.appendChild(head);
        var a = el("audio", "player");
        a.controls = true; a.preload = "none"; a.src = t.url;
        li.appendChild(a);
        if (!t.mine) {
          li.appendChild(flameRow(t));
          li.appendChild(el("p", "track__status", ""));
        }
        list.appendChild(li);
      });
    }

    v.tracks.forEach(function (t) {
      if (t.mine) return;
      var li = list.querySelector('[data-id="' + t.id + '"]');
      if (!li) return;
      var flames = pending[t.id] !== undefined ? pending[t.id] : t.flames;
      paintRate(li.querySelector(".rate"), flames);
      var status = li.querySelector(".track__status");
      if (pending[t.id] === undefined) {
        status.textContent = t.flames ? plural(t.flames, "flamme", "flammer") : "Ikke stemt endnu";
      }
      li.classList.toggle("is-voted", !!t.flames);
    });
  }

  /* ---------- 5. Resultat ---------- */

  function flameMeter(average) {
    var wrap = el("span", "meter-flames");
    wrap.setAttribute("aria-hidden", "true");
    for (var i = 0; i < 5; i++) {
      var f = el("span", "flame flame--meter");
      var fill = average === null ? 0 : Math.max(0, Math.min(1, average - i));
      // CSSOM er tilladt under CSP'en - det er style="" i HTML'en der ikke er.
      f.style.setProperty("--fill", (fill * 100).toFixed(0) + "%");
      wrap.appendChild(f);
    }
    return wrap;
  }

  function renderResult(data) {
    var r = data.result, round = data.round;
    $("result-beat").textContent = round.genre
      ? (round.solo ? "Din genre: " : "Jeres genre: ") + round.genre + " · " + round.beat : "";
    if (!changed("result", r)) return;

    var list = $("ranking");
    list.textContent = "";
    if (!r.ranking.length) {
      list.appendChild(el("li", "empty", "Ingen afleverede et nummer i den her runde."));
    }
    r.ranking.forEach(function (row) {
      var li = el("li", "rank" + (row.place && row.place <= 3 ? " rank--" + row.place : "") +
                        (row.mine ? " rank--mine" : ""));
      li.appendChild(el("span", "rank__place", row.place ? String(row.place) : "–"));

      var body = el("div", "rank__body");
      var who = round.solo ? (row.members[0] || "Ukendt") : "Hold " + row.team;
      var name = el("p", "rank__who", who);
      body.appendChild(name);
      if (!round.solo) body.appendChild(el("p", "rank__members", row.members.join(", ")));

      var score = el("div", "rank__score");
      score.appendChild(flameMeter(row.average));
      score.appendChild(el("span", "rank__avg",
        row.average === null ? "Ingen stemmer"
          : row.average.toFixed(1).replace(".", ",") + " · " + plural(row.votes, "stemme", "stemmer")));
      body.appendChild(score);

      var a = el("audio", "player");
      a.controls = true; a.preload = "none"; a.src = row.url;
      body.appendChild(a);
      var meta = [row.genre, row.label, length(row.seconds)];
      if (!round.solo && row.by) meta.push("afleveret af " + row.by);
      body.appendChild(el("p", "rank__meta", meta.filter(Boolean).join(" · ")));
      li.appendChild(body);
      list.appendChild(li);
    });

    var missing = $("missing");
    missing.hidden = !r.missing.length;
    missing.textContent = "";
    if (r.missing.length) {
      missing.appendChild(el("p", "missing__head", "Afleverede ikke"));
      var ul = el("ul", "missing__list");
      r.missing.forEach(function (m) {
        ul.appendChild(el("li", "", round.solo ? (m.members[0] || "Ukendt")
                                               : "Hold " + m.team + " — " + m.members.join(", ")));
      });
      missing.appendChild(ul);
    }
  }

  /* ---------- Undertekster ----------
     Det der sker, siges som en linje nederst i billedet. Smaa bogstaver,
     som i en cutscene. Kun til det der er nyt - ikke ved hver poll. */

  function say(text) { if (window.SW) window.SW.say(text); }

  function captionFor(p, data, first) {
    if (p === "lobby") say(first ? "venter på de andre …" : "");
    if (p === "spin" && !first) say("hjulet bestemmer. alle får hver sin genre.");
    if (p === "reveal") say((data.round.solo ? "din" : "jeres") + " genre er … " + data.round.genre.toLowerCase() + ".");
    if (p === "make" && !first) say("tiden går. hent beatet.");
    if (p === "vote") say(first ? "lyt med. giv flammer." : "tiden er gået. nu skal der stemmes.");
    if (p === "result") {
      var top = data.result.ranking[0];
      if (!top || top.average === null) say("ingen fik stemmer.");
      else say((data.round.solo ? (top.members[0] || "nogen") : "hold " + top.team) + " vandt.");
    }
    if (p === "closed") say("lobbyen er lukket.");
  }

  var known = null;

  function noteArrivals(data) {
    var names = data.players.map(function (p) { return p.name; });
    if (known && phase === "lobby") {
      names.forEach(function (n) {
        if (known.indexOf(n) === -1) say(n.toLowerCase() + " er kommet.");
      });
    }
    known = names;
  }

  /* ---------- Uret ---------- */

  var warned = {};

  function announce(text) {
    if (text !== lastAnnounced) {
      lastAnnounced = text;
      $("timer-live").textContent = text;
    }
  }

  function tick() {
    if (!state || !state.round) return;
    if (phase === "spin" && state.spin) tickSpin();
    var t = serverNow();
    if (phase === "reveal") {
      $("reveal-left").textContent = Math.max(0, Math.ceil((state.round.make_at - t) / 1000)) + " s";
    }
    if (phase === "make") {
      var left = state.round.deadline - t;
      var timer = $("timer");
      $("timer-text").textContent = clock(left);
      timer.classList.toggle("is-hot", left <= 60000 && left > 10000);
      timer.classList.toggle("is-final", left <= 10000 && left > 0);
      timer.classList.toggle("is-over", left <= 0);
      $("timer-label").textContent = left > 0 ? "tilbage" : "tiden er gået";
      var over = left <= 0;
      $("upload-btn").disabled = over;
      $("track-input").disabled = over;
      if (over) {
        $("upload-msg").textContent = "Tiden er gået. Det der er afleveret, er med.";
      }

      // Skaermlaesere faar besked hvert minut og de sidste 30 og 10 sekunder.
      var secs = Math.ceil(left / 1000);
      if (secs === 60 && !warned.minute) { warned.minute = true; say("ét minut tilbage."); }
      if (secs === 10 && !warned.ten) { warned.ten = true; say("ti sekunder."); }
      if (secs > 0 && (secs % 60 === 0 || secs === 30 || secs === 10)) {
        announce(secs >= 60 ? plural(secs / 60, "minut", "minutter") + " tilbage" : secs + " sekunder tilbage");
      } else if (secs <= 0) {
        announce("Tiden er gået.");
      }
    }
  }

  setInterval(tick, 250);
  poll();
})();
