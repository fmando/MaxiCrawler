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


class QueueFullError(WebInterfaceError):
    """Raised when a download is asked for and the queue has no room.

    A ceiling rather than an unbounded backlog, because the queue lives in
    memory and because one click will soon be able to add several thousand
    entries to it (the report's Download-selected button). The message names
    the limit and what is already waiting, so the answer to it is to let some
    of that finish rather than to guess.

    It replaced ``DownloadBusyError``, which said "one at a time, here is the
    one that is running" — true until Sprint 15 and, since ADR-033, no longer
    a thing this interface does.
    """
