"""Failures of the HTTP delivery layer.

Only two, and neither is about a crawl: a crawl that fails does so through
:mod:`maxicrawler.web.errors`, and the interface reports it rather than
inheriting it.
"""


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
