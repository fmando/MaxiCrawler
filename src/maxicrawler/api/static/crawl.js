/*
 * Keep a running crawl's page current.
 *
 * Everything below is an enhancement. The page arrives complete from the
 * server and a reload is a working way to follow a crawl; this only spares
 * you the reloading. Nothing here decides anything or formats anything --
 * every value written into the page was already formatted by the same code
 * that rendered it in the first place.
 */
(function () {
  "use strict";

  var live = document.getElementById("crawl-live");
  if (!live || typeof EventSource === "undefined") {
    return;
  }

  var source = new EventSource(live.dataset.stream);

  function write(id, value) {
    var node = document.getElementById(id);
    if (node) {
      node.textContent = value;
    }
  }

  source.addEventListener("progress", function (event) {
    var crawl = JSON.parse(event.data);
    write("pages-visited", crawl.pages_visited);
    write("pages-failed", crawl.pages_failed);
    write("pages-attempted", crawl.pages_attempted);
    write("links-found", crawl.links_found);
    write("elapsed", crawl.elapsed);
    write("latest-url", crawl.latest_url || "\u2014");
    write("progress-percent", crawl.progress_percent);
    write("state", crawl.state_label);

    var badge = document.getElementById("state");
    if (badge) {
      badge.className = "badge " + crawl.state_tone;
    }
    var bar = document.querySelector(".bar > span");
    if (bar) {
      bar.style.width = crawl.progress_percent + "%";
    }
  });

  source.addEventListener("finished", function () {
    // The server has more to say about a finished crawl than a running one,
    // so ask it again rather than assembling a report here.
    source.close();
    window.location.reload();
  });

  source.addEventListener("error", function () {
    // A dropped connection is not worth a broken page: the values on screen
    // stay true as of the last frame, and a reload picks it up again.
    source.close();
  });
})();
