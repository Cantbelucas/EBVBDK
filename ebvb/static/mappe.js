/* EBVB - mappe-upload og gennemse-tabellen.

   Upload: man vaelger en mappe, filnavnene sendes til /mappe/tjek, og
   listen vises med det der blev laest ud af dem. Foerst naar man trykker
   Laeg op, sendes filerne - en ad gangen og i bidder, saa en 300 MB wav
   kommer igennem Cloudflares graense paa 100 MB pr. forespoergsel.

   Fremdrift kraever XMLHttpRequest. fetch kan ikke melde hvor langt en
   upload er naaet. */

(function () {
  "use strict";

  var form = document.getElementById("folder");
  if (form) setupUpload(form);

  var review = document.getElementById("review");
  if (review) setupReview(review);

  /* ---------- Smaating ---------- */

  function humanSize(n) {
    if (n >= 1073741824) return (n / 1073741824).toFixed(1).replace(".", ",") + " GB";
    if (n >= 1048576) return Math.round(n / 1048576) + " MB";
    return Math.max(1, Math.round(n / 1024)) + " KB";
  }

  function make(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function randomHex() {
    var bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return Array.prototype.map.call(bytes, function (b) {
      return (b < 16 ? "0" : "") + b.toString(16);
    }).join("");
  }

  function parseJson(text) {
    try { return JSON.parse(text); } catch (e) { return null; }
  }

  function plural(n, one, many) {
    return n + " " + (n === 1 ? one : many);
  }

  function guard(event) {
    event.preventDefault();
    event.returnValue = "";
  }

  /* ============================================================
     Upload
     ============================================================ */

  function setupUpload(form) {
    var checkUrl = form.dataset.check;
    var chunkUrl = form.dataset.chunk;
    var reviewUrl = form.dataset.review;
    var chunkBytes = parseInt(form.dataset.chunkBytes, 10);
    var maxBytes = parseInt(form.dataset.maxBytes, 10);
    var maxLabel = form.dataset.maxLabel;

    var input = document.getElementById("folder-input");
    var radios = Array.prototype.slice.call(form.querySelectorAll('input[name="section"]'));
    var found = document.getElementById("found");
    var sum = document.getElementById("found-sum");
    var tbody = document.querySelector("#found-table tbody");
    var checkAll = document.getElementById("check-all");

    var dupBox = document.getElementById("dup-box");
    var dupText = document.getElementById("dup-text");
    var dupDone = document.getElementById("dup-done");
    var dupButtons = Array.prototype.slice.call(dupBox.querySelectorAll("[data-dup]"));

    var progress = document.getElementById("progress");
    var progressFill = document.getElementById("progress-fill");
    var progressText = document.getElementById("progress-text");

    var doneText = document.getElementById("done-text");
    var startBtn = document.getElementById("start");
    var stopBtn = document.getElementById("stop");
    var retryBtn = document.getElementById("retry");
    var reviewLink = document.getElementById("review-link");

    var packUrl = form.dataset.pack;
    var packAddUrl = form.dataset.packAdd;
    var packOpt = document.getElementById("pack-opt");
    var packOn = document.getElementById("pack-on");
    var packName = document.getElementById("pack-name");
    var packLink = document.getElementById("pack-link");

    var items = [];
    var batch = null;
    var busy = false;
    var stopping = false;
    var current = null;           // XHR der koerer lige nu
    var dupDecision = null;
    var scanToken = 0;
    var packId = null;            // saettes naar serveren har lavet pakken
    var packSent = false;         // navnet er sendt; serveren holder fast i det
    var lastFolder = null;

    // Mappe-vaelgeren findes i alle desktop-browsere, men ikke overalt.
    if (!("webkitdirectory" in input)) {
      form.hidden = false;
      input.disabled = true;
      sum.textContent = "";
      form.querySelector(".field__hint").textContent =
        "Din browser kan ikke vælge en hel mappe. Brug Chrome, Edge, Firefox eller Safari på en computer.";
      return;
    }

    form.hidden = false;

    function section() {
      var picked = radios.filter(function (r) { return r.checked; })[0];
      return picked || radios[0];
    }

    function isAudio(file) {
      return /\.(mp3|wav)$/i.test(file.name) && file.name.indexOf("._") !== 0;
    }

    function pathOf(file) {
      return file.webkitRelativePath || file.name;
    }

    input.addEventListener("change", scan);
    radios.forEach(function (radio) {
      // Dubletter afhaenger af sektionen, saa listen skal tjekkes igen.
      radio.addEventListener("change", function () { if (items.length) scan(); });
    });

    /* Mappens navn er forslaget til pakken. Den oeverste mappe - det er
       den man valgte - ikke undermappen den foerste fil ligger i. */
    function folderName(files) {
      var path = files.length ? files[0].webkitRelativePath || "" : "";
      var top = path.indexOf("/") > 0 ? path.slice(0, path.indexOf("/")) : "";
      return top.replace(/[_]+/g, " ").replace(/\s+/g, " ").trim();
    }

    function wantsPack() {
      return packOn.checked;
    }

    function paintPack() {
      packName.disabled = busy || packSent || !packOn.checked;
      packOn.disabled = busy || packSent;
      packOpt.classList.toggle("is-off", !packOn.checked);
    }

    packOn.addEventListener("change", function () { paintPack(); refresh(); });
    packName.addEventListener("input", refresh);

    /* ---------- 1. Find og tjek ---------- */

    function scan() {
      var files = Array.prototype.slice.call(input.files || []);
      var audio = files.filter(isAudio).sort(function (a, b) {
        return pathOf(a).localeCompare(pathOf(b), "da", { numeric: true });
      });

      var token = ++scanToken;
      items = [];
      batch = null;
      dupDecision = null;
      tbody.textContent = "";
      dupBox.hidden = true;
      progress.hidden = true;
      doneText.textContent = "";
      doneText.dataset.kind = "";
      reviewLink.hidden = true;
      packLink.hidden = true;
      packId = null;
      packSent = false;
      // Skift af sektion koerer ogsaa scan. Saa er det samme mappe, og et
      // navn man selv har rettet, eller en fravalgt pakke, skal blive.
      var folder = folderName(files);
      if (folder !== lastFolder) {
        packOn.checked = true;
        packName.value = folder;
        lastFolder = folder;
      }
      paintPack();
      retryBtn.hidden = true;
      startBtn.hidden = false;
      startBtn.disabled = true;
      checkAll.checked = true;

      if (!files.length) { found.hidden = true; return; }
      found.hidden = false;

      if (!audio.length) {
        sum.textContent = "Der er ingen mp3- eller wav-filer i mappen.";
        return;
      }

      sum.textContent = "Læser " + plural(audio.length, "filnavn", "filnavne") + " …";

      fetch(checkUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          section: section().value,
          files: audio.map(function (f) { return { name: f.name, size: f.size }; })
        })
      })
        .then(function (res) {
          return res.text().then(function (text) { return { res: res, data: parseJson(text) }; });
        })
        .then(function (r) {
          if (token !== scanToken) return;           // en nyere mappe er valgt imens
          if (!r.res.ok || !r.data) {
            sum.textContent = (r.data && r.data.error) ||
              "Kunne ikke tjekke filerne (" + r.res.status + "). Prøv igen.";
            return;
          }
          batch = r.data.batch;
          build(audio, r.data.files);
        })
        .catch(function () {
          if (token !== scanToken) return;
          sum.textContent = "Kunne ikke nå serveren. Tjek forbindelsen, og vælg mappen igen.";
        });
    }

    /* ---------- 2. Listen ---------- */

    function build(files, metas) {
      var seen = {};

      files.forEach(function (file, i) {
        var meta = metas[i];
        if (!meta) return;                            // serveren sagde nej til navnet

        var item = {
          file: file,
          meta: meta,
          state: "ready",
          duplicate: meta.duplicate,
          tooBig: file.size > maxBytes,
          sibling: null
        };

        // To filer med samme navn i hver sin undermappe. Den anden bliver
        // en dublet af den foerste, naar den foerste er lagt op.
        var lower = file.name.toLowerCase();
        if (seen[lower] && !item.duplicate) {
          item.sibling = seen[lower];
          item.duplicate = { title: seen[lower].meta.title, by: null, own: true };
        } else if (!seen[lower]) {
          seen[lower] = item;
        }

        item.row = renderRow(item);
        tbody.appendChild(item.row);
        items.push(item);
      });

      var dups = items.filter(function (it) { return it.duplicate && !it.tooBig; });
      if (dups.length) {
        var foreign = dups.filter(function (it) { return !it.duplicate.own; }).length;
        dupText.textContent =
          plural(dups.length, "nummer findes", "numre findes") + " allerede i " +
          section().dataset.label + ". Hvad skal der ske med " +
          (dups.length === 1 ? "det" : "dem") + "?" +
          (foreign ? " " + plural(foreign, "af dem", "af dem") +
            " er lagt op af en anden og kan ikke overskrives — kun springes over eller lægges op ved siden af." : "");
        dupButtons.forEach(function (b) { b.hidden = false; });
        dupDone.hidden = true;
        dupBox.hidden = false;
      }

      refresh();
    }

    function renderRow(item) {
      var tr = make("tr");
      var meta = item.meta;
      var path = pathOf(item.file);
      var dir = path.slice(0, path.length - item.file.name.length).replace(/\/$/, "");

      var tdCheck = make("td", "grid__check");
      var check = make("input");
      check.type = "checkbox";
      check.checked = !item.tooBig;
      check.disabled = item.tooBig;
      check.setAttribute("aria-label", "Tag med: " + item.file.name);
      check.addEventListener("change", refresh);
      tdCheck.appendChild(check);
      item.check = check;

      var tdFile = make("td", "grid__file");
      tdFile.appendChild(make("span", "grid__name", item.file.name));
      if (dir) tdFile.appendChild(make("span", "grid__dir", dir));

      var tdTitle = make("td", "grid__title", meta.title);
      var tdBpm = make("td", meta.bpm ? "" : "is-blank", meta.bpm || "—");
      var tdKey = make("td", meta.mkey ? "" : "is-blank", meta.mkey || "—");
      var tdSize = make("td", "grid__num", humanSize(item.file.size));

      var tdState = make("td", "grid__state");
      item.stateCell = tdState;

      if (meta.parsed === "ingen") tr.classList.add("is-flagged");
      if (meta.parsed !== "fuld") tr.classList.add("is-missing");

      tr.appendChild(tdCheck);
      tr.appendChild(tdFile);
      tr.appendChild(tdTitle);
      tr.appendChild(tdBpm);
      tr.appendChild(tdKey);
      tr.appendChild(tdSize);
      tr.appendChild(tdState);

      paintReady(item);
      return tr;
    }

    function badgeFor(meta) {
      if (meta.parsed === "fuld") return make("span", "badge badge--ok", "Læst");
      if (meta.parsed === "delvis") return make("span", "badge badge--warn", "Delvist læst");
      return make("span", "badge badge--warn", "Kunne ikke læses");
    }

    function paintReady(item) {
      var cell = item.stateCell;
      cell.textContent = "";

      if (item.tooBig) {
        cell.appendChild(make("span", "badge badge--warn", "Over " + maxLabel));
        return;
      }

      cell.appendChild(badgeFor(item.meta));

      if (item.duplicate) {
        var wrap = make("label", "dup");
        var label = item.sibling
          ? "Samme navn som " + pathOf(item.sibling.file)
          : item.duplicate.own
            ? "Findes allerede"
            : "Findes hos " + item.duplicate.by;
        wrap.appendChild(make("span", "dup__label", label));

        var select = make("select", "dup__pick");
        select.setAttribute("aria-label", label + ": " + item.file.name);
        addOption(select, "skip", "Spring over");
        if (item.duplicate.own) addOption(select, "overwrite", "Overskriv");
        else addOption(select, "keep", "Læg op ved siden af");
        select.value = item.choice || "skip";
        select.addEventListener("change", function () { item.choice = select.value; refresh(); });
        wrap.appendChild(select);
        item.select = select;
        cell.appendChild(wrap);
      }
    }

    function addOption(select, value, text) {
      var option = make("option", "", text);
      option.value = value;
      select.appendChild(option);
    }

    dupButtons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        dupDecision = btn.dataset.dup;
        items.forEach(function (it) {
          if (!it.select) return;
          var canOverwrite = it.duplicate.own;
          it.choice = dupDecision === "overwrite" && canOverwrite ? "overwrite" : "skip";
          it.select.value = it.choice;
        });
        dupButtons.forEach(function (b) { b.hidden = true; });
        dupDone.textContent = dupDecision === "overwrite"
          ? "Dine egne bliver overskrevet. Du kan ændre det for hvert nummer i listen."
          : "De bliver sprunget over. Du kan ændre det for hvert nummer i listen.";
        dupDone.hidden = false;
        refresh();
      });
    });

    checkAll.addEventListener("change", function () {
      items.forEach(function (it) { if (!it.check.disabled) it.check.checked = checkAll.checked; });
      refresh();
    });

    function chosen() {
      return items.filter(function (it) { return it.check.checked && !it.tooBig; });
    }

    /* Hvad der rent faktisk bliver sendt: valgte, minus dubletter der
       springes over. */
    function toSend(list) {
      return list.filter(function (it) { return !(it.duplicate && (it.choice || "skip") === "skip"); });
    }

    function isDone(it) {
      return it.state === "added" || it.state === "overwritten" || it.state === "skipped";
    }

    /* Klar til at blive sendt: valgt, ikke sprunget over, ikke sendt endnu.
       Fejlede og stoppede tager "Proev igen"-knappen sig af. */
    function pending() {
      return toSend(chosen()).filter(function (it) { return it.state === "ready"; });
    }

    function refresh() {
      if (busy) return;
      var picked = chosen().filter(function (it) { return !isDone(it); });
      var sending = pending();
      var bytes = sending.reduce(function (n, it) { return n + it.file.size; }, 0);
      var enabled = items.filter(function (it) { return !it.tooBig && !isDone(it); });

      checkAll.checked = picked.length === enabled.length && enabled.length > 0;
      checkAll.indeterminate = picked.length > 0 && picked.length < enabled.length;

      var parts = [plural(items.length, "lydfil", "lydfiler") + " fundet",
                   picked.length + " valgt"];
      var skipped = toSend(picked).length < picked.length ? picked.length - toSend(picked).length : 0;
      if (skipped) parts.push(skipped + " springes over");
      parts.push(humanSize(bytes) + " skal sendes");
      sum.textContent = parts.join(" · ");

      var undecided = dupBox.hidden === false && dupDecision === null &&
        picked.some(function (it) { return it.duplicate; });

      var unnamed = wantsPack() && !packSent && !packName.value.trim();

      startBtn.hidden = false;
      startBtn.disabled = !batch || !sending.length || undecided || unnamed;
      startBtn.textContent = sending.length ? "Læg " + plural(sending.length, "nummer", "numre") + " op" : "Læg op";
      if (undecided) doneText.textContent = "Vælg først hvad der skal ske med dubletterne.";
      else if (unnamed) doneText.textContent = "Giv pakken et navn, eller fravælg den.";
      else if (doneText.dataset.kind !== "result") doneText.textContent = "";
    }

    /* ---------- 3. Send ---------- */

    startBtn.addEventListener("click", function () { run(pending()); });

    retryBtn.addEventListener("click", function () {
      run(items.filter(function (it) { return it.state === "failed" || it.state === "stopped"; }));
    });

    stopBtn.addEventListener("click", function () {
      stopping = true;
      stopBtn.disabled = true;
      stopBtn.textContent = "Stopper …";
      if (current) current.abort();
    });

    function lock(on) {
      busy = on;
      input.disabled = on;
      checkAll.disabled = on;
      radios.forEach(function (r) { r.disabled = on; });
      items.forEach(function (it) {
        it.check.disabled = on || it.tooBig || isDone(it);
        if (it.select) it.select.disabled = on || isDone(it);
      });
      startBtn.hidden = on;
      stopBtn.hidden = !on;
      stopBtn.disabled = false;
      stopBtn.textContent = "Stop";
      paintPack();
      if (on) {
        window.addEventListener("beforeunload", guard);
        retryBtn.hidden = true;
        reviewLink.hidden = true;
        packLink.hidden = true;
      } else {
        window.removeEventListener("beforeunload", guard);
      }
    }

    function run(queue) {
      if (!queue.length || busy) return;
      stopping = false;
      // Navnet laases fra foerste bid. Serveren laver pakken med det navn
      // den faar foerst, saa et nyt navn ved "Proev igen" ville ikke
      // goere noget - bortset fra at forvirre.
      if (wantsPack()) packSent = true;
      lock(true);
      progress.hidden = false;
      doneText.dataset.kind = "";
      doneText.textContent = "";

      var total = queue.reduce(function (n, it) { return n + it.file.size; }, 0);
      var sentBefore = 0;

      queue.forEach(function (it) { setState(it, "waiting"); });

      function overall(extra) {
        var pct = total ? Math.min(100, ((sentBefore + extra) / total) * 100) : 100;
        progressFill.style.width = pct.toFixed(1) + "%";
      }

      function announce(at) {
        progressText.textContent = "Sender " + Math.min(at + 1, queue.length) + " af " + queue.length +
          " · " + humanSize(sentBefore) + " af " + humanSize(total);
      }

      function next(i) {
        if (i >= queue.length || stopping) {
          queue.slice(i).forEach(function (it) {
            if (it.state === "waiting") setState(it, "stopped");
          });
          finish();
          return;
        }
        var item = queue[i];
        announce(i);
        setState(item, "sending");

        send(item, function (loaded) {
          setProgress(item, loaded);
          overall(loaded);
        }).then(function (result) {
          sentBefore += item.file.size;
          overall(0);
          setState(item, result.state, result.message);
          next(i + 1);
        });
      }

      function finish() {
        lock(false);
        overall(0);
        var count = function (s) { return items.filter(function (it) { return it.state === s; }).length; };
        var added = count("added"), over = count("overwritten"), skip = count("skipped");
        var failed = count("failed"), stopped = count("stopped");

        var bits = [];
        if (added) bits.push(added + " lagt op");
        if (over) bits.push(over + " overskrevet");
        if (skip) bits.push(skip + " sprunget over");
        if (failed) bits.push(failed + " fejlede");
        if (stopped) bits.push(stopped + " ikke sendt");

        progressText.textContent = (stopping ? "Stoppet. " : "Færdig. ") + (bits.join(", ") || "Intet sendt") + ".";
        doneText.dataset.kind = "result";
        doneText.textContent = failed
          ? "De fejlede står markeret i listen med grunden."
          : "";

        retryBtn.hidden = !(failed || stopped);
        retryBtn.textContent = "Prøv igen (" + (failed + stopped) + ")";
        refresh();
        startBtn.hidden = !pending().length;

        if (packId) {
          packLink.href = packUrl.replace("PACK", packId);
          packLink.hidden = false;
          notePackLeftovers();
        } else if (wantsPack() && !(added + over)) {
          doneText.appendChild(document.createTextNode(
            (doneText.textContent ? " " : "") +
            "Der blev ikke lavet en pakke, fordi ingen numre blev lagt op."));
        }

        if (added + over) {
          reviewLink.href = reviewUrl.replace("BATCH", batch);
          reviewLink.hidden = false;
          reviewLink.focus();
        }
      }

      /* Dine egne numre der fandtes i forvejen og blev sprunget over, kom
         ikke med i pakken - der blev jo ikke sendt noget. Mappen var dog
         tydeligvis meningen, saa tilbyd at laegge dem i bagefter. */
      function notePackLeftovers() {
        var left = chosen().filter(function (it) {
          return it.duplicate && it.duplicate.id && it.duplicate.own && !it.sibling &&
            (it.choice || "skip") === "skip";
        });
        if (!left.length) return;

        var moving = left.filter(function (it) { return it.duplicate.pack; }).length;
        doneText.appendChild(document.createTextNode(
          (doneText.textContent ? " " : "") +
          plural(left.length, "nummer", "numre") + " fandtes allerede og kom ikke med i pakken" +
          (moving ? " (" + moving + " ligger i en anden pakke)" : "") + ". "));

        var link = make("a", "spec__link", "Læg " + (left.length === 1 ? "det" : "dem") + " i pakken");
        // Over et par hundrede id'er bliver adressen for lang til nginx.
        // Saa aabnes siden uden forvalg.
        link.href = packAddUrl.replace("PACK", packId) + (left.length <= 100
          ? "?vaelg=" + left.map(function (it) { return it.duplicate.id; }).join(",")
          : "");
        doneText.appendChild(link);
      }

      next(0);
    }

    /* En fil, i bidder. Loeser altid - en fejl er et resultat, ikke en
       exception, saa koeen fortsaetter med den naeste fil.

       uploadId og hvor langt serveren er naaet, gemmes paa nummeret. Saa
       fortsaetter "Proev igen" hvor den slap, i stedet for at sende de
       foerste 200 MB igen. Er den halve fil vaek paa serveren, svarer den
       409 med have=0, og saa starter den forfra af sig selv. */
    function send(item, onProgress) {
      return new Promise(function (resolve) {
        var file = item.file;
        var size = file.size;
        var uploadId = item.uploadId || (item.uploadId = randomHex());
        var offset = item.have || 0;
        var retries = 0;
        var choice = item.duplicate ? (item.choice || "skip") : "skip";

        function chunk() {
          if (stopping) { resolve({ state: "stopped" }); return; }

          var end = Math.min(size, offset + chunkBytes);
          var body = new FormData();
          body.append("upload", uploadId);
          body.append("batch", batch);
          body.append("section", section().value);
          body.append("name", file.name);
          body.append("size", String(size));
          body.append("offset", String(offset));
          body.append("duplicate", choice);
          body.append("pack_name", wantsPack() ? packName.value.trim() : "");
          body.append("part", file.slice(offset, end), "part");

          var xhr = new XMLHttpRequest();
          current = xhr;
          xhr.open("POST", chunkUrl);

          xhr.upload.onprogress = function (e) {
            if (!e.lengthComputable || !e.total) return;
            // e.total er hele formularen; skaleres ned til bidden.
            onProgress(offset + (e.loaded / e.total) * (end - offset));
          };

          xhr.onload = function () {
            current = null;
            var data = parseJson(xhr.responseText);

            if (xhr.status === 200 && data) {
              retries = 0;
              if (data.status === "part") {
                offset = item.have = data.have;
                onProgress(offset);
                chunk();
              } else {
                item.uploadId = null;
                item.have = 0;
                if (data.pack) packId = data.pack;
                onProgress(size);
                resolve({ state: data.status });
              }
              return;
            }

            if (xhr.status === 409 && data && typeof data.have === "number" && retries < 5) {
              retries += 1;
              offset = item.have = data.have;     // fortsaet hvor serveren er
              chunk();
              return;
            }

            resolve({ state: "failed", message: explain(xhr.status, data) });
          };

          xhr.onerror = function () {
            current = null;
            if (retries < 3) {
              retries += 1;
              setTimeout(chunk, 1500 * retries);
              return;
            }
            resolve({ state: "failed", message: "Forbindelsen blev afbrudt." });
          };

          xhr.onabort = function () {
            current = null;
            resolve({ state: "stopped" });
          };

          xhr.send(body);
        }

        chunk();
      });
    }

    function explain(status, data) {
      if (data && data.error) return data.error;
      if (status === 413) return "Afvist som for stor af nginx eller Cloudflare (413).";
      if (status === 502 || status === 503 || status === 504) return "Serveren svarede ikke (" + status + ").";
      return "Fejl fra serveren (" + (status || "ingen forbindelse") + ").";
    }

    var LABELS = {
      waiting: "Venter",
      sending: "Sender",
      added: "Lagt op",
      overwritten: "Overskrevet",
      skipped: "Sprunget over",
      failed: "Fejlede",
      stopped: "Ikke sendt"
    };

    function setState(item, state, message) {
      item.state = state;
      var cell = item.stateCell;
      cell.textContent = "";
      item.row.dataset.state = state;

      var kind = state === "added" || state === "overwritten" ? "badge--ok"
        : state === "failed" ? "badge--warn" : "";
      cell.appendChild(make("span", "badge " + kind, LABELS[state]));

      if (state === "sending") {
        var meter = make("span", "meter");
        meter.setAttribute("role", "progressbar");
        meter.setAttribute("aria-label", "Sender " + item.file.name);
        meter.setAttribute("aria-valuemin", "0");
        meter.setAttribute("aria-valuemax", "100");
        meter.setAttribute("aria-valuenow", "0");
        var fill = make("span", "meter__fill");
        meter.appendChild(fill);
        cell.appendChild(meter);
        item.meter = meter;
        item.fill = fill;
        item.pct = make("span", "meter__pct", "0 %");
        cell.appendChild(item.pct);
      } else {
        item.meter = item.fill = item.pct = null;
      }

      if (message) cell.appendChild(make("span", "grid__why", message));
    }

    function setProgress(item, loaded) {
      if (!item.fill) return;
      var pct = Math.min(100, Math.round((loaded / item.file.size) * 100));
      item.fill.style.width = pct + "%";
      item.meter.setAttribute("aria-valuenow", String(pct));
      item.pct.textContent = pct + " %";
    }
  }

  /* ============================================================
     Gennemse
     ============================================================ */

  function setupReview(form) {
    var rows = Array.prototype.slice.call(form.querySelectorAll("tr[data-row]"));
    var onlyMissing = document.getElementById("only-missing");
    var dirtyNote = document.getElementById("dirty");
    var missingCount = form.querySelector("[data-missing]");
    var dirty = 0;
    var changed = new Set();

    function paint(row) {
      var bpm = row.querySelector('input[name^="bpm_"]');
      var key = row.querySelector('input[name^="mkey_"]');
      bpm.classList.toggle("is-empty", !bpm.value.trim());
      key.classList.toggle("is-empty", !key.value.trim());
      row.classList.toggle("is-missing", !bpm.value.trim() || !key.value.trim());
    }

    function filter() {
      rows.forEach(function (row) {
        row.hidden = onlyMissing.checked && !row.classList.contains("is-missing") &&
          !row.classList.contains("is-flagged");
      });
    }

    rows.forEach(function (row) {
      row.querySelectorAll("input.cell").forEach(function (field) {
        var original = field.value;
        field.addEventListener("input", function () {
          if (field.value !== original) changed.add(field); else changed.delete(field);
          paint(row);
          if (missingCount) {
            missingCount.textContent = String(rows.filter(function (r) {
              return r.classList.contains("is-missing");
            }).length);
          }
          dirty = changed.size;
          dirtyNote.textContent = dirty ? plural(dirty, "ændring", "ændringer") + " er ikke gemt" : "";
          if (dirty) window.addEventListener("beforeunload", guard);
          else window.removeEventListener("beforeunload", guard);
        });
      });
    });

    onlyMissing.addEventListener("change", filter);

    form.addEventListener("submit", function () {
      window.removeEventListener("beforeunload", guard);
    });
  }
})();
