/* EBVB - faner, afspiller og negativ.
   Ingen afhaengigheder. Siden virker uden JS, den bliver bare mindre rar:
   begge lister vises, og sporene hentes i stedet for at blive afspillet. */

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
      if (match) found = true;
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

  var img = document.getElementById("plate-img");
  var blank = document.getElementById("plate-blank");
  var titleEl = document.getElementById("plate-title");
  var metaEl = document.getElementById("plate-meta");
  var dlEl = document.getElementById("plate-dl");
  var delEl = document.getElementById("plate-del");
  var playBtn = document.getElementById("play");
  var prevBtn = document.getElementById("prev");
  var nextBtn = document.getElementById("next");
  var seek = document.getElementById("seek");
  var atEl = document.getElementById("at");
  var durEl = document.getElementById("dur");

  var audio = new Audio();
  audio.preload = "metadata";
  var current = null;
  var scrubbing = false;

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

  function load(row, autoplay) {
    if (!row) return;
    current = row;
    markCurrent();

    plate.removeAttribute("data-empty");
    titleEl.textContent = row.dataset.title;

    var meta = row.dataset.meta || "";
    if (row.dataset.note) meta += " — " + row.dataset.note;
    metaEl.textContent = meta;

    if (row.dataset.cover) {
      img.src = row.dataset.cover;
      img.hidden = false;
      blank.hidden = true;
    } else {
      img.removeAttribute("src");
      img.hidden = true;
      blank.hidden = false;
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

  /* Pladen skal ikke staa tom. Nyeste spor laegges paa med det samme,
     uden at spille - saa er artworket det foerste man ser. */
  load(visibleRows()[0], false);

  playBtn.addEventListener("click", function () {
    if (!current) { step(1); return; }
    if (audio.paused) audio.play(); else audio.pause();
  });
  prevBtn.addEventListener("click", function () { step(-1); });
  nextBtn.addEventListener("click", function () { step(1); });

  audio.addEventListener("play", function () {
    plate.classList.add("is-playing");
    document.body.classList.add("playing");
    playBtn.textContent = "▮▮";
    playBtn.setAttribute("aria-label", "Pause");
  });

  function stopped() {
    plate.classList.remove("is-playing");
    document.body.classList.remove("playing");
    playBtn.textContent = "▶";
    playBtn.setAttribute("aria-label", "Afspil");
  }
  audio.addEventListener("pause", stopped);
  audio.addEventListener("ended", function () { stopped(); step(1); });

  audio.addEventListener("loadedmetadata", function () {
    durEl.textContent = clock(audio.duration);
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
