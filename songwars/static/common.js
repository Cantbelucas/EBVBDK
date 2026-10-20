/* Songwars - det der bruges paa alle sider.

   CSP'en tillader ingen inline-scripts, saa der staar ingen onsubmit= eller
   onclick= i HTML'en. I stedet:

     <form data-confirm="Sikker?">   spoerger foer formularen sendes
     <form data-busy="Sender …">     laaser knappen mens den sendes
     <button data-copy="tekst">      kopierer teksten                     */

(function () {
  "use strict";

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
    var label = btn.textContent;

    function done(ok) {
      btn.textContent = ok ? "Kopieret" : "Kopiér selv: " + text;
      setTimeout(function () { btn.textContent = label; }, ok ? 1600 : 6000);
    }

    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(function () { done(true); },
                                              function () { done(false); });
    } else {
      done(false);
    }
  });

  /* Kun et nummer ad gangen. Starter man et nyt, stopper det forrige. */
  document.addEventListener("play", function (event) {
    document.querySelectorAll("audio").forEach(function (a) {
      if (a !== event.target && !a.paused) a.pause();
    });
  }, true);
})();
