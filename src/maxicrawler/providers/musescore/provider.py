"""Turning a MuseScore score page into the files worth keeping.

**This provider holds no session and does not know one exists.** It is handed a
transport that already carries whatever credentials the installation was
configured with, which is what keeps the credential's blast radius down to the
two modules that build it. What this knows is that a request may come back
looking logged-out, and what to call that when it does.

An inspection is one GET of the score page, read as text rather than streamed:
the page is tens of kilobytes and its state is the point. A transfer is one GET
of an address that state named, streamed into the sink like any other file.

**Why a score is a container.** One page is one piece of music offered in
several renderings, and MaxiCrawler already knows how to turn a container into
several jobs. Which renderings are worth keeping is a configuration question,
not a MuseScore question, so the set is handed in.
"""

from __future__ import annotations

from contextlib import closing

from maxicrawler import __version__
from maxicrawler.domain import (
    Availability,
    ContentDescriptor,
    LinkAttribute,
    ProviderCapability,
    ProviderInfo,
    ResourceEntry,
    ResourceInspection,
    ResourceKind,
    ResourceMetadata,
    ResourceRef,
    UrlClassification,
)
from maxicrawler.plugins.musescore.parser import parse_score_url
from maxicrawler.providers.errors import UnsupportedResourceError
from maxicrawler.providers.musescore.errors import ScorePageError
from maxicrawler.providers.musescore.state import ScorePage, parse_score_page
from maxicrawler.providers.protocol import DownloadSink
from maxicrawler.providers.transport import DEFAULT_CHUNK_SIZE, FileTransport

MUSESCORE_PROVIDER_NAME = "musescore"
"""Registry name of the MuseScore provider."""

MUSESCORE_PROVIDER_PRIORITY = 100
"""Above :class:`~maxicrawler.providers.direct.DirectProvider`, which claims everything.

A score page fetched by the direct provider would store the HTML, which is a
believable file and the wrong one entirely.
"""

DEFAULT_FORMATS = ("pdf", "mscz")
"""What is kept when nothing says otherwise: one to read from, one to edit.

Not every rendering the page offers. MuseScore's allowance is spent per
download, so every format added is a format the queue spends a day's budget on
— which makes this the setting that decides whether a hundred scores take a
week or a month.
"""

MAX_PAGE_BYTES = 4 * 1024 * 1024
"""How much of a score page will be read before giving up on it.

A score page is well under a megabyte. This is not a security boundary, it is a
refusal to buffer something that is no longer a score page — a redirect into a
download, a misconfigured proxy streaming something large.
"""


