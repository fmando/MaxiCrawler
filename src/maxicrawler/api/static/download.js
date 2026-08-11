/*
 * Keep a running transfer's page current -- and, on the queue, the page that
 * outlives it.
 *
 * The same shape as crawl.js, and for the same reason: both pages arrive
 * complete from the server, and a reload is a working way to follow a transfer.
 * Nothing here decides anything or formats anything -- every value written into
 * the page was already formatted by the code that rendered it, and where to
 * look next was decided by the server too.
 *
 * What a *finished* transfer means differs between the two pages that load
 * this, and the server says which by writing `data-swap` or leaving it out.
 *
 * On one download's page there is more to say about a finished transfer than a
 * running one -- where the file went, and the way to the library -- so the page
 * is asked for again, once, and that is the end of it.
 *
 * On the queue the transfer that ended is one of a batch, and the page is not
 * over: the next file is already starting. Asking for the whole page there cost
 * one page load per file, which over two hundred files is two hundred scroll
 * positions somebody has to find again. So the queue asks for its panels alone
 * and puts them where the old ones were. The page under them never moves.
 */
(function () {
  "use strict";

  var BETWEEN_TRANSFERS = 1000;
  /* How long to wait before asking again when there is nothing to listen to.
   * Only reached in the gap between two transfers, which is over in a moment;
   * this is what keeps that moment from ending the batch. */

  function write(id, value) {
    var node = document.getElementById(id);
    if (node) {
      node.textContent = value;
    }
  }

  function paint(download) {
    write("download-transferred", download.transferred);
    write("download-bytes", download.bytes_written);
    write("download-elapsed", download.elapsed);
    write("download-rate", download.rate || "—");
    write("download-remaining", download.remaining || "—");
    write("download-files", download.files_finished);
    write("download-state", download.state_label);

    var badge = document.getElementById("download-state");
    if (badge) {
      badge.className = "badge " + download.state_tone;
    }
    // A transfer whose size nobody stated keeps the indeterminate bar it was
    // rendered with; there is no percentage to write into it.
    if (download.has_total) {
      write("download-percent", download.progress_percent);
      var bar = document.querySelector("#download-bar > span");
      if (bar) {
        bar.style.width = download.progress_percent + "%";
      }
    }
  }

  function listen(live) {
    var source = new EventSource(live.dataset.stream);

    source.addEventListener("progress", function (event) {
      paint(JSON.parse(event.data));
    });

    source.addEventListener("finished", function () {
      source.close();
      ended(live);
    });

    source.addEventListener("error", function () {
      // A dropped connection is not worth a broken page: what is on screen
      // stays true as of the last frame, and a reload picks it up again.
      source.close();
    });
  }

  function ended(live) {
    if (live.dataset.swap && window.fetch) {
      swap(live);
    } else {
      window.location.reload();
    }
  }

  function swap(live) {
    window
      .fetch(live.dataset.swap, { headers: { Accept: "text/html" } })
      .then(function (response) {
        if (!response.ok) {
          throw new Error(String(response.status));
        }
        return response.text();
      })
      .then(function (html) {
        var region = document.getElementById(live.dataset.into);
        if (!region) {
          throw new Error("nowhere to put it");
        }
        region.innerHTML = html;
        follow();
      })
      .catch(function () {
        // Whatever went wrong, what is on screen is now out of date, and the
        // reload this replaces is still a working way to fix that.
        window.location.reload();
      });
  }

  function follow() {
    var live = document.getElementById("download-live");
    if (!live) {
      // Nothing running and nothing waiting: the page is as final as it gets.
      return;
    }
    if (live.dataset.stream) {
      listen(live);
    } else if (live.dataset.swap && window.fetch) {
      // Somewhere to ask but nothing to listen to, which is the moment between
      // two transfers: the one that ended is off the queue and the next has not
      // been picked up. Ask again rather than give up -- the answer either
      // carries a stream or says there is nothing left, and both end this.
      window.setTimeout(function () {
        swap(live);
      }, BETWEEN_TRANSFERS);
    }
  }

  if (typeof EventSource !== "undefined") {
    follow();
  }
})();
