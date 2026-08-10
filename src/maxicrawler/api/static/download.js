/*
 * Keep a running download's page current.
 *
 * The same shape as crawl.js, and for the same reason: the page arrives
 * complete from the server and a reload is a working way to follow a transfer.
 * Nothing here decides anything or formats anything -- every value written into
 * the page was already formatted by the code that rendered it.
 */
(function () {
  "use strict";

  var live = document.getElementById("download-live");
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
    var download = JSON.parse(event.data);
    write("download-transferred", download.transferred);
    write("download-bytes", download.bytes_written);
    write("download-elapsed", download.elapsed);
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
  });

  source.addEventListener("finished", function () {
    // The server has more to say about a finished download than a running one
    // -- where the file went, and the way to the library -- so ask it again
    // rather than assembling that here.
    source.close();
    window.location.reload();
  });

  source.addEventListener("error", function () {
    // A dropped connection is not worth a broken page: what is on screen stays
    // true as of the last frame, and a reload picks it up again.
    source.close();
  });
})();
