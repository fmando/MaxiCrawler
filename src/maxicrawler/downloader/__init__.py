"""The download layer: how downloads are executed.

The provider layer in :mod:`maxicrawler.providers` answers *"what can I do with
this resource?"* and the library layer in :mod:`maxicrawler.library` answers
*"where does what we fetched live?"*. This layer sits between them and answers
*"how are downloads executed?"*: it turns a source into URLs, URLs into jobs,
drains a queue, and hands each resource to whichever provider claims it.

Nothing in it names a provider. Where behaviour differs between hosts it is
asked for through the provider protocol or declared through
:class:`~maxicrawler.domain.providers.ProviderCapability`, so a new provider
plugs in without a line changing here.
"""

from maxicrawler.downloader.control import DownloadControl
from maxicrawler.downloader.errors import (
    DownloadCancelledError,
    DownloadError,
    DownloadRefusedError,
    SourceError,
)
from maxicrawler.downloader.manager import DownloadManager, DownloadWorker
from maxicrawler.downloader.models import (
    DownloadJob,
    DownloadOutcome,
    DownloadPlan,
    DownloadReport,
    ResourceIdentity,
    UnresolvedSource,
)
from maxicrawler.downloader.planner import DownloadPlanner
from maxicrawler.downloader.progress import (
    NullProgressReporter,
    ProgressReporter,
    RichProgressReporter,
)
from maxicrawler.downloader.queue import DownloadQueue
from maxicrawler.downloader.sink import DEFAULT_HASH_ALGORITHM, LibrarySink
from maxicrawler.downloader.sources import SourceItem, SourceResolver, looks_like_url

__all__ = [
    "DEFAULT_HASH_ALGORITHM",
    "DownloadCancelledError",
    "DownloadControl",
    "DownloadError",
    "DownloadJob",
    "DownloadManager",
    "DownloadOutcome",
    "DownloadPlan",
    "DownloadPlanner",
    "DownloadQueue",
    "DownloadRefusedError",
    "DownloadReport",
    "DownloadWorker",
    "LibrarySink",
    "NullProgressReporter",
    "ProgressReporter",
    "ResourceIdentity",
    "RichProgressReporter",
    "SourceError",
    "SourceItem",
    "SourceResolver",
    "UnresolvedSource",
    "looks_like_url",
]
