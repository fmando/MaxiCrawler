"""The HTTP delivery layer: MaxiCrawler's web interface.

A second client of the same services the command line uses, and nothing more.
It runs no crawl of its own, holds no crawl logic, and contains no copy of
anything in :mod:`maxicrawler.cli`. Everything it can do, it does through
:class:`maxicrawler.app.CrawlService`.

Two rules hold this in place, and both are asserted by
``tests/test_api_boundaries.py`` rather than trusted:

1. **This package never imports** ``providers``, ``downloader`` or ``library``.
   When the library view becomes real it will go through a service in
   :mod:`maxicrawler.app`, for the same reason crawling does.
2. **No core package imports this one.** ``config``, ``domain``, ``crawler``,
   ``web``, ``database``, ``app``, ``plugins``, ``providers``, ``downloader``
   and ``library`` build and run without it, because it is an optional
   delivery layer and they are not.

   :mod:`maxicrawler.cli` is the one exception, and not a grudging one: it
   carries the ``serve`` command, so the program's entry point is precisely the
   place that has to know this layer exists. It imports
   :mod:`maxicrawler.api.errors` only — the module that names the missing
   extra, which by definition must be readable on an installation that has not
   got it.

Importing this module never fails. The optional ``web`` extra is only required
once something is actually built, so a caller can ask whether the interface is
available and be told in a sentence:

    >>> from maxicrawler.api import create_app
    Traceback (most recent call last):
    maxicrawler.api.errors.WebDependencyError: the web interface needs ...
"""

from typing import TYPE_CHECKING, Any

from maxicrawler.api.errors import WebDependencyError, WebInterfaceError

if TYPE_CHECKING:
    from maxicrawler.api.application import create_app

__all__ = ["WebDependencyError", "WebInterfaceError", "create_app"]


def __getattr__(name: str) -> Any:
    """Import the application lazily, so this package needs no extra to import.

    Without this, ``import maxicrawler.api`` would drag in Starlette, and a
    core package or a test that only wants to look at the boundary would fail
    on an installation that never asked for the web interface.
    """
    if name == "create_app":
        from maxicrawler.api.application import create_app

        return create_app
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
