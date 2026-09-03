/* EBVB - faner, afspiller og negativ.
   Ingen afhaengigheder. Siden virker uden JS, den bliver bare mindre rar:
   begge lister vises, og sporene hentes i stedet for at blive afspillet.

   Afspilningen styres fra baren i bunden. Panelet i siden er kun
   detaljer om sporet og bliver skrevet af load(). */

(function () {
  "use strict";

  /* ---------- Negativ (dark mode) ---------- */

  var root = document.documentElement;
  var themeBtn = document.getElementById("theme");

  function paintTheme() {
    var dark = root.dataset.theme === "dark";
    if (!themeBtn) return;
    themeBtn.setAttribute("aria-pressed", dark ? "true" : "false");
    var label = themeBtn.querySelector("[data-theme-label]");
    if (label) label.textContent = dark ? "Positiv" : "Negativ";
  }

  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
      try { localStorage.setItem("ebvb-theme", root.dataset.theme); } catch (e) {}
      paintTheme();
    });
    paintTheme();
  }

  /* ---------- Faner ---------- */

  var tabs = Array.prototype.slice.call(document.querySelectorAll(".tab"));
  var lists = Array.prototype.slice.call(document.querySelectorAll(".list"));

  function showSection(slug, push) {
    var found = false;
    lists.forEach(function (list) {
      var match = list.dataset.section === slug;
      list.hidden = !match;
      if (match) {
        found = true;
        list.classList.remove("is-entering");
        void list.offsetWidth;
        list.classList.add("is-entering");
      }
    });
    if (!found) return;

    tabs.forEach(function (tab) {
      var active = tab.dataset.tab === slug;
      tab.classList.toggle("is-active", active);
      if (active) tab.setAttribute("aria-current", "page");
      else tab.removeAttribute("aria-current");
    });

    var radio = document.querySelector('.pick input[value="' + slug + '"]');
    if (radio) radio.checked = true;

    if (push) history.pushState({ slug: slug }, "", "/" + slug);
  }

  tabs.forEach(function (tab) {
    tab.addEventListener("click", function (event) {
      event.preventDefault();
      showSection(tab.dataset.tab, true);
    });
  });

  window.addEventListener("popstate", function () {
    showSection(location.pathname.replace(/^\//, "") || "beats", false);
  });

  /* ---------- Afspiller ---------- */

  var plate = document.getElementById("plate");
  if (!plate) return;

  var plateArt = document.querySelector(".plate__art");
  var img = document.getElementById("plate-img");
  var blank = document.getElementById("plate-blank");
  var titleEl = document.getElementById("plate-title");
  var metaEl = document.getElementById("plate-meta");
  var dlEl = document.getElementById("plate-dl");
  var delEl = document.getElementById("plate-del");

  var dBpm = document.getElementById("d-bpm");
  var dKey = document.getElementById("d-key");
  var dNoteWrap = document.getElementById("d-note-wrap");
  var dNote = document.getElementById("d-note");
  var dBy = document.getElementById("d-by");
  var dDate = document.getElementById("d-date");
  var dFile = document.getElementById("d-file");
  var dLen = document.getElementById("d-len");

  var deckImg = document.getElementById("deck-img");
  var deckTitle = document.getElementById("deck-title");
  var deckSub = document.getElementById("deck-sub");

  var playBtn = document.getElementById("play");
  var prevBtn = document.getElementById("prev");
  var nextBtn = document.getElementById("next");
  var stopBtn = document.getElementById("stop");
  var seek = document.getElementById("seek");
  var vol = document.getElementById("vol");
  var atEl = document.getElementById("at");
  var durEl = document.getElementById("dur");

  var audio = new Audio();
  audio.preload = "metadata";
  var current = null;
  var scrubbing = false;

  /* Lydstyrken skal huskes - ellers starter man paa fuld hver gang. */
  var savedVol = 80;
  try {
    var raw = localStorage.getItem("ebvb-vol");
    if (raw !== null) savedVol = Math.min(100, Math.max(0, parseInt(raw, 10) || 0));
  } catch (e) {}
  vol.value = String(savedVol);
  audio.volume = savedVol / 100;

  vol.addEventListener("input", function () {
    audio.volume = vol.value / 100;
    try { localStorage.setItem("ebvb-vol", vol.value); } catch (e) {}
  });

  function clock(seconds) {
    if (!isFinite(seconds) || seconds < 0) return "0:00";
    var m = Math.floor(seconds / 60);
    var s = Math.floor(seconds % 60);
    return m + ":" + (s < 10 ? "0" : "") + s;
  }

  function visibleRows() {
    var list = lists.filter(function (l) { return !l.hidden; })[0];
    if (!list) return [];
    return Array.prototype.slice.call(list.querySelectorAll(".row"));
  }

  function markCurrent() {
    document.querySelectorAll(".row").forEach(function (row) {
      row.classList.toggle("is-current", row === current);
    });
  }

  /* Coveret lander. Klassen skal fjernes og saettes igen, ellers
     spiller animationen ikke naar man vaelger to spor i traek. */
  function settleArt() {
    plateArt.classList.remove("is-fresh");
    void plateArt.offsetWidth;
    plateArt.classList.add("is-fresh");
  }

  function load(row, autoplay) {
    if (!row) return;
    current = row;
    markCurrent();

    plate.removeAttribute("data-empty");
    titleEl.textContent = row.dataset.title;
    metaEl.textContent = row.dataset.by + " · " + row.dataset.date;

    dBpm.textContent = row.dataset.bpm || "—";
    dKey.textContent = row.dataset.key || "—";
    dBy.textContent = row.dataset.by || "—";
    dDate.textContent = row.dataset.date || "—";
    dFile.textContent = row.dataset.file || "—";
    dLen.textContent = "—";

    if (row.dataset.note) {
      dNote.textContent = row.dataset.note;
      dNoteWrap.hidden = false;
    } else {
      dNoteWrap.hidden = true;
    }

    deckTitle.textContent = row.dataset.title;
    var sub = [];
    if (row.dataset.bpm) sub.push(row.dataset.bpm + " BPM");
    if (row.dataset.key) sub.push(row.dataset.key);
    sub.push(row.dataset.by);
    deckSub.textContent = sub.join(" · ");

    if (row.dataset.cover) {
      img.src = row.dataset.cover;
      img.hidden = false;
      blank.hidden = true;
      deckImg.src = row.dataset.cover;
      deckImg.hidden = false;
      if (img.complete) settleArt(); else img.onload = settleArt;
    } else {
      img.removeAttribute("src");
      img.hidden = true;
      blank.hidden = false;
      deckImg.removeAttribute("src");
      deckImg.hidden = true;
      settleArt();
    }

    dlEl.href = row.dataset.dl;
    dlEl.hidden = false;
    if (row.dataset.own) {
      delEl.action = "/slet/" + row.dataset.id;
      delEl.hidden = false;
    } else {
      delEl.hidden = true;
    }

    seek.disabled = false;
    seek.value = 0;
    atEl.textContent = "0:00";
    durEl.textContent = "0:00";

    audio.src = row.dataset.src;
    if (autoplay) {
      audio.play().catch(function () {
        metaEl.textContent = "Kunne ikke afspille filen her. Hent den i stedet.";
      });
    }
  }

  function step(delta) {
    var rows = visibleRows();
    if (!rows.length) return;
    var at = rows.indexOf(current);
    var next = rows[(at + delta + rows.length) % rows.length];
    load(next, true);
  }

  document.querySelectorAll(".row").forEach(function (row) {
    row.querySelector(".row__hit").addEventListener("click", function () {
      if (row === current) {
        if (audio.paused) audio.play(); else audio.pause();
      } else {
        load(row, true);
      }
    });
  });

  /* Panelet skal ikke staa tomt. Nyeste spor laegges paa med det samme,
     uden at spille - saa er artworket det foerste man ser. */
  load(visibleRows()[0], false);

  playBtn.addEventListener("click", function () {
    if (!current) { step(1); return; }
    if (audio.paused) audio.play(); else audio.pause();
  });
  prevBtn.addEventListener("click", function () { step(-1); });
  nextBtn.addEventListener("click", function () { step(1); });

  /* Stop er ikke pause: den gaar tilbage til begyndelsen. */
  stopBtn.addEventListener("click", function () {
    audio.pause();
    audio.currentTime = 0;
    seek.value = 0;
    atEl.textContent = "0:00";
  });

  audio.addEventListener("play", function () {
    plate.classList.add("is-playing");
    document.body.classList.add("playing");
    playBtn.textContent = "\u23F8";
    playBtn.setAttribute("aria-label", "Pause");
  });

  function stopped() {
    plate.classList.remove("is-playing");
    document.body.classList.remove("playing");
    playBtn.textContent = "\u25B6";
    playBtn.setAttribute("aria-label", "Afspil");
  }
  audio.addEventListener("pause", stopped);
  audio.addEventListener("ended", function () { stopped(); step(1); });

  audio.addEventListener("loadedmetadata", function () {
    durEl.textContent = clock(audio.duration);
    dLen.textContent = clock(audio.duration);
  });

  audio.addEventListener("timeupdate", function () {
    atEl.textContent = clock(audio.currentTime);
    if (!scrubbing && audio.duration) {
      seek.value = String(Math.round((audio.currentTime / audio.duration) * 1000));
    }
  });

  seek.addEventListener("input", function () { scrubbing = true; });
  seek.addEventListener("change", function () {
    if (audio.duration) audio.currentTime = (seek.value / 1000) * audio.duration;
    scrubbing = false;
  });

  /* Mellemrum = play/pause, naar man ikke staar i et felt. */
  document.addEventListener("keydown", function (event) {
    if (event.code !== "Space" || event.target.closest("input, textarea, button, a, dialog")) return;
    event.preventDefault();
    if (!current) step(1);
    else if (audio.paused) audio.play();
    else audio.pause();
  });

  /* ---------- Upload ---------- */

  var sheet = document.getElementById("upload");
  if (!sheet) return;

  document.querySelectorAll("[data-open-upload]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (btn.dataset.section) {
        var radio = document.querySelector('.pick input[value="' + btn.dataset.section + '"]');
        if (radio) radio.checked = true;
      }
      sheet.showModal();
    });
  });

  sheet.querySelectorAll("[data-close-upload]").forEach(function (btn) {
    btn.addEventListener("click", function () { sheet.close(); });
  });

  /* Filnavnet er et fint forslag til titlen. */
  var audioInput = document.getElementById("audio-input");
  var titleInput = document.getElementById("title-input");
  audioInput.addEventListener("change", function () {
    var file = audioInput.files[0];
    if (file && !titleInput.value) {
      titleInput.value = file.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim();
    }
  });

  /* En 300 MB wav tager tid. Sig det, i stedet for at se doed ud. */
  document.getElementById("upload-form").addEventListener("submit", function () {
    document.getElementById("upload-go").disabled = true;
    document.getElementById("upload-wait").hidden = false;
  });
})();
