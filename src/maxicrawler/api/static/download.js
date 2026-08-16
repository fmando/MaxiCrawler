/*
 * Keep running transfers' pages current -- and, on the queue, the page that
 * outlives them.
 *
 * The same shape as crawl.js, and for the same reason: both pages arrive
 * complete from the server, and a reload is a working way to follow a transfer.
 * Nothing here decides anything or formats anything -- every value written into
 * the page was already formatted by the code that rendered it, and where to
 * look next was decided by the server too.
 *
 * There may be several transfers at once, so there are several streams, and
 * every field a frame writes into is named by its download's id. Listening to
 * each transfer beats asking the server for the panels on a timer: the panels
 * carry every waiting row with them, and there can be a thousand of those.
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

  var sources = [];
  /* Every stream currently open. They are closed together: one transfer
   * finishing replaces the panels that hold all of them, and a source left
   * running would be writing into elements that are no longer on the page. */

  var settling = false;
  /* Whether the answer to "one of them ended" is already on its way. Two
   * transfers finishing in the same moment are one refresh, not two. */

  function write(field, downloadId, value) {
    var node = document.getElementById(field + "-" + downloadId);
    if (node) {
      node.textContent = value;
    }
  }

  function paint(download) {
    var id = download.download_id;
    write("download-transferred", id, download.transferred);
    write("download-bytes", id, download.bytes_written);
    write("download-elapsed", id, download.elapsed);
    write("download-rate", id, download.rate || "—");
    write("download-remaining", id, download.remaining || "—");
    write("download-files", id, download.files_finished);
    write("download-state", id, download.state_label);

    var badge = document.getElementById("download-state-" + id);
    if (badge) {
      badge.className = "badge " + download.state_tone;
    }
    // A transfer whose size nobody stated keeps the indeterminate bar it was
    // rendered with; there is no percentage to write into it.
    if (download.has_total) {
      write("download-percent", id, download.progress_percent);
      var bar = document.querySelector("#download-bar-" + id + " > span");
      if (bar) {
        bar.style.width = download.progress_percent + "%";
      }
    }
  }

  function closeAll() {
    sources.forEach(function (source) {
      source.close();
    });
    sources = [];
  }

  function listen(live, url) {
    var source = new EventSource(url);
    sources.push(source);

    source.addEventListener("progress", function (event) {
      paint(JSON.parse(event.data));
    });

    source.addEventListener("finished", function () {
      ended(live);
    });

    source.addEventListener("error", function () {
      // A dropped connection is not worth a broken page: what is on screen
      // stays true as of the last frame, and a reload picks it up again.
      source.close();
    });
  }

  function ended(live) {
    if (settling) {
      return;
    }
    settling = true;
    closeAll();
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
        settling = false;
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
    var streams = live.querySelectorAll("[data-stream]");
    if (streams.length) {
      Array.prototype.forEach.call(streams, function (node) {
        listen(live, node.dataset.stream);
      });
    } else if (live.dataset.swap && window.fetch) {
      // Somewhere to ask but nothing to listen to, which is the moment between
      // two transfers: the one that ended is off the queue and the next has not
      // been picked up. Ask again rather than give up -- the answer either
      // carries streams or says there is nothing left, and both end this.
      window.setTimeout(function () {
        swap(live);
      }, BETWEEN_TRANSFERS);
    }
  }

  if (typeof EventSource !== "undefined") {
    follow();
  }
})();