class MuseScoreProvider:
    """Inspects and transfers the renderings of a MuseScore score.

    Built without a transport it advertises no capability at all, which is the
    same switch every other provider here has: a command that must not touch
    the network gets a provider that says it cannot rather than one that fails
    when asked.
    """

    def __init__(
        self,
        *,
        transport: FileTransport | None = None,
        formats: tuple[str, ...] = DEFAULT_FORMATS,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> None:
        if chunk_size <= 0:
            msg = "chunk_size must be positive"
            raise ValueError(msg)
        if not formats:
            msg = "at least one format must be wanted"
            raise ValueError(msg)
        self._transport = transport
        self._formats = formats
        self._chunk_size = chunk_size
        capabilities = {ProviderCapability.INSPECT, ProviderCapability.LIST}
        if transport is not None:
            capabilities.add(ProviderCapability.DOWNLOAD)
        self._metadata = ProviderInfo(
            name=MUSESCORE_PROVIDER_NAME,
            version=__version__,
            module=__name__,
            description="Reads MuseScore score pages and transfers their renderings.",
            display_name="MuseScore",
            priority=MUSESCORE_PROVIDER_PRIORITY,
            capabilities=frozenset(capabilities),
        )

    @property
    def metadata(self) -> ProviderInfo:
        """Return the immutable descriptor for this provider."""
        return self._metadata

    @property
    def formats(self) -> tuple[str, ...]:
        """Return the renderings this provider keeps, in order."""
        return self._formats

    def supports(self, classification: UrlClassification) -> bool:
        """Return whether *classification* names a MuseScore score.

        The URL is parsed rather than the plugin's verdict read, matching every
        other provider here: what this can address is a property of the address.
        """
        return parse_score_url(classification.record.raw_url) is not None

    def reference(self, classification: UrlClassification) -> ResourceRef:
        """Return the score *classification* addresses, without any I/O.

        The score number is the identity and the format is not, because at this
        point there is one resource: the page. The per-format references are
        made during inspection, once the page has said which formats exist.
        """
        raw_url = classification.record.raw_url
        link = parse_score_url(raw_url)
        if link is None:
            msg = f"not a MuseScore score page: {raw_url}"
            raise UnsupportedResourceError(msg)
        return ResourceRef(
            provider=MUSESCORE_PROVIDER_NAME,
            resource_id=link.score_id,
            kind=ResourceKind.FOLDER,
            url=link.url,
        )

    def inspect(self, ref: ResourceRef) -> ResourceInspection:
        """Return the renderings of *ref* that this installation wants.

        A rendering the page does not offer is absent rather than an error: a
        PDF-only score is a score, not a failure.
        """
        page = self._read_page(ref)
        entries = tuple(
            self._entry(ref, page, kind)
            for kind in self._formats
            if page.download_for(kind) is not None
        )
        if not entries:
            offered = ", ".join(download.kind for download in page.downloads)
            msg = f"score {page.score_id} offers {offered}, none of them wanted"
            raise ScorePageError(msg)
        return ResourceInspection(
            ref=ref,
            availability=Availability.AVAILABLE,
            metadata=ResourceMetadata(
                kind=ResourceKind.FOLDER,
                name=page.title,
                attributes=_page_attributes(page),
            ),
            entries=entries,
        )

    def download(self, ref: ResourceRef, sink: DownloadSink) -> ContentDescriptor:
        """Stream one rendering of a score into *sink*.

        The reference must name a rendering, which is what inspection produces.
        Handing in the page itself is refused rather than guessed at: a score is
        two files here, and picking one of them silently would be picking wrong
        half the time.
        """
        transport = self._require_transport(ref)
        if ref.kind is not ResourceKind.FILE or ref.parent_id is None:
            msg = f"score {ref.resource_id} names no rendering to transfer"
            raise UnsupportedResourceError(msg)
        remote, chunks = transport.open(ref.url, chunk_size=self._chunk_size)
        # The name comes from the reference rather than the response: MuseScore
        # answers a download URL with a generic filename, and the reference
        # already carries the title inspection read off the page.
        descriptor = ContentDescriptor(name=ref.resource_id, size=remote.size)
        with closing(chunks):
            sink.begin(descriptor)
            for chunk in chunks:
                sink.write(chunk)
        return descriptor

    def _entry(self, ref: ResourceRef, page: ScorePage, kind: str) -> ResourceEntry:
        """Return the entry for one rendering of an inspected score."""
        download = page.download_for(kind)
        if download is None:  # pragma: no cover - the caller filtered these out
            msg = f"score {page.score_id} offers no {kind}"
            raise ScorePageError(msg)
        name = f"{_slug(page)}.{kind}"
        return ResourceEntry(
            ref=ResourceRef(
                provider=MUSESCORE_PROVIDER_NAME,
                resource_id=name,
                kind=ResourceKind.FILE,
                url=download.url,
                parent_id=page.score_id,
            ),
            metadata=ResourceMetadata(
                kind=ResourceKind.FILE,
                name=name,
                attributes=(LinkAttribute("format", kind),),
            ),
        )

    def _read_page(self, ref: ResourceRef) -> ScorePage:
        """Fetch the score page behind *ref* and read its state."""
        transport = self._require_transport(ref)
        remote, chunks = transport.open(ref.url, chunk_size=self._chunk_size)
        collected = bytearray()
        with closing(chunks):
            for chunk in chunks:
                collected.extend(chunk)
                if len(collected) > MAX_PAGE_BYTES:
                    msg = f"the page at {remote.url} is too large to be a score page"
                    raise ScorePageError(msg)
        return parse_score_page(collected.decode("utf-8", errors="replace"), url=remote.url)

    def _require_transport(self, ref: ResourceRef) -> FileTransport:
        """Return the transport, refusing a reference this cannot serve."""
        if ref.provider != MUSESCORE_PROVIDER_NAME:
            msg = f"reference belongs to another provider: {ref.provider}"
            raise UnsupportedResourceError(msg)
        if self._transport is None:
            msg = "this provider was built without a transport"
            raise UnsupportedResourceError(msg)
        return self._transport


def _page_attributes(page: ScorePage) -> tuple[LinkAttribute, ...]:
    """Return what the page said that has no field of its own.

    The allowance is carried through here rather than acted on, because a
    provider is the wrong place to have an opinion about tomorrow. Whoever owns
    the queue reads these and decides.
    """
    attributes = [LinkAttribute("score_id", page.score_id)]
    if page.author is not None:
        attributes.append(LinkAttribute("author", page.author))
    if page.composer is not None:
        attributes.append(LinkAttribute("composer", page.composer))
    if page.pages is not None:
        attributes.append(LinkAttribute("pages", str(page.pages)))
    if page.daily_limit is not None:
        attributes.append(LinkAttribute("daily_limit", str(page.daily_limit)))
    attributes.append(LinkAttribute("limit_reached", "yes" if page.limit_reached else "no"))
    return tuple(attributes)


def _slug(page: ScorePage) -> str:
    """Return a readable stem for the files of *page*.

    The score number is always in it. A title alone would collide between two
    arrangements of the same song, which on this host is the common case rather
    than the odd one.
    """
    if page.title is None:
        return page.score_id
    return f"{page.title} ({page.score_id})"
