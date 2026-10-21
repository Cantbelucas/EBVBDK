/* Songwars - forsiden: startmenuen og "Tilpas".

   Menuen viser et panel ad gangen, styret af adressen (#spil, #tilpas,
   #indstillinger), saa browserens tilbage-knap virker. Piletasterne flytter
   mellem menupunkterne, Enter aabner, Esc gaar tilbage - som paa en konsol.

   Profilen (kaldenavn, avatar, farve) gemmes i browseren (sw-profile) og
   saettes ind i formularerne, naar man kommer med eller opretter en lobby.
   Serveren godtager kun avatarer og farver fra sin egen liste. */

(function () {
  "use strict";

  var start = document.getElementById("start");
  if (!start) return;
  var menu = document.getElementById("menu");
  var items = Array.prototype.slice.call(menu.querySelectorAll(".menu__item"));
  var panels = Array.prototype.slice.call(start.querySelectorAll(".panel"));
  var sound = window.SW && window.SW.sound;

  /* ---------- Profilen ---------- */

  var PROFILE_KEY = "sw-profile";
  var profile = { name: "", avatar: "", color: "" };
  try {
    var saved = JSON.parse(localStorage.getItem(PROFILE_KEY) || "{}");
    ["name", "avatar", "color"].forEach(function (k) {
      if (typeof saved[k] === "string") profile[k] = saved[k];
    });
  } catch (e) {}

  function saveProfile() {
    try { localStorage.setItem(PROFILE_KEY, JSON.stringify(profile)); } catch (e) {}
    paintProfile();
  }

  function initials(name) {
    var parts = name.trim().split(/\s+/).filter(Boolean);
    return parts.slice(0, 2).map(function (w) { return Array.from(w)[0]; }).join("").toUpperCase() || "?";
  }

  function paintFace(node) {
    node.className = node.className.replace(/\s*tint--\S+|\s*avatar--emoji/g, "");
    if (profile.color) node.classList.add("tint--" + profile.color);
    if (profile.avatar) node.classList.add("avatar--emoji");
    node.textContent = profile.avatar || initials(profile.name);
  }

  function paintProfile() {
    var face = document.getElementById("custom-face");
    if (face) paintFace(face);
    var preview = document.getElementById("custom-name-preview");
    if (preview) preview.textContent = profile.name || "Dit kaldenavn";
    // Menuen viser hvem man er, naar man har valgt noget.
    var me = document.getElementById("menu-me");
    if (me) {
      me.hidden = !(profile.name || profile.avatar);
      me.textContent = "";
      if (!me.hidden) {
        var f = document.createElement("span");
        f.className = "avatar avatar--small";
        paintFace(f);
        me.appendChild(f);
        me.appendChild(document.createTextNode(profile.name || "Uden navn"));
      }
    }
  }

  // Formularerne: kaldenavnet udfyldes, og avatar og farve sendes med.
  document.querySelectorAll("form[data-profile]").forEach(function (form) {
    var name = form.querySelector('input[name="name"]');
    if (name && !name.value && profile.name) name.value = profile.name;
    form.addEventListener("submit", function () {
      form.querySelector('input[name="avatar"]').value = profile.avatar;
      form.querySelector('input[name="color"]').value = profile.color;
      if (name && name.value.trim()) { profile.name = name.value.trim(); saveProfile(); }
    });
  });

  // "Tilpas"
  var nameInput = document.getElementById("custom-name");
  if (nameInput) {
    nameInput.value = profile.name;
    nameInput.addEventListener("input", function () {
      profile.name = nameInput.value.trim();
      saveProfile();
      document.querySelectorAll('form[data-profile] input[name="name"]').forEach(function (i) {
        i.value = profile.name;
      });
    });
  }
  function bindPicker(name, key) {
    document.querySelectorAll('input[name="' + name + '"]').forEach(function (radio) {
      radio.checked = radio.value === profile[key];
      radio.addEventListener("change", function () {
        if (!radio.checked) return;
        profile[key] = radio.value;
        saveProfile();
        if (sound) sound.ok();
      });
    });
  }
  bindPicker("avatar-pick", "avatar");
  bindPicker("color-pick", "color");
  paintProfile();

  /* ---------- Menuen og panelerne ---------- */

  var PANELS = { spil: true, tilpas: true, indstillinger: true };

  function show(name, focus) {
    var open = PANELS[name] ? name : "";
    menu.hidden = !!open;
    panels.forEach(function (p) { p.hidden = p.id !== open; });
    start.dataset.view = open || "menu";
    if (!focus) return;
    if (open) {
      var panel = document.getElementById(open);
      var first = panel.querySelector("input:not([type=hidden]), button, select") ||
                  panel.querySelector(".panel__back");
      if (first) first.focus({ preventScroll: true });
    } else {
      var back = items.filter(function (a) { return a.getAttribute("href") === "#" + lastPanel; })[0];
      (back || items[0]).focus({ preventScroll: true });
    }
  }

  var lastPanel = "spil";
  function route(focus) {
    var name = location.hash.replace("#", "");
    if (PANELS[name]) lastPanel = name;
    show(name, focus);
  }

  // Tilbage-knapperne paa panelerne virker, naar menuen er der.
  panels.forEach(function (p) {
    var back = p.querySelector(".panel__back");
    if (back) back.hidden = false;
  });
  start.addEventListener("click", function (e) {
    var back = e.target.closest(".panel__back");
    if (!back) return;
    e.preventDefault();
    history.pushState(null, "", location.pathname + location.search);
    route(true);
    if (sound) sound.ok();
  });

  window.addEventListener("hashchange", function () { route(true); });

  // Piletaster i menuen, Esc i et panel.
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      // Esc i et panel: tilbage til menuen. Lukkede Esc lige tandhjulets
      // panel (common.js), er den brugt.
      if (!e.defaultPrevented && menu.hidden) {
        history.pushState(null, "", location.pathname + location.search);
        route(true);
      }
      return;
    }
    if (menu.hidden) return;
    var at = items.indexOf(document.activeElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      var next = at === -1 ? 0 : (at + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length;
      items[next].focus();
    }
  });
  // Et lille blip naar markeringen flytter sig - som i en konsolmenu.
  items.forEach(function (a) {
    a.addEventListener("focus", function () { if (sound) sound.tick(); });
    // Kun naar musen faktisk flytter sig - ellers tager en mus der staar
    // stille over menuen, markeringen fra tastaturet, naar menuen kommer frem.
    a.addEventListener("mousemove", function () {
      if (document.activeElement !== a) a.focus({ preventScroll: true });
    });
  });

  // Et link med en kode, eller en fejl fra en formular: direkte til "Spil".
  if (!location.hash && start.dataset.open) {
    history.replaceState(null, "", location.pathname + location.search + "#" + start.dataset.open);
  }
  route(false);
  if (menu.hidden === false && !("ontouchstart" in window)) items[0].focus({ preventScroll: true });
})();
