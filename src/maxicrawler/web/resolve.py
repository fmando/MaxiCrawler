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
