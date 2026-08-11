"""The provider for files that are simply at a URL.

Every other provider exists because a host wraps its files in something: an
API, a share token, a decryption key. This one exists because most of the web
does not. An image on a board, a PDF on a university page, a zip on a release
page — the URL *is* the file, and the only thing standing between a crawl
result and the library was that nothing claimed it.

**It claims every HTTP(S) URL, and that is the honest answer.** It really can
transfer any of them, a page included. The consequence is that *"could this be
downloaded?"* stops being a discriminating question — which is a fact about
reporting rather than about this provider, and is dealt with where reports are
made. What tells an image from a page is the URL's own suffix, which
:mod:`maxicrawler.app.targets` already reads.

**It is registered at the lowest priority**, below every specialised provider,
for the same reason the generic *plugin* is: a Mega link must be answered by
the provider that can decrypt it, not by the one that would faithfully
download its ciphertext. A registry resolves by descending priority and stops
at the first claim, so ordering is the whole of the arrangement.

**Nothing here decides whether a URL may be reached.** That is
:class:`~maxicrawler.providers.transport.UrllibFileTransport`'s, which refuses
internal addresses on the first request and on every redirect. Putting it there
rather than here means a second provider built on the same transport inherits
it instead of having to remember it.
"""

from contextlib import closing
from urllib.parse import unquote, urlsplit

from maxicrawler import __version__
from maxicrawler.domain import (
    Availability,
    ContentDescriptor,
    LinkAttribute,
    ProviderCapability,
    ProviderInfo,
    ResourceInspection,
    ResourceKind,
    ResourceMetadata,
    ResourceRef,
    UrlClassification,
)
from maxicrawler.providers.errors import UnsupportedResourceError
from maxicrawler.providers.protocol import DownloadSink
from maxicrawler.providers.transport import DEFAULT_CHUNK_SIZE, FileTransport, RemoteFile
from maxicrawler.utils.urls import strip_fragment

DIRECT_PROVIDER_NAME = "direct"
"""Registry name of the provider for ordinary files."""

DIRECT_PROVIDER_PRIORITY = -100
"""Default priority, matching the generic plugin's and for the same reason.

Deliberately below any specialised provider. This one claims everything, so
anything else that claims a URL knows something about it that this does not.
"""

SUPPORTED_SCHEMES = frozenset({"http", "https"})
"""What this provider will address. Not a policy — a statement of reach."""

AVAILABILITY: dict[int, Availability] = {
    401: Availability.ACCESS_DENIED,
    403: Availability.ACCESS_DENIED,
    404: Availability.NOT_FOUND,
    410: Availability.NOT_FOUND,
    429: Availability.RATE_LIMITED,
    451: Availability.BLOCKED,
}
"""How a refusing status is read as an availability.

Only the statuses that *say something about the resource* are listed. 410 is
"gone" and 404 is "not here", and the difference does not survive into a
report, so both are NOT_FOUND. 451 is the legal takedown, which is what
BLOCKED means for a host that has one.

Anything absent — every 5xx, and the odd statuses nobody agrees on — is
:attr:`Availability.UNKNOWN` rather than a guess. A server that is broken has
not said the file is gone, and recording that it had would be the kind of
wrong answer somebody acts on months later.
"""


def availability_for(status: int) -> Availability:
    """Return what *status* says about the resource behind it."""
    if 200 <= status < 300:
        return Availability.AVAILABLE
    return AVAILABILITY.get(status, Availability.UNKNOWN)


def stated_name(remote: RemoteFile) -> str | None:
    """Return the best name the response supports, or ``None`` for neither.

    ``Content-Disposition`` first, because a host that states a name has said
    what it wants the file called. Otherwise the last path segment of the URL
    that *answered* — after redirects, since that is the one naming the file
    rather than the one naming the redirector.

    Percent-encoding is undone, because ``na%C3%AFve.pdf`` is a name written
    for a URL and not a name. Nothing else is cleaned here:
    :func:`~maxicrawler.library.naming.safe_filename` does that for every name
    the library stores, and two sanitizers on one string is one too many.
    """
    if remote.filename:
        return remote.filename
    last = urlsplit(remote.url).path.rstrip("/").rpartition("/")[2]
    return unquote(last) or None


