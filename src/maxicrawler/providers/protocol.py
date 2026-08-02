"""The public protocol implemented by MaxiCrawler resource providers."""

from typing import Protocol, runtime_checkable

from maxicrawler.domain import (
    ProviderInfo,
    ResourceInspection,
    ResourceRef,
    UrlClassification,
)


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
    can be built, stored, and compared offline. Only :meth:`inspect` performs
    requests, which keeps the network on an explicit, testable seam.

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
