"""The Mega client/server API, reduced to what metadata inspection needs.

Mega does not publish a specification for this endpoint; the request shapes
below follow its own open-source clients. Every wire detail lives here, so a
change on Mega's side has exactly one place to be fixed, and a response that no
longer fits raises :class:`ProviderProtocolError` instead of being guessed at.

Nothing in this module ever receives a
:class:`~maxicrawler.domain.providers.ResourceSecret`: a share key is used
locally by :mod:`maxicrawler.providers.mega.crypto` and is never part of a
request. That confinement is what makes a Mega link safe to hand to a server.
"""

from collections.abc import Mapping
from typing import Any, NoReturn

from maxicrawler.providers.errors import (
    ProviderError,
    ProviderProtocolError,
    ProviderRateLimitError,
    ProviderTransportError,
)
from maxicrawler.providers.mega.mapping import error_name, is_retryable
from maxicrawler.providers.retry import Retrier
from maxicrawler.providers.transport import HttpTransport

MEGA_API_URL = "https://g.api.mega.co.nz/cs"
"""The anonymous client/server endpoint; public links need no authentication."""


class MegaApiError(ProviderError):
    """A negative status code the Mega API answered with."""

    def __init__(self, code: int) -> None:
        super().__init__(f"Mega API error {code} ({error_name(code)})")
        self.code = code
        """The raw status code, for mapping onto an availability."""


class MegaApiClient:
    """Issues the two commands metadata inspection needs.

    Requests carry a monotonic ``id`` and, for anything inside a shared folder,
    the share handle as ``n``. No session identifier is ever sent: a public
    link is readable anonymously, and staying anonymous is deliberate.
    """

    def __init__(
        self,
        transport: HttpTransport,
        *,
        base_url: str = MEGA_API_URL,
        retrier: Retrier | None = None,
    ) -> None:
        self._transport = transport
        self._base_url = base_url
        self._retrier = retrier if retrier is not None else Retrier()
        self._sequence = 0

    def file_metadata(self, handle: str) -> Mapping[str, Any]:
        """Return size and encrypted attributes of the public file *handle*.

        The ``g`` download flag is deliberately left unset. Mega then describes
        the file without allocating a transfer URL, so the inspection moves no
        bytes and consumes no download quota.
        """
        return self._command({"a": "g", "p": handle})

    def folder_nodes(self, folder_handle: str) -> tuple[Mapping[str, Any], ...]:
        """Return every node of the shared folder *folder_handle*.

        One request returns the whole tree, including sizes, timestamps, and
        parent links. All of that is plaintext: only names are encrypted.

        A file inside a shared folder is described from this listing rather
        than by asking about it directly, because its per-node key is only
        published here — without it the name cannot be read at all.
        """
        answer = self._command({"a": "f", "c": 1, "r": 1}, folder=folder_handle)
        nodes = answer.get("f")
        if not isinstance(nodes, list):
            msg = "Mega folder listing does not contain a node array"
            raise ProviderProtocolError(msg)
        return tuple(node for node in nodes if isinstance(node, dict))

    def _command(
        self, command: Mapping[str, Any], *, folder: str | None = None
    ) -> Mapping[str, Any]:
        """Send *command* and return its result, repeating it while Mega defers."""
        return self._retrier.call(
            lambda: self._send(command, folder),
            retry_on=(ProviderRateLimitError, ProviderTransportError),
        )

    def _send(self, command: Mapping[str, Any], folder: str | None) -> Mapping[str, Any]:
        """Send one request and unwrap the single result it answers with."""
        self._sequence += 1
        params = {"id": str(self._sequence)}
        if folder is not None:
            params["n"] = folder
        answer = self._transport.post_json(self._base_url, [command], params=params)
        return _unwrap(answer)


def _unwrap(answer: object) -> Mapping[str, Any]:
    """Return the single result in *answer*.

    Mega answers a batch of one command with an array of one result, and
    reports a failure of the whole request as a bare negative number.
    """
    if isinstance(answer, int) and not isinstance(answer, bool):
        _fail(answer)
    if isinstance(answer, list) and len(answer) == 1:
        result = answer[0]
        if isinstance(result, int) and not isinstance(result, bool):
            _fail(result)
        if isinstance(result, dict):
            return result
    msg = "unexpected response shape from the Mega API"
    raise ProviderProtocolError(msg)


def _fail(code: int) -> NoReturn:
    """Raise the error *code* stands for.

    A deferral becomes :class:`ProviderRateLimitError` so the retrier repeats
    it; every other negative code becomes a :class:`MegaApiError` the provider
    turns into an availability.
    """
    if code >= 0:
        msg = f"unexpected numeric answer from the Mega API: {code}"
        raise ProviderProtocolError(msg)
    if is_retryable(code):
        msg = f"Mega deferred the request: {error_name(code)} ({code})"
        raise ProviderRateLimitError(msg)
    raise MegaApiError(code)
