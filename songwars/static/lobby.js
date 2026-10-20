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
    if (failures) delay = Math.min(15000, POLL_MS * (1 + failures));
    pollTimer = setTimeout(poll, delay);
  }

  /* Uret her er naaet nul, men serveren har ikke skiftet fase endnu. */
  function waitingForServer() {
    if (!state || !state.round) return false;
    var t = serverNow();
    if (phase === "reveal") return t >= state.round.make_at - 500;
    if (phase === "make") return t >= state.round.deadline - 1000;
    return false;
  }

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) poll();
  });

  /* ---------- Faser ---------- */

  var PILLS = {
    lobby: "Lobby · venter på spillere",
    reveal: "Trækker genre og beat",
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
      if (!first) {
        // Ny fase: flyt fokus til den, saa en skaermlaeser hoerer det.
        var heading = document.querySelector('.phase[data-phase="' + phase + '"] h1');
        if (heading) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); }
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
    }

    var pill = $("phase-pill");
    pill.textContent = PILLS[phase] || "";
    pill.className = "phase-pill phase-pill--" + phase;

    renderPlayers(data);
    if (phase === "lobby") renderLobby(data);
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

  function renderPlayers(data) {
    var rows = data.players.map(function (p) {
      return [p.id, p.name, p.owner, p.me, p.online, p.team];
    });
    $("player-count").textContent = "(" + data.players.length + ")";
    if (!changed("players", [rows, data.phase, data.me.owner])) return;

    var list = $("player-list");
    list.textContent = "";
    data.players.forEach(function (p) {
      var li = el("li", "crew__item" + (p.online ? "" : " is-away"));
      li.appendChild(el("span", "dot", ""));
      li.lastChild.setAttribute("aria-hidden", "true");
      li.appendChild(el("span", "crew__name", p.name));
      if (p.me) li.appendChild(el("span", "tag", "dig"));
      if (p.owner) li.appendChild(el("span", "tag tag--fire", "ejer"));
      if (p.team && !(data.round && data.round.solo)) li.appendChild(el("span", "tag", "hold " + p.team));
      if (!p.online) li.appendChild(el("span", "sr", "(ikke aktiv)"));

      if (data.me.owner && data.phase === "lobby" && !p.me) {
        var form = el("form", "crew__kick");
        form.method = "post";
        form.action = KICK_URL;
        form.dataset.confirm = "Fjern " + p.name + " fra lobbyen?";
        var csrf = el("input"); csrf.type = "hidden"; csrf.name = "csrf"; csrf.value = CSRF;
        var who = el("input"); who.type = "hidden"; who.name = "player"; who.value = String(p.id);
        var btn = el("button", "btn btn--tiny", "Fjern");
        btn.type = "submit";
        btn.setAttribute("aria-label", "Fjern " + p.name);
        form.appendChild(csrf); form.appendChild(who); form.appendChild(btn);
        li.appendChild(form);
      }
      list.appendChild(li);
    });

    // Under og efter en runde kan man ikke forlade lobbyen.
    $("leave-form").hidden = data.phase === "reveal" || data.phase === "make" || data.phase === "vote";
  }

  /* ---------- 1. Lobby ---------- */

  function renderLobby(data) {
    var s = data.settings;
    $("settings-line").textContent = [
      plural(s.minutes, "minut", "minutter"),
      s.team_size === 1 ? "solo" : "hold på " + s.team_size,
      "genre: " + (s.genre || "tilfældig")
    ].join(" · ");

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

  /* ---------- 2. Reveal ---------- */

  function renderReveal(data) {
    $("reveal-genre").textContent = data.round.genre;
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
      : "";
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
        if (t.mine) {
          li.appendChild(el("p", "track__status", "Jeres eget nummer — I stemmer ikke på det."));
        } else {
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
    $("result-beat").textContent = round.genre + " · " + round.beat;
    if (!changed("result", r)) return;

    var list = $("ranking");
    list.textContent = "";
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
      var meta = [row.label, length(row.seconds)];
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

  /* ---------- Uret ---------- */

  function announce(text) {
    if (text !== lastAnnounced) {
      lastAnnounced = text;
      $("timer-live").textContent = text;
    }
  }

  function tick() {
    if (!state || !state.round) return;
    var t = serverNow();
    if (phase === "reveal") {
      $("reveal-left").textContent = Math.max(0, Math.ceil((state.round.make_at - t) / 1000)) + " s";
    }
    if (phase === "make") {
      var left = state.round.deadline - t;
      var timer = $("timer");
      $("timer-text").textContent = clock(left);
      timer.classList.toggle("is-hot", left <= 60000 && left > 0);
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
