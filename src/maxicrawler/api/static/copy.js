/*
 * Put a field's contents on the clipboard.
 *
 * Two pages want this, for the same reason. A browser cannot open a file
 * manager -- a `file://` link from an `http://` page is blocked, and asking the
 * server to launch one would mean a web page starting a local program. Nor can
 * it run a maintenance script, and there the refusal is deliberate rather than
 * the browser's. Copying is the honest version of both buttons.
 *
 * Every `button.copy` on the page is wired, not the first one: the maintenance
 * page carries a line per run. Each names its field by id, which is what makes
 * a page-wide lookup right rather than merely convenient -- an id is the one
 * selector that cannot match a neighbour's field.
 *
 * The buttons are rendered hidden and revealed here, so a browser without
 * scripting or without a clipboard API shows none that cannot work. The fields
 * are selectable either way.
 */
(function () {
  "use strict";

  if (!navigator.clipboard) {
    return;
  }

  var buttons = document.querySelectorAll("button.copy");
  Array.prototype.forEach.call(buttons, function (button) {
    var field = document.querySelector(button.dataset.copy);
    if (!field) {
      return;
    }

    button.hidden = false;
    button.addEventListener("click", function () {
      navigator.clipboard.writeText(field.value).then(
        function () {
          button.textContent = "Copied";
          window.setTimeout(function () {
            button.textContent = "Copy";
          }, 1500);
        },
        function () {
          // Denied, or no permission in this context. The field is still there
          // to select by hand, so saying so once is enough.
          button.textContent = "Copy failed";
        }
      );
    });
  });
})();
