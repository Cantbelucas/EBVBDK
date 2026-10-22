/* Songwars - det der bruges paa alle sider.

   CSP'en tillader ingen inline-scripts, saa der staar ingen onsubmit= eller
   onclick= i HTML'en. I stedet:

     <form data-confirm="Sikker?">   spoerger foer formularen sendes
     <form data-busy="Sender …">     laaser knappen mens den sendes
     <button data-copy="tekst">      kopierer teksten
     <p data-type>                   skrives ud tegn for tegn, som en undertekst

   Scenen i baggrunden (traeet og bladene) bygges ogsaa her. Placering og
   tempo saettes som CSS-variabler via CSSOM - det tillader CSP'en, i
   modsaetning til style="" i HTML'en. */

(function () {
  "use strict";

  var calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------- Indstillinger ----------
     Lyd, animationer og vibration. Gemt i browseren (sw-prefs) - de gaelder
     kun den her browser. Fra en aeldre version: "sw-sound" = "off". prefs.js
     i <head> saetter html[data-motion] foer siden tegnes. */

  var PREFS_KEY = "sw-prefs";
  var prefs = { sound: true, motion: true, vibrate: true };
  try {
    var saved = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
    Object.keys(prefs).forEach(function (k) { if (typeof saved[k] === "boolean") prefs[k] = saved[k]; });
    if (!("sound" in saved) && localStorage.getItem("sw-sound") === "off") prefs.sound = false;
  } catch (e) {}
  var prefListeners = [];

  function setPref(key, value) {
    prefs[key] = value;
    try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch (e) {}
    document.documentElement.dataset.motion = prefs.motion ? "on" : "off";
    prefListeners.forEach(function (fn) { fn(prefs); });
    document.querySelectorAll("[data-prefs]").forEach(renderPrefs);
  }
  document.documentElement.dataset.motion = prefs.motion ? "on" : "off";

  // "Animationer: fra" - intet bevaeger sig, heller ikke hjulet eller teksten.
  function still() { return !prefs.motion; }

  var canVibrate = typeof navigator.vibrate === "function";

  // Telefonen vibrerer kun, naar man har roert siden - foer det afviser
  // browseren det (og skriver en fejl i konsollen).
  function buzz(pattern) {
    var touched = !navigator.userActivation || navigator.userActivation.hasBeenActive;
    if (prefs.vibrate && canVibrate && touched) {
      try { navigator.vibrate(pattern); } catch (e) {}
    }
  }

  var PREF_ROWS = [
    { key: "sound", label: "Lyd", note: "Klik, blip og fanfarer. Aldrig mens der stemmes — så hører man numrene." },
    { key: "motion", label: "Animationer", note: "Fra: intet bevæger sig — hjulet springer direkte til genren, og teksten står der med det samme." },
    { key: "vibrate", label: "Vibration", note: "Når det er din tur, og når tiden er ved at løbe ud.", only: canVibrate }
  ];

  function renderPrefs(box) {
    box.textContent = "";
    PREF_ROWS.forEach(function (row) {
      if (row.only === false) return;
      var line = document.createElement("div");
      line.className = "pref";
      var text = document.createElement("div");
      text.className = "pref__text";
      var label = document.createElement("span");
      label.className = "pref__label";
      label.textContent = row.label;
      label.id = "pref-" + row.key + "-" + Math.random().toString(36).slice(2, 7);
      var note = document.createElement("span");
      note.className = "pref__note";
      note.textContent = row.note;
      text.appendChild(label);
      text.appendChild(note);
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "toggle" + (prefs[row.key] ? " is-on" : "");
      btn.setAttribute("role", "switch");
      btn.setAttribute("aria-checked", prefs[row.key] ? "true" : "false");
      btn.setAttribute("aria-labelledby", label.id);
      btn.dataset.pref = row.key;
      var knob = document.createElement("span");
      knob.className = "toggle__text";
      knob.textContent = prefs[row.key] ? "Til" : "Fra";
      btn.appendChild(knob);
      line.appendChild(text);
      line.appendChild(btn);
      box.appendChild(line);
    });
  }

  document.addEventListener("click", function (event) {
    var btn = event.target.closest && event.target.closest("[data-pref]");
    if (!btn) return;
    var key = btn.dataset.pref;
    setPref(key, !prefs[key]);
    if (key === "sound" && prefs.sound) { sound.unlock(); sound.ok(); }
    if (key === "vibrate" && prefs.vibrate) buzz(40);
    var again = document.querySelector('[data-pref="' + key + '"]');
    if (again && btn.isConnected === false) again.focus();
  });

  document.querySelectorAll("[data-prefs]").forEach(renderPrefs);

  // Tandhjulet i toppen: indstillingerne som et lille panel.
  var prefsBtn = document.getElementById("prefs-btn");
  var prefsPop = document.getElementById("prefs-pop");
  function closePrefs() {
    if (!prefsPop || prefsPop.hidden) return;
    prefsPop.hidden = true;
    prefsBtn.setAttribute("aria-expanded", "false");
  }
  if (prefsBtn && prefsPop) {
    prefsBtn.addEventListener("click", function () {
      var open = prefsPop.hidden;
      prefsPop.hidden = !open;
      prefsBtn.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) { var first = prefsPop.querySelector("button"); if (first) first.focus(); }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !prefsPop.hidden) { e.preventDefault(); closePrefs(); prefsBtn.focus(); }
    });
    document.addEventListener("click", function (e) {
      if (!prefsPop.hidden && !prefsPop.contains(e.target) && !prefsBtn.contains(e.target)) closePrefs();
    });
  }

  /* ---------- Lyd ----------
     Korte, kantede menulyde som paa en PS2. Det hele laves med WebAudio -
     ingen lydfiler. Browseren tillader foerst lyd efter et klik eller et
     tastetryk paa siden. */

  var sound = (function () {
    var ctx = null, out = null, lastTick = 0;

    function unlock() {
      if (!prefs.sound) return;
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      try {
        if (!ctx) {
          ctx = new AC();
          out = ctx.createGain();
          out.gain.value = 0.7;
          out.connect(ctx.destination);
        }
        if (ctx.state === "suspended") ctx.resume();
      } catch (e) { ctx = null; }
    }
    document.addEventListener("pointerdown", unlock, { passive: true });
    document.addEventListener("keydown", unlock);

    function ready() { return prefs.sound && ctx && ctx.state === "running"; }

    function tone(freq, type, at, dur, vol, opts) {
      opts = opts || {};
      var o = ctx.createOscillator(), g = ctx.createGain();
      o.type = type;
      o.frequency.setValueAtTime(freq, at);
      if (opts.to) o.frequency.exponentialRampToValueAtTime(opts.to, at + dur);
      if (opts.detune) o.detune.value = opts.detune;
      var attack = opts.attack || 0.006;
      g.gain.setValueAtTime(0.0001, at);
      g.gain.exponentialRampToValueAtTime(vol, at + attack);
      g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
      o.connect(g);
      g.connect(out);
      o.start(at);
      o.stop(at + dur + 0.05);
    }

    function play(fn) { if (ready()) fn(ctx.currentTime); }

    return {
      unlock: unlock,
      ready: ready,
      // Et klik pr. pind paa hjulet - aldrig taettere end 30 ms.
      tick: function () {
        if (!ready()) return;
        var now = ctx.currentTime;
        if (now - lastTick < 0.03) return;
        lastTick = now;
        tone(1650 + Math.random() * 140, "square", now, 0.024, 0.03);
      },
      release: function () { play(function (n) {
        tone(660, "square", n, 0.06, 0.035);
        tone(990, "square", n + 0.07, 0.08, 0.035);
      }); },
      // Den dybe "boot"-tone: to lave sinustoner, en bloed trekant og en glimmer.
      land: function () { play(function (n) {
        tone(55, "sine", n, 2.4, 0.22, { attack: 0.03 });
        tone(110, "sine", n, 2.0, 0.12, { attack: 0.03 });
        tone(220, "triangle", n, 1.6, 0.05, { attack: 0.05, detune: 7 });
        tone(1320, "sine", n + 0.05, 1.4, 0.022, { attack: 0.08, to: 1760 });
        tone(880, "square", n, 0.09, 0.03);
      }); },
      turn: function () { play(function (n) {
        tone(880, "square", n, 0.07, 0.03);
        tone(1320, "square", n + 0.09, 0.1, 0.03);
      }); },
      ok: function () { play(function (n) { tone(1046, "square", n, 0.06, 0.025); }); },
      // Nedtaelling: et tik hvert sekund de sidste ti, hoejere de sidste tre.
      count: function (secs) { play(function (n) {
        if (secs <= 3) tone(1480, "square", n, 0.09, 0.04);
        else tone(990, "square", n, 0.035, 0.022);
      }); },
      // Afleveret: tre blip opad.
      received: function () { play(function (n) {
        tone(784, "square", n, 0.06, 0.03);
        tone(1046, "square", n + 0.07, 0.06, 0.03);
        tone(1568, "square", n + 0.14, 0.12, 0.03);
      }); },
      // Den ekstra tid starter: en lav to-tone-alarm.
      alarm: function () { play(function (n) {
        for (var k = 0; k < 3; k++) {
          tone(440, "sawtooth", n + k * 0.32, 0.14, 0.03);
          tone(330, "sawtooth", n + k * 0.32 + 0.16, 0.14, 0.03);
        }
      }); },
      // Genren afsloeres.
      reveal: function () { play(function (n) {
        tone(110, "sine", n, 1.2, 0.16, { attack: 0.02 });
        tone(660, "triangle", n, 0.8, 0.04, { to: 990, attack: 0.02 });
      }); },
      // Resultatet: en kort fanfare - C, E, G og en lang C.
      fanfare: function () { play(function (n) {
        [523, 659, 784].forEach(function (f, k) { tone(f, "square", n + k * 0.12, 0.11, 0.035); });
        tone(1046, "square", n + 0.36, 0.5, 0.04);
        tone(523, "triangle", n + 0.36, 0.7, 0.05);
      }); }
    };
  })();


  /* ---------- Formularer og knapper ---------- */

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
      event.preventDefault();
      return;
    }
    if (form.dataset.busy) {
      var btn = form.querySelector('button[type="submit"], button:not([type])');
      if (btn) {
        // Efter submit, ellers kommer knappens navn ikke med.
        setTimeout(function () {
          btn.disabled = true;
          btn.textContent = form.dataset.busy;
        }, 0);
      }
    }
  });

  document.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-copy]");
    if (!btn) return;
    var text = btn.dataset.copy;
    var label = btn.dataset.label || (btn.dataset.label = btn.textContent.trim());

    function done(ok) {
      btn.textContent = ok ? "Kopieret" : "Kunne ikke kopiere";
      btn.classList.toggle("is-done", ok);
      // Lykkes det ikke, vises linket under knappen, klar til at markere.
      var spare = btn.nextElementSibling;
      if (!ok && !(spare && spare.classList.contains("copy-spare"))) {
        spare = document.createElement("input");
        spare.className = "input copy-spare";
        spare.readOnly = true;
        spare.value = text;
        spare.setAttribute("aria-label", "Linket til lobbyen");
        btn.insertAdjacentElement("afterend", spare);
      }
      if (!ok) { spare.focus(); spare.select(); }
      setTimeout(function () {
        btn.textContent = label;
        btn.classList.remove("is-done");
      }, ok ? 1800 : 2600);
    }

    // Den gamle maade: en markeret tekst og execCommand. Virker hvor
    // clipboard-API'et ikke er tilladt.
    function legacy() {
      var area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.className = "sr";
      document.body.appendChild(area);
      area.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      document.body.removeChild(area);
      done(ok);
    }

    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, legacy);
    } else {
      legacy();
    }
  });

  /* Kun et nummer ad gangen. Starter man et nyt, stopper det forrige. */
  document.addEventListener("play", function (event) {
    document.querySelectorAll("audio").forEach(function (a) {
      if (a !== event.target && !a.paused) a.pause();
    });
  }, true);

  /* ---------- Afspilleren ----------
     I stedet for browserens egen afspiller (den kan ikke styles, og den
     ser forskellig ud i hver browser): en rund afspil-knap, hvis ring
     lyser gult mens der spilles, en tyk gul fremdriftslinje med en knop -
     klik, traek eller piletaster for at spole - og titel til venstre, tid
     til hoejre. Ingen volumen (brug telefonens knapper) og ingen menu (den
     tilboed "Hent", ogsaa under en anonym afstemning).

     <audio> bliver liggende og spiller lyden; den er bare skjult.
     locked: under "lyt sammen" styrer serveren, hvor nummeret er - saa kan
     man ikke spole, og knappen slaar kun lyden til og fra her. */

  function fmt(seconds) {
    if (!isFinite(seconds) || seconds < 0) seconds = 0;
    var s = Math.floor(seconds), m = Math.floor(s / 60);
    return m + ":" + (s % 60 < 10 ? "0" : "") + (s % 60);
  }

  function player(audio, opts) {
    opts = opts || {};
    if (audio._sp) {
      if (opts.label) audio._sp.setLabel(opts.label);
      if (opts.seconds) audio._sp.setSeconds(opts.seconds);
      return audio._sp;
    }
    var label = opts.label || audio.dataset.label || "Lyd";
    var fallback = opts.seconds || parseFloat(audio.dataset.seconds) || 0;
    var locked = false, onToggle = null;

    audio.controls = false;
    audio.removeAttribute("controls");
    audio.classList.add("sp__audio");

    var box = document.createElement("div");
    box.className = "sp";
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "sp__play";
    var icon = document.createElement("span");
    icon.className = "sp__icon";
    icon.setAttribute("aria-hidden", "true");
    btn.appendChild(icon);

    var main = document.createElement("div");
    main.className = "sp__main";
    var head = document.createElement("div");
    head.className = "sp__head";
    var title = document.createElement("span");
    title.className = "sp__title";
    var time = document.createElement("span");
    time.className = "sp__time";
    head.appendChild(title);
    head.appendChild(time);

    var bar = document.createElement("div");
    bar.className = "sp__bar";
    bar.tabIndex = 0;
    bar.setAttribute("role", "slider");
    bar.setAttribute("aria-valuemin", "0");
    var fill = document.createElement("span");
    fill.className = "sp__fill";
    var knob = document.createElement("span");
    knob.className = "sp__knob";
    bar.appendChild(fill);
    bar.appendChild(knob);
    main.appendChild(head);
    main.appendChild(bar);
    box.appendChild(btn);
    box.appendChild(main);
    audio.parentNode.insertBefore(box, audio);
    box.appendChild(audio);

    function duration() {
      return isFinite(audio.duration) && audio.duration > 0 ? audio.duration : fallback;
    }

    function paint() {
      var d = duration(), at = Math.min(audio.currentTime || 0, d || 0);
      var pct = d ? (at / d) * 100 : 0;
      fill.style.setProperty("--pct", pct.toFixed(2) + "%");
      knob.style.setProperty("--pct", pct.toFixed(2) + "%");
      time.textContent = fmt(at) + " / " + fmt(d);
      bar.setAttribute("aria-valuemax", String(Math.round(d)));
      bar.setAttribute("aria-valuenow", String(Math.round(at)));
      bar.setAttribute("aria-valuetext", fmt(at) + " af " + fmt(d));
      var playing = !audio.paused && !audio.ended;
      box.classList.toggle("is-playing", playing);
      btn.setAttribute("aria-label", (playing ? "Pause " : "Afspil ") + label);
      btn.setAttribute("aria-pressed", playing ? "true" : "false");
    }

    function setLabel(text) {
      label = text;
      title.textContent = text;
      bar.setAttribute("aria-label", "Spol i " + text);
      paint();
    }

    ["play", "pause", "ended", "timeupdate", "loadedmetadata", "durationchange", "seeked", "emptied"]
      .forEach(function (ev) { audio.addEventListener(ev, paint); });

    btn.addEventListener("click", function () {
      if (locked) { if (onToggle) onToggle(); return; }
      if (audio.paused) { var p = audio.play(); if (p && p.catch) p.catch(function () {}); }
      else audio.pause();
    });

    function seekTo(seconds) {
      if (locked) return;
      var d = duration();
      if (!d) return;
      var target = Math.max(0, Math.min(d, seconds));
      if (audio.readyState === 0) {
        // Ikke hentet endnu (preload="none"): hent, og spol naar den kender laengden.
        audio.addEventListener("loadedmetadata", function once() {
          audio.removeEventListener("loadedmetadata", once);
          audio.currentTime = target;
        });
        audio.load();
      } else {
        audio.currentTime = target;
      }
      paint();
    }

    function fromPointer(e) {
      var r = bar.getBoundingClientRect();
      return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * duration();
    }
    var dragging = false;
    bar.addEventListener("pointerdown", function (e) {
      if (locked) return;
      dragging = true;
      bar.setPointerCapture(e.pointerId);
      box.classList.add("is-seeking");
      seekTo(fromPointer(e));
    });
    bar.addEventListener("pointermove", function (e) { if (dragging) seekTo(fromPointer(e)); });
    function endDrag() { dragging = false; box.classList.remove("is-seeking"); }
    bar.addEventListener("pointerup", endDrag);
    bar.addEventListener("pointercancel", endDrag);
    bar.addEventListener("keydown", function (e) {
      var step = { ArrowRight: 5, ArrowUp: 5, ArrowLeft: -5, ArrowDown: -5, PageUp: 15, PageDown: -15 }[e.key];
      if (step !== undefined) { e.preventDefault(); seekTo((audio.currentTime || 0) + step); }
      else if (e.key === "Home") { e.preventDefault(); seekTo(0); }
      else if (e.key === "End") { e.preventDefault(); seekTo(duration()); }
    });

    var api = {
      el: box,
      audio: audio,
      setLabel: setLabel,
      setSeconds: function (s) { fallback = s || fallback; paint(); },
      // Under lyt sammen: ingen spoling; knappen slaar lyden til og fra her.
      setLocked: function (on, toggle) {
        locked = !!on;
        onToggle = toggle || null;
        box.classList.toggle("is-locked", locked);
        bar.setAttribute("aria-disabled", locked ? "true" : "false");
        bar.tabIndex = locked ? -1 : 0;
      },
      paint: paint
    };
    audio._sp = api;
    setLabel(label);
    return api;
  }

  document.querySelectorAll("audio.player").forEach(function (a) { player(a); });

  /* Tidspunkter fra serveren (<time data-ts="ms">) i browserens egen tid -
     serveren koerer i UTC. "21. okt. kl. 20.14". */
  document.querySelectorAll("time[data-ts]").forEach(function (node) {
    var d = new Date(parseInt(node.dataset.ts, 10));
    if (isNaN(d)) return;
    node.dateTime = d.toISOString();
    node.textContent = d.toLocaleDateString("da-DK", { day: "numeric", month: "short" }) +
      " kl. " + d.toLocaleTimeString("da-DK", { hour: "2-digit", minute: "2-digit" });
  });

  /* ---------- Undertekster ---------- */

  /* Den gule tekst skrives ud tegn for tegn. Det er ikke den slags
     bevaegelse "reduceret bevaegelse" handler om - intet flytter sig paa
     skaermen - saa den koerer altid. En ny tekst afbryder den forrige. */
  function typeInto(node, text, done, speed) {
    clearInterval(node._typer);
    clearTimeout(node._caretOff);
    if (still()) { node.textContent = text; if (done) done(); return; }
    // Hvert bogstav i sit eget span, usynligt fra start. Saa er teksten
    // lige bred hele vejen, og centreret tekst staar stille mens den skrives.
    node.textContent = "";
    var chars = Array.prototype.map.call(text, function (ch) {
      var span = document.createElement("span");
      span.className = "tw-ghost";
      span.textContent = ch;
      node.appendChild(span);
      return span;
    });
    var i = 0;
    node._typer = setInterval(function () {
      if (i > 0) chars[i - 1].classList.remove("tw-caret");
      if (i >= chars.length) {
        clearInterval(node._typer);
        var last = chars[chars.length - 1];
        if (last) {
          last.classList.add("tw-caret");
          node._caretOff = setTimeout(function () { last.classList.remove("tw-caret"); }, 1200);
        }
        if (done) done();
        return;
      }
      chars[i].classList.remove("tw-ghost");
      chars[i].classList.add("tw-caret");
      i += 1;
    }, speed || 45);
  }

  /* Til tekst der kommer fra serveren (genren). Skrives kun ud naar den
     er ny - en poll med samme tekst roerer den ikke. `done` kaldes naar
     den er skrevet, saa to tekster kan skrives efter hinanden. */
  function type(node, text, speed, done) {
    if (!node || node.dataset.typed === text) return;
    node.dataset.typed = text;
    node.setAttribute("aria-label", text);
    typeInto(node, text, done, speed);
  }

  var caption = document.getElementById("caption");
  var captionQueue = [];
  var captionBusy = false;

  /* En linje nederst i billedet, som i en cutscene. Kommer der flere paa
     en gang, vises de efter hinanden - aldrig oven i hinanden. */
  function say(text) {
    if (!caption || !text) return;
    if (captionQueue[captionQueue.length - 1] === text) return;
    captionQueue.push(text);
    if (!captionBusy) next();
  }

  function next() {
    var text = captionQueue.shift();
    if (text === undefined) { captionBusy = false; return; }
    captionBusy = true;
    caption.classList.add("is-on");
    typeInto(caption, text, function () {
      setTimeout(function () {
        caption.classList.remove("is-on");
        setTimeout(next, 450);
      }, Math.min(4200, 1600 + text.length * 45));
    });
  }

  /* ---------- Titlerne: boelgen ----------
     Hvert bogstav i sit eget span med sit nummer (--i). CSS'en lader dem
     poppe ind efter tur - som den gamle typewriter, bare med fjedre - og
     med loop="1" boelger de bagefter. Ordene holdes samlet, saa en lang
     titel stadig brydes mellem ordene paa en smal skaerm.

     Kun tekst-noder deles op. Elementer inde i titlen (fx prikkerne efter
     "Venter paa flere spillere") bliver staaende som de er. */

  function wave(node, loop) {
    if (!node || node.dataset.waved) return;
    node.dataset.waved = "1";
    node.setAttribute("aria-label", node.textContent.replace(/\s+/g, " ").trim());
    node.classList.add("is-wave");
    if (loop) node.classList.add("is-loop");
    var i = 0;
    Array.prototype.slice.call(node.childNodes).forEach(function (child) {
      if (child.nodeType !== 3) return;
      var frag = document.createDocumentFragment();
      child.textContent.split(/(\s+)/).forEach(function (part) {
        if (!part) return;
        if (/^\s+$/.test(part)) { frag.appendChild(document.createTextNode(" ")); return; }
        var word = document.createElement("span");
        word.className = "word";
        word.setAttribute("aria-hidden", "true");
        Array.from(part).forEach(function (ch) {
          var span = document.createElement("span");
          span.className = "ch";
          span.textContent = ch;
          span.style.setProperty("--i", String(i++));
          word.appendChild(span);
        });
        frag.appendChild(word);
      });
      node.replaceChild(frag, child);
    });
  }

  /* Til tekst fra serveren (genren): skift teksten og lad den poppe ind
     igen - men kun naar den faktisk er ny. */
  function waveText(node, text, loop) {
    if (!node || node.dataset.text === text) return;
    node.dataset.text = text;
    delete node.dataset.waved;
    node.textContent = text;
    wave(node, loop);
  }

  window.SW = { say: say, type: type, wave: wave, waveText: waveText, sound: sound, buzz: buzz,
                still: still, prefs: function () { return prefs; }, setPref: setPref,
                player: player,
                onPrefs: function (fn) { prefListeners.push(fn); } };

  document.querySelectorAll("[data-wave]").forEach(function (node) {
    wave(node, node.dataset.wave === "loop");
  });

  document.querySelectorAll("[data-type]").forEach(function (node) {
    var text = node.textContent.trim();
    node.setAttribute("aria-label", text);
    node.textContent = " ";
    setTimeout(function () { type(node, text, 70); }, calm ? 300 : 900);
  });

  /* Lobbykoden skrives med store bogstaver, uanset hvad man taster. */
  document.addEventListener("input", function (event) {
    var input = event.target;
    if (!input.matches || !input.matches("[data-upper]")) return;
    var at = input.selectionStart;
    input.value = input.value.toUpperCase();
    if (at !== null) input.setSelectionRange(at, at);
  });

  /* ---------- Scenen ---------- */

  // Fast frø: traeet ser ens ud paa alle sider og ved hver indlaesning.
  function rng(seed) {
    return function () {
      seed |= 0; seed = seed + 0x6D2B79F5 | 0;
      var t = Math.imul(seed ^ seed >>> 15, 1 | seed);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }

  function drawTree(svg) {
    var rand = rng(1979);
    var NS = "http://www.w3.org/2000/svg";
    var depthMax = 8;
    var paths = [];
    for (var d = 0; d <= depthMax; d++) paths.push([]);

    function grow(x, y, angle, length, depth) {
      var x2 = x + Math.cos(angle) * length;
      var y2 = y - Math.sin(angle) * length;
      // Lidt bue i hver gren, saa den ikke ser ud som en vektor-tegning.
      var bend = (rand() - 0.5) * length * 0.35;
      var cx = (x + x2) / 2 + Math.cos(angle + Math.PI / 2) * bend;
      var cy = (y + y2) / 2 - Math.sin(angle + Math.PI / 2) * bend;
      paths[depth].push("M" + x.toFixed(1) + " " + y.toFixed(1) +
                        "Q" + cx.toFixed(1) + " " + cy.toFixed(1) + " " +
                        x2.toFixed(1) + " " + y2.toFixed(1));
      if (depth >= depthMax) return;
      var kids = depth < 2 ? 2 : (rand() < 0.35 ? 3 : 2);
      for (var k = 0; k < kids; k++) {
        var spread = depth < 2 ? 0.55 : 0.75;
        var turn = (k - (kids - 1) / 2) * spread + (rand() - 0.5) * 0.5;
        // Grenene vil op mod lyset; de yderste haenger lidt.
        var lift = depth > 5 ? -0.08 : 0.05;
        grow(x2, y2, angle + turn + lift, length * (0.66 + rand() * 0.16), depth + 1);
      }
    }

    grow(300, 700, Math.PI / 2 + 0.04, 170, 0);

    paths.forEach(function (list, depth) {
      if (!list.length) return;
      var p = document.createElementNS(NS, "path");
      p.setAttribute("d", list.join(""));
      p.setAttribute("class", "branch");
      p.setAttribute("stroke-width", String(Math.max(0.6, 26 * Math.pow(0.62, depth))));
      svg.appendChild(p);
    });
  }

  var tree = document.getElementById("scene-tree");
  if (tree) drawTree(tree);

  var LEAF_TONES = ["#b8622a", "#d4893c", "#9a4f27", "#c97a35", "#7d4a2c", "#e0a24e"];

  /* Bladene og kragerne er en del af stemningen, saa de er med ogsaa ved
     reduceret bevaegelse - de driver roligt i baggrunden og flytter ikke
     noget man skal laese eller trykke paa. */
  function scatterLeaves(box) {
    var rand = Math.random;
    var count = window.innerWidth < 600 ? 9 : 16;
    for (var i = 0; i < count; i++) {
      var leaf = document.createElement("span");
      leaf.className = "leaf";
      var inner = document.createElement("span");
      inner.className = "leaf__shape";
      leaf.appendChild(inner);
      var fall = 11 + rand() * 12;
      leaf.style.setProperty("--x", (rand() * 100).toFixed(1) + "vw");
      leaf.style.setProperty("--drift", ((rand() - 0.3) * 30).toFixed(1) + "vw");
      leaf.style.setProperty("--fall", fall.toFixed(1) + "s");
      leaf.style.setProperty("--delay", (-rand() * fall).toFixed(1) + "s");
      leaf.style.setProperty("--sway", (2.2 + rand() * 2.4).toFixed(1) + "s");
      leaf.style.setProperty("--size", (7 + rand() * 9).toFixed(0) + "px");
      leaf.style.setProperty("--tone", LEAF_TONES[Math.floor(rand() * LEAF_TONES.length)]);
      leaf.style.setProperty("--spin", (rand() < 0.5 ? -1 : 1) * (240 + rand() * 360) + "deg");
      box.appendChild(leaf);
    }
  }

  /* Gnister der svaever op fra jorden, langsomt og uden at roere ved
     noget. De laves ikke ved reduceret bevaegelse. */
  function scatterSparks(box) {
    if (calm || still()) return;
    var count = window.innerWidth < 600 ? 7 : 12;
    for (var i = 0; i < count; i++) {
      var spark = document.createElement("span");
      spark.className = "spark";
      var rise = 10 + Math.random() * 10;
      spark.style.setProperty("--x", (Math.random() * 100).toFixed(1) + "vw");
      spark.style.setProperty("--drift", ((Math.random() - 0.5) * 18).toFixed(1) + "vw");
      spark.style.setProperty("--rise", rise.toFixed(1) + "s");
      spark.style.setProperty("--delay", (-Math.random() * rise).toFixed(1) + "s");
      spark.style.setProperty("--size", (2 + Math.random() * 3).toFixed(1) + "px");
      box.appendChild(spark);
    }
  }

  /* Parallax: lagene i baggrunden flytter sig lidt med musen - byen mindst,
     traeet mest. Kun med en rigtig mus, og kun naar bevaegelse er tilladt. */
  function parallax(scene) {
    if (calm || still() || !window.matchMedia("(pointer: fine)").matches) return;
    var pending = false, px = 0, py = 0;
    window.addEventListener("pointermove", function (e) {
      px = e.clientX / window.innerWidth - 0.5;
      py = e.clientY / window.innerHeight - 0.5;
      if (pending) return;
      pending = true;
      requestAnimationFrame(function () {
        scene.style.setProperty("--px", px.toFixed(3));
        scene.style.setProperty("--py", py.toFixed(3));
        pending = false;
      });
    }, { passive: true });
  }

  var sceneNode = document.querySelector(".scene");
  if (sceneNode) {
    scatterSparks(document.getElementById("leaves"));
    parallax(sceneNode);
  }

  var leaves = document.getElementById("leaves");
  var sceneStarted = false;
  function startScene() {
    if (sceneStarted || still()) return;
    sceneStarted = true;
    if (leaves) scatterLeaves(leaves);
    var townNode = document.querySelector(".scene__town");
    if (townNode) startCrows(townNode);
  }

  /* ---------- Kragerne ----------
     En eller to ad gangen. De flyver ind i en bue, lander paa en tagkant
     (eller lampen), sidder lidt, og letter igen. Tegnet inde i byens SVG,
     saa de lander praecis paa kanten uanset skaermstoerrelse. */

  var NS = "http://www.w3.org/2000/svg";
  var VIEW_W = 1600, VIEW_H = 900;

  // Hvor de kan sidde, i byens koordinater: tagkanterne og lampehovedet.
  var PERCHES = [
    { x1: 50, x2: 630, y: 318 },     // venstre bygning
    { x1: 925, x2: 1555, y: 268 },   // hoejre bygning
    { x1: 575, x2: 965, y: 470 },    // bygningen bagved - den man ser paa mobil
    { x1: 1258, x2: 1278, y: 500 }   // lampen
  ];

  function svgEl(tag, attrs, parent) {
    var node = document.createElementNS(NS, tag);
    for (var k in attrs) node.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(node);
    return node;
  }

  function makeCrow(layer) {
    var g = svgEl("g", { "class": "crow" }, layer);
    // I luften: krop, hoved, naeb, hale og to vinger der blafrer.
    var fly = svgEl("g", { "class": "crow__fly" }, g);
    svgEl("path", { "class": "crow__wing", d: "M-3 -1 Q-7 -13 -17 -17 Q-8 -9 5 -1 Z" }, fly);
    svgEl("ellipse", { cx: 0, cy: 0, rx: 9, ry: 3.6 }, fly);
    svgEl("circle", { cx: 9, cy: -1.5, r: 3.2 }, fly);
    svgEl("path", { d: "M11.5 -2.6 L17 -1.4 L11.5 -0.2 Z" }, fly);
    svgEl("path", { d: "M-8 -1 L-16 -4 L-16 3.5 L-8 1.5 Z" }, fly);
    svgEl("path", { "class": "crow__wing crow__wing--far", d: "M-1 0 Q-3 10 -10 14 Q-3 9 4 0.5 Z" }, fly);
    // Siddende: foldet og lidt sammenkroebet, foedderne paa y = 0.
    var sit = svgEl("g", { "class": "crow__sit" }, g);
    svgEl("path", { d: "M-5 -6 L-15 -1 L-13.5 0.8 L-3 -3 Z" }, sit);
    svgEl("path", { d: "M-6 -8 C-6 -14 0 -17 5 -15.5 C8 -14.5 9 -11 8 -8 C7 -4.5 3 -2.5 -1 -2.5 C-4 -2.5 -6 -5 -6 -8 Z" }, sit);
    var head = svgEl("g", { "class": "crow__head" }, sit);
    svgEl("circle", { cx: 6.5, cy: -16, r: 3.6 }, head);
    svgEl("path", { d: "M9.4 -17.2 L15 -15.4 L9.4 -14.6 Z" }, head);
    svgEl("path", { "class": "crow__legs", d: "M-0.5 -3 L-1 0 M2.5 -3 L2.5 0" }, sit);
    return g;
  }

  function visibleX(town) {
    // Byen er skaleret med "slice": paa en smal skaerm skaeres siderne af.
    var box = town.getBoundingClientRect();
    var scale = Math.max(box.width / VIEW_W, box.height / VIEW_H);
    var shown = box.width / scale;
    var left = (VIEW_W - shown) / 2;
    return [left, left + shown];
  }

  function bezier(a, b, c, t) {
    var u = 1 - t;
    return u * u * a + 2 * u * t * b + t * t * c;
  }

  function startCrows(town) {
    var layer = svgEl("g", { "class": "crows" }, town);
    var flock = [];
    var slow = calm ? 1.7 : 1;

    function pickPerch() {
      var range = visibleX(town);
      var options = [];
      PERCHES.forEach(function (p) {
        var a = Math.max(p.x1, range[0] + 30), b = Math.min(p.x2, range[1] - 30);
        if (b > a) options.push({ a: a, b: b, y: p.y });
      });
      for (var tries = 0; tries < 12 && options.length; tries++) {
        var o = options[Math.floor(Math.random() * options.length)];
        var x = o.a + Math.random() * (o.b - o.a);
        // Ikke oven i en der allerede sidder der.
        var free = flock.every(function (c) {
          return Math.abs(c.perch.x - x) > 46 || c.perch.y !== o.y;
        });
        if (free) return { x: x, y: o.y, range: range };
      }
      return null;
    }

    function place(crow, x, y) {
      crow.g.setAttribute("transform", "translate(" + x.toFixed(1) + " " + y.toFixed(1) +
        ") scale(" + (crow.dir * 1.45) + " 1.45)");
    }

    function spawn() {
      if (flock.length >= 2) return;
      var perch = pickPerch();
      if (!perch) return;
      var fromLeft = Math.random() < 0.5;
      var startX = fromLeft ? perch.range[0] - 60 : perch.range[1] + 60;
      var crow = {
        g: makeCrow(layer),
        perch: perch,
        state: "in",
        from: { x: startX, y: 60 + Math.random() * 160 },
        dir: fromLeft ? 1 : -1,
        t0: performance.now(),
        dur: (3200 + Math.random() * 1800) * slow
      };
      crow.ctrl = { x: (startX + perch.x) / 2,
                    y: Math.min(crow.from.y, perch.y) - 80 - Math.random() * 60 };
      crow.g.classList.add("is-flying");
      place(crow, crow.from.x, crow.from.y);
      flock.push(crow);
    }

    function takeOff(crow, now) {
      var range = visibleX(town);
      // Letter som regel videre i samme retning; en gang imellem vender den.
      if (Math.random() < 0.3) crow.dir = -crow.dir;
      crow.from = { x: crow.perch.x, y: crow.perch.y };
      crow.to = { x: crow.dir > 0 ? range[1] + 70 : range[0] - 70, y: 30 + Math.random() * 140 };
      crow.ctrl = { x: crow.perch.x + crow.dir * 160, y: crow.perch.y - 200 };
      crow.state = "out";
      crow.t0 = now;
      crow.dur = (2600 + Math.random() * 1400) * slow;
      crow.g.classList.remove("is-sitting");
      crow.g.classList.add("is-flying");
    }

    function frame(now) {
      for (var i = flock.length - 1; i >= 0; i--) {
        var c = flock[i];
        var t = Math.min(1, (now - c.t0) / c.dur);
        if (c.state === "in") {
          var e = 1 - Math.pow(1 - t, 3);            // bremser op foer landing
          place(c, bezier(c.from.x, c.ctrl.x, c.perch.x, e), bezier(c.from.y, c.ctrl.y, c.perch.y, e));
          if (t >= 1) {
            c.state = "sit";
            c.g.classList.remove("is-flying");
            c.g.classList.add("is-sitting");
            c.leaveAt = now + (5000 + Math.random() * 7000) * slow;
            c.turnAt = now + 1800 + Math.random() * 2500;
          }
        } else if (c.state === "sit") {
          if (now >= c.turnAt) {
            // Vender sig en gang imellem, som krager goer.
            c.dir = -c.dir;
            place(c, c.perch.x, c.perch.y);
            c.turnAt = now + 2500 + Math.random() * 3500;
          }
          if (now >= c.leaveAt) takeOff(c, now);
        } else {
          var e2 = t * t;                              // tager fart
          place(c, bezier(c.from.x, c.ctrl.x, c.to.x, e2), bezier(c.from.y, c.ctrl.y, c.to.y, e2));
          if (t >= 1) {
            layer.removeChild(c.g);
            flock.splice(i, 1);
          }
        }
      }
      requestAnimationFrame(frame);
    }

    function schedule() {
      spawn();
      setTimeout(schedule, (5000 + Math.random() * 9000) * slow);
    }

    setTimeout(schedule, 1500);
    requestAnimationFrame(frame);
  }

  startScene();
  prefListeners.push(startScene);
})();
