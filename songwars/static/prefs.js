/* Songwars - indstillinger der skal gaelde, foer siden tegnes.

   Indlaeses i <head> uden defer, saa "Animationer: fra" gaelder fra
   foerste billede - ellers naar tingene at bevaege sig et ojeblik. Resten
   af indstillingerne ligger i common.js. */
(function () {
  try {
    var p = JSON.parse(localStorage.getItem("sw-prefs") || "{}");
    document.documentElement.dataset.motion = p.motion === false ? "off" : "on";
  } catch (e) {}
})();
