/*
 * The keyboard, on one stored file's page.
 *
 * An extra in the sense ADR-023 means: every key here presses a control that is
 * already on the page and already works with a mouse. Nothing is reachable only
 * by keyboard, nothing is decided here, and nothing is formatted here -- the
 * words on the buttons and the destinations of the links were written by the
 * server (ADR-038). What this removes is the travel between a picture and four
 * small buttons, three hundred times in a sitting.
 *
 *   ← →   the previous and next file of the listing being walked
 *   k     keep       i  ignore       x  discard       f  the star
 *   Enter open the file on its own
 *
 * A key that names a control the page does not have does nothing, which is how
 * the same script serves a file with a walk and a file without one, and a
 * discarded entry that has no "open" link.
 */
(function () {
  "use strict";

  var KEYS = {
    k: 'button[name="verdict"][value="kept"]',
    i: 'button[name="verdict"][value="ignored"]',
    x: 'button[name="verdict"][value="discarded"]',
    f: 'button[name="favourite"]',
    ArrowLeft: 'a[data-walk="previous"]',
    ArrowRight: 'a[data-walk="next"]',
    Enter: "a[data-open]"
  };

  function typing(target) {
    // Somebody selecting the path field is typing, not judging. The copy field
    // is the only input on this page, and it is the reason this check exists.
    if (!target || !target.tagName) {
      return false;
    }
    var tag = target.tagName.toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select" || target.isContentEditable;
  }

  document.addEventListener("keydown", function (event) {
    // A modifier means the browser's own shortcut -- Ctrl+F is find, not
    // favourite -- so nothing here may claim one.
    if (event.altKey || event.ctrlKey || event.metaKey || typing(event.target)) {
      return;
    }
    var selector = KEYS[event.key];
    if (!selector) {
      return;
    }
    var control = document.querySelector(selector);
    if (!control) {
      return;
    }
    event.preventDefault();
    control.click();
  });
})();
