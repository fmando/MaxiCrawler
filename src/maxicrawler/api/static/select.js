/*
 * Tick every row of the page at once, and say how many are ticked.
 *
 * An extra, in the sense ADR-023 means: the table already works without this.
 * Two hundred boxes can be ticked by hand, and the button beside them queues
 * whatever was ticked either way. What this removes is the two hundred clicks.
 *
 * The controls are rendered hidden and revealed here, the way copy.js reveals
 * its button: a checkbox that ticks nothing and a counter that never counts are
 * worse than no checkbox and no counter.
 *
 * Nothing here decides anything or formats anything -- the same rule crawl.js
 * and download.js follow. The words "selected" and "Select every link on this
 * page" were written by the server; this only writes a number between one and
 * the page size into a node the server put there. The interface fixes that size
 * at two hundred, so the number never reaches the width where the rest of the
 * page would start grouping digits and this would not.
 *
 * "Every link on this page" is meant exactly. The other button queues every
 * link the *filter* matches, which is a different set and is resolved on the
 * server; this one is the rows you can see. The library's own selection means
 * the same thing about the files it shows.
 */
(function () {
  "use strict";

  // Found by a marker attribute rather than by name and form id, because two
  // pages have a selection now and they tick different things: a report ticks
  // links to queue, the library ticks files to judge. What they share is the
  // arrangement -- boxes joined to one form by id -- and that is what this is
  // about, so the marker names the arrangement instead of either page.
  var boxes = Array.prototype.slice.call(
    document.querySelectorAll('input[type="checkbox"][data-tick]')
  );
  if (!boxes.length) {
    return;
  }

  var all = document.querySelector("input.tick-all");
  var chosen = document.querySelector(".chosen");
  var count = chosen ? chosen.querySelector(".num") : null;

  function ticked() {
    var total = 0;
    for (var i = 0; i < boxes.length; i++) {
      if (boxes[i].checked) {
        total++;
      }
    }
    return total;
  }

  function refresh() {
    var chosenCount = ticked();
    if (all) {
      // Indeterminate is the honest third state: "some" is neither the box
      // that would tick everything nor the one that would clear it.
      all.checked = chosenCount === boxes.length;
      all.indeterminate = chosenCount > 0 && chosenCount < boxes.length;
    }
    if (count) {
      count.textContent = String(chosenCount);
      // Nothing ticked is nothing to say, the same rule the queue's own strip
      // in the top bar follows.
      chosen.hidden = chosenCount === 0;
    }
  }

  if (all) {
    all.hidden = false;
    all.addEventListener("change", function () {
      for (var i = 0; i < boxes.length; i++) {
        boxes[i].checked = all.checked;
      }
      refresh();
    });
  }

  for (var i = 0; i < boxes.length; i++) {
    boxes[i].addEventListener("change", refresh);
  }

  // Once at the start, because a browser restores ticked boxes when somebody
  // comes back to this page -- and a header checkbox that disagreed with the
  // rows under it would be the one thing this must never be.
  refresh();
})();
