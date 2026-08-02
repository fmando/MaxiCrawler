"""URL canonicalization and duplicate-detection helpers."""

from collections.abc import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def normalize_url(value: str) -> str:
    """Return a stable HTTP(S) URL.

    Query parameters are sorted solely to make equivalent candidate URLs
    compare consistently within an in-memory discovery session.

    The fragment is preserved verbatim, because it can carry the identity of a
    link rather than a position inside a page: a legacy Mega share keeps its
    whole handle and decryption key there. Dropping or rewriting it would make
    two unrelated links compare equal and destroy case-sensitive keys.
    """
    parsed = urlsplit(value.strip())
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        msg = "URL must be an absolute HTTP(S) URL"
        raise ValueError(msg)
    hostname = parsed.hostname.lower()
    port = parsed.port
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"
    path = parsed.path or "/"
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)), doseq=True)
    return urlunsplit((scheme, netloc, path, query, parsed.fragment))


def strip_fragment(url: str) -> str:
    """Return *url* without its fragment.

    A share link keeps its decryption key there, so anything that echoes a URL
    back to a person, a log, or an error message should echo this instead. The
    result is not a substitute for the URL: a legacy Mega link keeps its whole
    identity in the fragment and is reduced to its host.
    """
    parsed = urlsplit(url.strip())
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


class DuplicateDetector:
    """Tracks normalized URLs seen during one discovery session."""

    def __init__(self, seen_urls: Iterable[str] = ()) -> None:
        self._seen = {normalize_url(url) for url in seen_urls}

    def is_duplicate(self, normalized_url: str) -> bool:
        """Return whether *normalized_url* was already registered."""
        return normalized_url in self._seen

    def register(self, normalized_url: str) -> bool:
        """Register a normalized URL and report whether it was already known."""
        duplicate = self.is_duplicate(normalized_url)
        self._seen.add(normalized_url)
        return duplicate
