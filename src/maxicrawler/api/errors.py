"""Failures of the HTTP delivery layer.

Only two, and neither is about a crawl: a crawl that fails does so through
:mod:`maxicrawler.web.errors`, and the interface reports it rather than
inheriting it.

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
