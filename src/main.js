/* Progressive enhancement. Siden virker fuldt ud uden denne fil -
   alt indhold er allerede i HTML. Her tilfoejes kun filtrering af
   projekter og markering af aktivt menupunkt. */
(function () {
  'use strict';

  /* ---------- Filtrering af projekter ---------- */

  var filters = document.getElementById('filters');
  var count = document.getElementById('filters-count');
  var grid = document.getElementById('projects');
  var cards = grid ? [].slice.call(grid.querySelectorAll('.project')) : [];

  if (filters && count && grid && cards.length > 1) {
    var chips = [].slice.call(filters.querySelectorAll('.chip'));

    var empty = document.createElement('p');
    empty.className = 'projects__empty';
    empty.hidden = true;
    empty.textContent = 'Ingen projekter matcher det filter.';
    grid.parentNode.insertBefore(empty, grid.nextSibling);

    var apply = function (value, label) {
      var shown = 0;

      cards.forEach(function (card) {
        var tags = (card.getAttribute('data-tags') || '').split('|');
        var match = value === '*' || tags.indexOf(value) !== -1;
        card.hidden = !match;
        if (match) { shown += 1; }
      });

      chips.forEach(function (chip) {
        chip.setAttribute('aria-pressed', String(chip.dataset.filter === value));
      });

      empty.hidden = shown > 0;
      count.textContent = value === '*'
        ? 'Viser alle ' + cards.length + ' projekter'
        : 'Viser ' + shown + ' af ' + cards.length + ' projekter · ' + label;
    };

    chips.forEach(function (chip) {
      chip.addEventListener('click', function () {
        apply(chip.dataset.filter, chip.textContent.trim());
      });
    });

    filters.hidden = false;
    apply('*', '');
  }

  /* ---------- Aktivt menupunkt ---------- */

  if ('IntersectionObserver' in window) {
    var links = {};
    [].forEach.call(document.querySelectorAll('.nav a[href^="#"]'), function (a) {
      links[a.getAttribute('href').slice(1)] = a;
    });

    var sections = Object.keys(links)
      .map(function (id) { return document.getElementById(id); })
      .filter(Boolean);

    if (sections.length) {
      var visible = Object.create(null);

      var observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          visible[entry.target.id] = entry.isIntersecting;
        });

        // Den sidste synlige sektion i dokumentrækkefølge er den
        // der ligger øverst i visningsfeltet.
        var current = null;
        sections.forEach(function (section) {
          if (visible[section.id]) { current = section.id; }
        });

        Object.keys(links).forEach(function (id) {
          links[id].classList.toggle('is-current', id === current);
        });
      }, { rootMargin: '-70px 0px -55% 0px', threshold: 0 });

      sections.forEach(function (section) { observer.observe(section); });
    }
  }
}());
