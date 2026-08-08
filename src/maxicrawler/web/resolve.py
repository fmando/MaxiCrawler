"""Turning what a page wrote down into absolute HTTP(S) URLs.

Three rules govern this module, and each of them is a decision rather than a
detail.

**Resolution is against the URL that answered.** A page reached through a
redirect states its relative links against where it ended up, not against where
we started. Using the requested URL is the most common relative-link bug in a
crawler, which is why :class:`~maxicrawler.web.models.FetchedPage` keeps both.

**A ``<base>`` overrides that, and the first one wins.** The standard ignores
later ones, and a page that declares a base means every relative reference on
it, including the ones written before the element.

**Fragments are preserved.** A conventional crawler strips them, and doing that
here would silently destroy every legacy Mega share on a page, because such a
link keeps its whole handle and decryption key in the fragment. MaxiCrawler
treats a fragment as identity throughout — see
:func:`maxicrawler.utils.urls.normalize_url` — and this is where that promise
either holds or is quietly broken. A reference that is *only* a fragment is a
different thing: it points into the page we already have, so it is dropped.
"""

from urllib.parse import urljoin, urlsplit

from maxicrawler.utils import HTTP_SCHEMES
from maxicrawler.web.models import HtmlDocument, PageLink, ParsedHtml, RawLink


def resolve_base_url(page_url: str, base_href: str | None) -> str:
    """Return what relative references on the page resolve against.

    A ``<base href>`` is itself resolved against *page_url*, so a relative base
    such as ``docs/`` works. A base that resolves to something other than
    HTTP(S) is ignored rather than honoured: a page cannot talk the crawler
    into a scheme it refuses.
    """
    if base_href is None:
        return page_url
    candidate = urljoin(page_url, base_href)
    if not is_http_url(candidate):
        return page_url
    return candidate


def is_http_url(url: str) -> bool:
    """Return whether *url* is an absolute HTTP(S) URL with a host."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return parsed.scheme.lower() in HTTP_SCHEMES and bool(parsed.hostname)


NON_PAGE_SUFFIXES = frozenset(
    {
        # documents
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".odt",
        ".ods",
        ".odp",
        ".rtf",
        ".epub",
        ".mobi",
        ".txt",
        ".csv",
        # archives and images
        ".zip",
        ".rar",
        ".7z",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".iso",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".bmp",
        ".webp",
        ".svg",
        ".ico",
        ".tif",
        ".tiff",
        # audio, video and binaries
        ".mp3",
        ".wav",
        ".flac",
        ".ogg",
        ".m4a",
        ".aac",
        ".mp4",
        ".m4v",
        ".mkv",
        ".avi",
        ".mov",
        ".webm",
        ".wmv",
        ".exe",
        ".msi",
        ".dmg",
        ".deb",
        ".rpm",
        ".apk",
        ".torrent",
        # assets that are occasionally linked from an anchor
        ".css",
        ".js",
        ".json",
    }
)
"""File extensions that are never an HTML page.

Deliberately conservative, and biased in one direction: a suffix missing from
this set costs one wasted request, while a suffix wrongly *in* it silently
loses a page. So ``.xml`` is absent — a document served as XHTML or a feed is
plausible enough not to guess about — and anything that a server routinely
renders as HTML (``.php``, ``.aspx``, ``.jsp``) was never a candidate.

The set is data, so teaching the crawler about one more archive format is an
entry rather than a change.
"""


def looks_like_a_page(url: str) -> bool:
    """Return whether *url* could plausibly answer with an HTML page.

    Judged from the path's extension alone, which is a heuristic and is
    treated as one: it is used to avoid *asking*, never to decide what an
    answer meant. A server that returns a PDF from ``/download?id=7`` is still
    caught by the content type of its reply.
    """
    path = urlsplit(url).path
    suffix = path[path.rfind(".") :].lower() if "." in path.rsplit("/", 1)[-1] else ""
    return suffix not in NON_PAGE_SUFFIXES


def resolve_link(base_url: str, raw_url: str) -> str | None:
    """Return *raw_url* as an absolute HTTP(S) URL, or ``None``.

    ``None`` means the reference is not something the discovery pipeline can
    take: a ``mailto:``, ``javascript:``, ``tel:`` or ``data:`` target, a
    same-document ``#`` reference, or a string that will not parse at all.
    """
    reference = raw_url.strip()
    if not reference or reference.startswith("#"):
        return None
    try:
        absolute = urljoin(base_url, reference)
    except ValueError:
        return None
    return absolute if is_http_url(absolute) else None


def resolve_links(
    parsed: ParsedHtml, *, page_url: str, encoding: str, extra: tuple[RawLink, ...] = ()
) -> HtmlDocument:
    """Return *parsed* with every reference resolved against the page.

    *extra* carries links found outside the markup — bare URLs in the page's
    prose — so they pass through the same resolution and the same counting as
    the ones an element declared.

    Duplicates are kept. Removing them here would make "42 links, 30 unique"
    impossible to report, and the pipeline counts them anyway.
    """
    base_url = resolve_base_url(page_url, parsed.base_href)
    links: list[PageLink] = []
    skipped = 0
    for raw in (*parsed.raw_links, *extra):
        resolved = resolve_link(base_url, raw.value)
        if resolved is None:
            skipped += 1
            continue
        links.append(
            PageLink(
                raw_url=raw.value,
                resolved_url=resolved,
                kind=raw.kind,
                tag=raw.tag,
                attribute=raw.attribute,
            )
        )
    return HtmlDocument(
        url=page_url,
        base_url=base_url,
        encoding=encoding,
        title=parsed.title,
        canonical_url=resolve_link(base_url, parsed.canonical_href or ""),
        links=tuple(links),
        skipped_links=skipped,
        truncated=parsed.truncated,
    )
