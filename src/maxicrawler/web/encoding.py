"""Deciding what the bytes of a page are, as text.

The order follows the HTML standard's encoding sniffing algorithm, reduced to
the steps a non-interactive fetcher can perform:

1. a byte order mark, which is decisive;
2. the ``charset`` parameter of the HTTP ``Content-Type`` header;
3. a prescan of the first 1024 bytes for ``<meta charset>`` or
   ``<meta http-equiv="Content-Type">``;
4. UTF-8.

Step 4 departs from browsers, which fall back to windows-1252 for historical
reasons. The corpus this project targets is modern, and the cost of being
wrong is bounded: decoding never fails, because a body that will not decode
strictly is decoded again with replacement characters. A page can therefore
never abort a crawl, and a mangled paragraph still yields its ASCII URLs.
"""

import codecs
import re

DEFAULT_ENCODING = "utf-8"
"""What a page is assumed to be when it says nothing and carries no BOM."""

PRESCAN_BYTES = 1024
"""How far into the body a ``<meta>`` declaration is looked for.

The standard's recommended limit. Scanning further would find declarations no
browser honours, which is a worse kind of wrong than missing them.
"""

_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)
"""Byte order marks, longest first: a UTF-32 LE mark starts with a UTF-16 one."""

_META_CHARSET = re.compile(rb"<meta[^>]+?charset\s*=\s*[\"']?\s*([a-zA-Z0-9_\-:.]+)", re.IGNORECASE)
"""Matches both ``<meta charset=…>`` and the ``charset=`` inside an http-equiv."""


def normalize_label(label: str | None) -> str | None:
    """Return the codec name *label* refers to, or ``None``.

    Real responses carry labels no codec is registered under — ``utf8``,
    ``UTF-8"``, an empty string, the word ``unknown``. Every one of them is
    resolved through :func:`codecs.lookup`, so an unusable label falls through
    to the next step of the algorithm rather than raising.
    """
    if label is None:
        return None
    cleaned = label.strip().strip("\"'").strip()
    if not cleaned:
        return None
    try:
        return codecs.lookup(cleaned).name
    except LookupError:
        return None


def sniff_bom(body: bytes) -> str | None:
    """Return the encoding a byte order mark states, or ``None``.

    A mark outranks every declaration, including the HTTP header: it is written
    by the producer of the bytes, while a header is written by whatever served
    them.
    """
    for mark, encoding in _BOMS:
        if body.startswith(mark):
            return encoding
    return None


def sniff_meta(body: bytes, *, limit: int = PRESCAN_BYTES) -> str | None:
    """Return the encoding the markup declares in its first *limit* bytes."""
    match = _META_CHARSET.search(body[:limit])
    if match is None:
        return None
    return normalize_label(match.group(1).decode("ascii", errors="replace"))


def detect_encoding(body: bytes, *, declared: str | None = None) -> str:
    """Return the encoding *body* should be decoded as.

    *declared* is the ``charset`` from the HTTP header, when the response
    stated one.
    """
    return sniff_bom(body) or normalize_label(declared) or sniff_meta(body) or DEFAULT_ENCODING


def decode_body(body: bytes, *, declared: str | None = None) -> tuple[str, str]:
    """Return *body* as text, together with the encoding that produced it.

    A strict decode is attempted first, so a correct declaration is honoured
    exactly. When it fails the same encoding is used again with replacement
    characters: a page with a handful of bad bytes is worth reading, and a
    crawl must not stop over one.
    """
    encoding = detect_encoding(body, declared=declared)
    try:
        return body.decode(encoding), encoding
    except (UnicodeDecodeError, LookupError):
        return body.decode(encoding, errors="replace"), encoding
