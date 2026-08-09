"""Failures of the HTTP delivery layer.

None of them is about a crawl or a download: those fail through
:mod:`maxicrawler.web.errors` and through a reported outcome respectively, and
the interface reports what happened rather than inheriting it.

This module imports nothing optional, deliberately. The one message here
describes an installation that cannot import the web interface, so it has to
be readable by exactly that installation.
"""

MISSING_EXTRA = (
    "the web interface needs the optional 'web' extra.\n"
    "Install it with:  uv sync --extra web\n"
    "or:               pip install 'maxicrawler[web]'"
)
"""What to say when the web dependencies are not installed."""


class WebInterfaceError(RuntimeError):
    """Base class for every failure of the web interface itself."""


class WebDependencyError(WebInterfaceError):
    """Raised when the optional ``web`` extra is not installed.

    The message names the package set and the command that installs it, the
    same courtesy :class:`~maxicrawler.providers.errors.ProviderDependencyError`
    extends for the ``mega`` extra. Failing on an import line with
    ``ModuleNotFoundError: starlette`` would be technically accurate and
    useless.
    """


class DownloadBusyError(WebInterfaceError):
    """Raised when a download is asked for while one is already running.

    This interface runs one transfer at a time and has no queue, which is a
    decision rather than a limitation: a queue needs a policy for ordering,
    cancelling, resuming and surviving a restart, and none of that is worth
    inventing before a single download works end to end. Saying "one at a time,
    here is the one that is running" is the honest answer until then.
    """
