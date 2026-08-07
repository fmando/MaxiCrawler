"""The offline discovery workflow over local documents."""

from collections import Counter
from pathlib import Path

from maxicrawler.crawler.discovery import DiscoveryPipeline
from maxicrawler.crawler.repository import DiscoveryRepository, NullDiscoveryRepository
from maxicrawler.crawler.summary import DiscoverySummary, to_plugin_usage
from maxicrawler.documents import DocumentLoader
from maxicrawler.domain import ScanSession
from maxicrawler.extractors import GenericUrlExtractor, UrlExtractor


class LocalDiscoveryService:
    """Discovers URLs in local documents without any network access.

    The service is pure orchestration: the loader reads documents, the
    extractor turns them into URL candidates, and
    :class:`~maxicrawler.crawler.DiscoveryPipeline` performs normalization,
    duplicate detection, and plugin resolution. The plugin registry is never
    bypassed. Persistence goes through the injected
    :class:`~maxicrawler.crawler.DiscoveryRepository`, so no storage detail
    reaches this layer.
    """

    def __init__(
        self,
        pipeline: DiscoveryPipeline,
        *,
        loader: DocumentLoader | None = None,
        extractor: UrlExtractor | None = None,
        repository: DiscoveryRepository | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._loader = loader if loader is not None else DocumentLoader()
        self._extractor = extractor if extractor is not None else GenericUrlExtractor()
        self._repository = repository if repository is not None else NullDiscoveryRepository()

    @property
    def pipeline(self) -> DiscoveryPipeline:
        """Return the pipeline this service feeds."""
        return self._pipeline

    @property
    def loader(self) -> DocumentLoader:
        """Return the document loader in use."""
        return self._loader

    def run(self, root: Path, session: ScanSession) -> DiscoverySummary:
        """Discover URLs in every supported document at or below *root*.

        Only first-seen URLs are persisted; repeats are counted as duplicates
        instead. The returned summary reports the session counters together
        with how often each plugin was responsible.

        Raises:
            FileNotFoundError: *root* does not exist.
        """
        self._repository.start_session(session)
        self._pipeline.start(session)
        usage: Counter[str] = Counter()
        for document in self._loader.load_all(root):
            self._pipeline.record_document()
            for candidate in self._extractor.extract(document):
                result = self._pipeline.discover(candidate.raw_url, source_url=document.source)
                if result.is_duplicate:
                    continue
                self._repository.save_result(session, result)
                resolution = result.resolution
                if resolution is not None and resolution.plugin is not None:
                    usage[resolution.plugin.name] += 1
        statistics = self._pipeline.finish(session)
        self._repository.finish_session(session, statistics)
        return DiscoverySummary(
            session=session,
            statistics=statistics,
            plugin_usage=to_plugin_usage(usage),
        )
