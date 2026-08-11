"""The error hierarchy shared by every provider.

Only faults on our side are raised. Whether a resource still exists is
reported as an :class:`~maxicrawler.domain.providers.Availability` value,
because a deleted or revoked share is a valid answer to *"what can I do with
this resource?"* rather than a failure of the inspection.
"""


class ProviderError(RuntimeError):
    """Base class for every provider failure."""


class ProviderRegistryError(ProviderError):
    """Base class for every provider registry failure."""


class DuplicateProviderError(ProviderRegistryError):
    """Raised when a provider name is registered more than once."""


class UnknownProviderError(ProviderRegistryError):
    """Raised when a provider name is not registered."""


class InvalidProviderError(ProviderRegistryError):
    """Raised when an object does not implement the provider protocol."""


class UnsupportedResourceError(ProviderError):
    """Raised when a provider cannot build a reference from a classification."""


class ProviderDependencyError(ProviderError):
    """Raised when an optional dependency a provider needs is not installed."""


class ProviderTransportError(ProviderError):
    """Raised when a request could not be carried out at all.

    Connection failures, timeouts, and refused HTTP statuses end up here; the
    resource itself may be perfectly healthy.
    """


class AddressRefusedError(ProviderTransportError):
    """Raised when a URL points inside this machine or this network.

    A subclass of :class:`ProviderTransportError` because to everything that
    catches transport failures this is one: no transfer happened. It is its own
    class because *why* differs in a way worth acting on — a timeout is worth
    retrying and a metadata service never will be — and because the message
    carries a rule's own words, which belong in front of an operator rather
    than in a log line about a network problem.
    """


class ProviderProtocolError(ProviderError):
    """Raised when a response cannot be understood.

    This usually means the remote API changed shape, so it is deliberately
    distinct from a transport failure: retrying will not help.
    """


class ProviderRateLimitError(ProviderError):
    """Raised when a provider refused to answer and retries were exhausted."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        """Seconds the provider asked us to wait, when it said so."""


class ProviderCryptoError(ProviderError):
    """Raised when a credential or an encrypted payload cannot be processed.

    The message never repeats the offending material, so that a malformed key
    cannot reach a log record through an exception.
    """
