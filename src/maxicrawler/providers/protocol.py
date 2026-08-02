"""The public protocols implemented by, and handed to, resource providers."""

from typing import Protocol, runtime_checkable

from maxicrawler.domain import (
    ContentDescriptor,
    ProviderInfo,
    ResourceInspection,
    ResourceRef,
    UrlClassification,
)


@runtime_checkable
class DownloadSink(Protocol):
    """Receives the content of one resource as a provider transfers it.

    This is the seam that keeps the download manager provider-independent and
    providers storage-independent. A provider streams bytes into a sink and
    never learns where they end up; the manager owns the destination, the
    staging file, the hashing, and the progress bar, and never learns how the
    bytes were obtained.

    A sink is written to exactly once, in order:
    :meth:`begin` announces what is coming, then :meth:`write` is called until
    the payload is complete. What happens after that — committing, discarding,
    verifying — belongs to whoever created the sink, not to the provider.
    """

    def begin(self, content: ContentDescriptor) -> None:
        """Announce the payload that is about to be written.

        Called exactly once, before the first chunk, so a destination can be
        named and a total can be shown even for a provider that only learns
        both while opening the transfer.
        """
        ...

    def write(self, chunk: bytes) -> None:
        """Append *chunk* to the payload."""
        ...


@runtime_checkable
class ResourceProvider(Protocol):
    """Answers what can be done with the resource behind a classified URL.

    A provider takes over where a
    :class:`~maxicrawler.plugins.protocol.CrawlerPlugin` stops. The plugin
    decides *"can I classify this URL?"* without touching the network; the
    provider decides *"what can I do with this resource?"* and may talk to the
    outside world to find out.

    The contract splits deliberately into a pure half and an I/O half.
    :meth:`supports` and :meth:`reference` are side-effect free, so references
    can be built, stored, and compared offline. Only :meth:`inspect` and
    :meth:`download` perform requests, which keeps the network on an explicit,
    testable seam.

    Implementations are duck-typed; inheriting from this protocol is optional
    but makes the contract explicit to readers and type checkers.
    """

    @property
    def metadata(self) -> ProviderInfo:
        """Return the immutable descriptor advertised by this provider."""
        ...

    def supports(self, classification: UrlClassification) -> bool:
        """Return whether this provider claims responsibility for *classification*.

        Implementations must be side-effect free and must not perform I/O.
        """
        ...

    def reference(self, classification: UrlClassification) -> ResourceRef:
        """Return the addressable resource *classification* points at.

        This is a pure translation from a URL to a reference; no request is
        made and no credential is used.

        Raises:
            UnsupportedResourceError: the classification is not one this
                provider can address.
        """
        ...

    def inspect(self, ref: ResourceRef) -> ResourceInspection:
        """Return what can be learned about *ref* without downloading it.

        Implementations report an unreachable resource through
        :attr:`ResourceInspection.availability` instead of raising, and raise
        only when the inspection itself failed.

        Raises:
            ProviderTransportError: the request could not be carried out.
            ProviderProtocolError: the response could not be understood.
            ProviderRateLimitError: the provider refused and retries ran out.
        """
        ...

    def download(self, ref: ResourceRef, sink: DownloadSink) -> ContentDescriptor:
        """Transfer the content of *ref* into *sink* and describe what was sent.

        Only a single resource is transferred. A container is enumerated with
        :meth:`inspect` and its entries are downloaded individually, so the
        question *"what does one transfer mean?"* has exactly one answer for
        every provider.

        Unlike :meth:`inspect`, a resource that cannot be reached is raised
        rather than returned: there is no partial answer to give, and the
        caller has a failed transfer to record either way.

        A provider that cannot transfer content omits
        :attr:`~maxicrawler.domain.providers.ProviderCapability.DOWNLOAD` from
        its metadata and raises :class:`UnsupportedResourceError` here.

        Raises:
            UnsupportedResourceError: *ref* is not something this provider can
                transfer — a foreign reference, a container, or a link missing
                the credential its content needs.
            ProviderTransportError: the transfer could not be carried out.
            ProviderProtocolError: the response could not be understood.
            ProviderRateLimitError: the provider refused and retries ran out.
            ProviderCryptoError: the content could not be decrypted.
        """
        ...
