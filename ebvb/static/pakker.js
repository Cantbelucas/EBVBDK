/* EBVB - vaelg numre til en pakke.

   Siden virker uden denne fil: det er en almindelig formular med en
   afkrydsning pr. nummer. Her kommer soegning, filter paa sektion,
   "Vaelg hele uploaden" og en taeller der siger hvad der sker. */

(function () {
  "use strict";

  var form = document.getElementById("pack-add");
  if (!form) return;

  var rows = Array.prototype.slice.call(form.querySelectorAll("tr[data-row]"));
  var groups = Array.prototype.slice.call(form.querySelectorAll("tbody[data-group]"));
  var filter = document.getElementById("add-filter");
  var sectionPick = document.getElementById("add-section");
  var note = document.getElementById("add-note");
  var go = document.getElementById("add-go");

  function boxOf(row) {
    return row.querySelector('input[name="track"]');
  }

  function plural(n, one, many) {
    return n + " " + (n === 1 ? one : many);
  }

  function count() {
    var picked = rows.filter(function (row) { return boxOf(row).checked; });
    var moving = picked.filter(function (row) { return boxOf(row).dataset.moves; }).length;
    var hiddenPicked = picked.filter(function (row) { return row.hidden; }).length;

    if (!picked.length) {
      note.textContent = "Ingen valgt";
    } else {
      note.textContent = plural(picked.length, "nummer", "numre") + " valgt" +
        (moving ? " · " + moving + " flyttes fra en anden pakke" : "") +
        // Filteret skjuler kun raekker - valgte numre kommer stadig med.
        (hiddenPicked ? " · " + hiddenPicked + " af dem er skjult af søgningen" : "");
    }
    note.classList.toggle("is-warn", moving > 0);
    go.disabled = !picked.length;
    go.textContent = picked.length
      ? "Læg " + plural(picked.length, "nummer", "numre") + " i pakken"
      : "Læg i pakken";
  }

  function apply() {
    var words = filter.value.toLowerCase().trim().split(/\s+/).filter(Boolean);
    var section = sectionPick.value;

    rows.forEach(function (row) {
      var text = row.dataset.search;
      var match = (!section || row.dataset.section === section) &&
        words.every(function (w) { return text.indexOf(w) !== -1; });
      row.hidden = !match;
    });

    // En gruppe uden synlige raekker skal heller ikke vise sin overskrift.
    groups.forEach(function (group) {
      var head = group.querySelector("[data-group-head]");
      if (!head) return;
      head.hidden = !Array.prototype.some.call(
        group.querySelectorAll("tr[data-row]"), function (row) { return !row.hidden; });
    });

    count();
  }

  groups.forEach(function (group) {
    var btn = group.querySelector("[data-select-group]");
    if (!btn) return;
    var boxes = Array.prototype.slice.call(group.querySelectorAll('input[name="track"]'));

    function paint() {
      var all = boxes.every(function (b) { return b.checked; });
      btn.textContent = all ? "Fravælg uploaden" : "Vælg hele uploaden";
      btn.setAttribute("aria-pressed", all ? "true" : "false");
    }

    btn.addEventListener("click", function () {
      var all = boxes.every(function (b) { return b.checked; });
      boxes.forEach(function (b) { b.checked = !all; });
      paint();
      count();
    });
    boxes.forEach(function (b) { b.addEventListener("change", paint); });
    paint();
  });

  rows.forEach(function (row) { boxOf(row).addEventListener("change", count); });
  filter.addEventListener("input", apply);
  sectionPick.addEventListener("change", apply);

  // Forvalgte numre (fra mappe-uploaden) skal kunne ses med det samme.
  var first = rows.filter(function (row) { return boxOf(row).checked; })[0];
  if (first) first.scrollIntoView({ block: "center" });

  count();
})();