class DirectProvider:
    """Reads and transfers files that a plain HTTP(S) URL points at.

    An inspection is one ``HEAD`` — or, for a host that will not answer one, a
    ``GET`` whose body is never pulled. A transfer is one ``GET``, streamed
    into the sink chunk by chunk and never held: a four-gigabyte file costs the
    same memory as a four-kilobyte one.

    There is no container here and never will be. A URL names one file; a page
    that lists more of them is a *crawl*, which is the other half of this
    program and must not be reinvented behind a provider. So
    :attr:`~maxicrawler.domain.providers.ProviderCapability.LIST` is not
    advertised and :attr:`ResourceInspection.entries` is always empty.

    Transferring is optional in the same way it is for every provider: built
    without a transport, this advertises neither capability rather than failing
    when asked.
    """

    def __init__(
        self,
        transport: FileTransport | None = None,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        priority: int = DIRECT_PROVIDER_PRIORITY,
    ) -> None:
        if chunk_size < 1:
            msg = "chunk_size must be at least 1"
            raise ValueError(msg)
        self._transport = transport
        self._chunk_size = chunk_size
        capabilities = (
            {ProviderCapability.INSPECT, ProviderCapability.DOWNLOAD}
            if transport is not None
            else frozenset()
        )
        self._metadata = ProviderInfo(
            name=DIRECT_PROVIDER_NAME,
            version=__version__,
            module=__name__,
            description="Reads and transfers files at ordinary HTTP and HTTPS URLs.",
            display_name="Direct",
            priority=priority,
            capabilities=frozenset(capabilities),
        )

    @property
    def metadata(self) -> ProviderInfo:
        """Return the immutable descriptor for this provider."""
        return self._metadata

    def supports(self, classification: UrlClassification) -> bool:
        """Return whether *classification* names an absolute HTTP(S) URL.

        The URL is parsed rather than the plugin's verdict read, matching every
        other provider here: what this can address is a property of the address.
        """
        parsed = urlsplit(classification.record.raw_url)
        return parsed.scheme.lower() in SUPPORTED_SCHEMES and bool(parsed.hostname)

    def reference(self, classification: UrlClassification) -> ResourceRef:
        """Return the file *classification* addresses, without any I/O.

        The identity is split across ``parent_id`` and ``resource_id``: the
        host holds the first and the path the second. That is not decoration.
        A library key is a readable slug of ``resource_id`` beside a digest of
        the whole identity, so putting the path there makes an entry that
        ``ls`` can be read on — and keeping the host in the identity is what
        stops ``a.test/1.jpg`` and ``b.test/1.jpg`` becoming one entry.
        """
        raw_url = classification.record.raw_url
        parsed = urlsplit(raw_url)
        if parsed.scheme.lower() not in SUPPORTED_SCHEMES or not parsed.hostname:
            msg = f"not an absolute HTTP(S) URL: {strip_fragment(raw_url)}"
            raise UnsupportedResourceError(msg)
        path = parsed.path or "/"
        return ResourceRef(
            provider=DIRECT_PROVIDER_NAME,
            resource_id=f"{path}?{parsed.query}" if parsed.query else path,
            kind=ResourceKind.FILE,
            # The fragment goes, as it does for every reference. Here it never
            # carried anything: a fragment addresses a place inside a document,
            # and a host is not told about it.
            url=strip_fragment(raw_url),
            parent_id=parsed.netloc.lower(),
        )

    def inspect(self, ref: ResourceRef) -> ResourceInspection:
        """Return what the host discloses about *ref* without transferring it."""
        transport = self._require_transport(ref)
        remote = transport.head(ref.url)
        availability = availability_for(remote.status)
        if not availability.is_available:
            return ResourceInspection(ref=ref, availability=availability)
        return ResourceInspection(
            ref=ref,
            availability=availability,
            metadata=ResourceMetadata(
                kind=ResourceKind.FILE,
                name=stated_name(remote),
                size=remote.size,
                attributes=_attributes(remote),
            ),
        )

    def download(self, ref: ResourceRef, sink: DownloadSink) -> ContentDescriptor:
        """Stream the content of *ref* into *sink* and describe what was sent.

        The descriptor is built from the response rather than from the
        reference, because a redirect can change both the name and the size,
        and the thing that answered is the thing being stored.
        """
        transport = self._require_transport(ref)
        remote, chunks = transport.open(ref.url, chunk_size=self._chunk_size)
        descriptor = ContentDescriptor(name=stated_name(remote), size=remote.size)
        # Closing matters on the way out as much as on the way through: a sink
        # that raises -- a full disk, a cancelled download -- would otherwise
        # leave the socket open until the generator was collected.
        with closing(chunks):
            sink.begin(descriptor)
            for chunk in chunks:
                sink.write(chunk)
        return descriptor

    def _require_transport(self, ref: ResourceRef) -> FileTransport:
        """Return the transport, refusing a reference this cannot serve."""
        if ref.provider != DIRECT_PROVIDER_NAME:
            msg = f"reference belongs to another provider: {ref.provider}"
            raise UnsupportedResourceError(msg)
        if self._transport is None:
            msg = "this provider was built without a transport"
            raise UnsupportedResourceError(msg)
        return self._transport


def _attributes(remote: RemoteFile) -> tuple[LinkAttribute, ...]:
    """Return what the response said that has no field of its own."""
    if remote.media_type is None:
        return ()
    return (LinkAttribute(name="content_type", value=remote.media_type),)
