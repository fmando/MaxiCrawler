/*
 * Put a path on the clipboard.
 *
 * A browser cannot open a file manager -- a `file://` link from an `http://`
 * page is blocked, and asking the server to launch one would mean a web page
 * starting a local program. Copying the path is the honest version of that
 * button, and this is the whole of it.
 *
 * The button is rendered hidden and revealed here, so a browser without
 * scripting or without a clipboard API shows no button that cannot work. The
 * field beside it is selectable either way.
 */
(function () {
  "use strict";

  var button = document.querySelector("button.copy");
  if (!button || !navigator.clipboard) {
    return;
  }

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
        // Denied, or no permission in this context. The field is still there to
        // select by hand, so saying so once is enough.
        button.textContent = "Copy failed";
      }
    );
  });
})();
