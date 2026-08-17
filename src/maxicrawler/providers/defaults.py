"""Composition helper assembling the built-in provider set.

Keeping this wiring in its own module lets
:mod:`maxicrawler.providers.registry` stay unaware of any concrete provider.
"""

from maxicrawler.providers.crypto import CipherBackend, default_cipher_backend
from maxicrawler.providers.direct import DirectProvider
from maxicrawler.providers.mega import MEGA_API_URL, MegaApiClient, MegaProvider
from maxicrawler.providers.mega.provider import DEFAULT_MAX_ENTRIES
from maxicrawler.providers.musescore import MuseScoreProvider
from maxicrawler.providers.musescore.provider import DEFAULT_FORMATS
from maxicrawler.providers.registry import ProviderRegistry
from maxicrawler.providers.retry import Retrier, RetryPolicy
from maxicrawler.providers.transport import (
    DEFAULT_CHUNK_SIZE,
    FileTransport,
    HttpTransport,
    StreamTransport,
)


def create_default_provider_registry(
    *,
    transport: HttpTransport,
    stream: StreamTransport | None = None,
    files: FileTransport | None = None,
    musescore_files: FileTransport | None = None,
    musescore_formats: tuple[str, ...] = DEFAULT_FORMATS,
    cipher: CipherBackend | None = None,
    retry: RetryPolicy | None = None,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    mega_api_url: str = MEGA_API_URL,
) -> ProviderRegistry:
    """Return a registry holding MaxiCrawler's built-in providers.

    *cipher* defaults to the optional AES backend when it is installed. Passing
    one explicitly keeps the choice in the caller's hands, which is what tests
    and future headless callers want.

    *stream* is what makes downloading possible. Leaving it out yields
    inspection-only providers, which is exactly what a command that must not
    move any content wants — and the providers say so through their
    capabilities rather than by failing when asked.

    *files* is the same switch for ordinary URLs, and it is separate on
    purpose: it is the one transport that can be pointed at any host a crawl
    named, so *"does this installation fetch arbitrary files?"* stays a
    question with a visible answer rather than a consequence of wiring
    something else.

    *musescore_files* is a **second** file transport, and the separation is the
    security property rather than tidiness. That host needs a session the
    person running MaxiCrawler exported from their own browser, and the way to
    keep a credential from reaching hosts it has no business with is to keep it
    out of the transport those hosts are fetched through. So the session-
    bearing transport serves one provider, ``files`` stays anonymous, and
    neither can be mistaken for the other at a glance.

    Passing nothing leaves the MuseScore provider without a transport, which it
    reports as having no download capability — the ordinary state of an
    installation nobody has configured a session for.

    Order is not arrangement here. The registry resolves by descending
    priority, and :class:`~maxicrawler.providers.direct.DirectProvider` sits
    below everything, so a Mega link reaches the provider that can decrypt it
    rather than the one that would happily store its ciphertext.
    """
    api = MegaApiClient(transport, base_url=mega_api_url, retrier=Retrier(retry))
    mega = MegaProvider(
        api,
        cipher=cipher if cipher is not None else default_cipher_backend(),
        stream=stream,
        max_entries=max_entries,
        chunk_size=chunk_size,
    )
    direct = DirectProvider(files, chunk_size=chunk_size)
    musescore = MuseScoreProvider(
        transport=musescore_files,
        formats=musescore_formats,
        chunk_size=chunk_size,
    )
    return ProviderRegistry([mega, musescore, direct])
