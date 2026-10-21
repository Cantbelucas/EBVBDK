/* Songwars - admin.

   Mens et beat hentes fra YouTube, spoerger siden serveren hvert andet
   sekund, hvordan det gaar. Naar en hentning er faerdig, genindlaeses
   siden, saa beatet staar i listen. Al tekst saettes med textContent. */

(function () {
  "use strict";

  var box = document.getElementById("imports");
  if (!box) return;
  var list = document.getElementById("imports-list");
  var url = box.dataset.url;

  function running() {
    return list.querySelector('[data-status="running"]') !== null;
  }

  function draw(imports) {
    list.textContent = "";
    imports.forEach(function (i) {
      var li = document.createElement("li");
      li.className = "imports__item is-" + i.status;
      li.dataset.id = String(i.id);
      li.dataset.status = i.status;
      var what = document.createElement("span");
      what.className = "imports__what";
      what.textContent = (i.title || i.url) + (i.genre ? " · " + i.genre : "");
      var msg = document.createElement("span");
      msg.className = "imports__msg";
      msg.textContent = i.status === "running" ? "Henter …" : i.message;
      li.appendChild(what);
      li.appendChild(msg);
      list.appendChild(li);
    });
    box.hidden = !imports.length;
  }

  function poll() {
    fetch(url, { credentials: "same-origin", cache: "no-store", headers: { "Accept": "application/json" } })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (data) {
        if (!data) return;
        var before = {};
        list.querySelectorAll("[data-id]").forEach(function (li) { before[li.dataset.id] = li.dataset.status; });
        // Blev en hentning faerdig, siden vi sidst kiggede? Saa staar beatet i listen nu.
        var landed = data.imports.some(function (i) {
          return before[i.id] === "running" && i.status === "done";
        });
        if (landed) { location.reload(); return; }
        draw(data.imports);
      })
      .catch(function () {})
      .then(function () { if (running()) setTimeout(poll, 2000); });
  }

  if (running()) setTimeout(poll, 2000);
})();
