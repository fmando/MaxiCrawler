"""Reading what the markup of a page declares.

The parser is pure syntax. It knows about tags and attributes and nothing about
URLs: what it collects are the strings a page wrote down, in the order it wrote
them. Turning those into absolute URLs is :mod:`maxicrawler.web.resolve`'s job.
Keeping the two apart means the parser can be tested without a base URL and the
resolver without any HTML.

Parsing uses the standard library's :class:`~html.parser.HTMLParser`, which is
what :class:`~maxicrawler.documents.HtmlDocumentReader` already uses for local
documents. That is the point: ``discover`` and ``crawl`` agree about what a
link is by construction rather than by coincidence. It is not a spec-compliant
HTML5 tree builder, and it does not need to be — link extraction reads start
tags and attribute values, which is the part it is reliable at.
"""

import re
from html.parser import HTMLParser
from typing import Protocol, runtime_checkable

from maxicrawler.web.models import LinkKind, ParsedHtml, RawLink

LINK_SOURCES: dict[tuple[str, str], LinkKind] = {
    ("a", "href"): LinkKind.ANCHOR,
    ("area", "href"): LinkKind.ANCHOR,
    ("img", "src"): LinkKind.IMAGE,
    ("script", "src"): LinkKind.SCRIPT,
    ("link", "href"): LinkKind.STYLESHEET,
    ("iframe", "src"): LinkKind.FRAME,
}
"""Which element and attribute pairs carry a link, and what kind it is.

Deliberately a table rather than a chain of conditions: teaching the crawler
about ``<video src>`` or ``<source srcset>`` is one entry, not a code change.
"""

NON_CONTENT_TAGS = frozenset({"script", "style"})
"""Elements whose text content is code, not prose."""

DEFAULT_MAX_LINKS = 10_000
"""How many links one page may contribute before the rest are dropped.

A page is written by a stranger. Without a ceiling, a generated document with a
million anchors would be answered by holding a million strings.
"""

_META_REFRESH_URL = re.compile(r"""^\s*[\d.]*\s*[;,]?\s*url\s*=\s*['"]?([^'"\s]+)""", re.IGNORECASE)
"""Extracts the target from a ``content`` attribute such as ``5; url=/next``."""


@runtime_checkable
class HtmlParser(Protocol):
    """Turns the markup of a page into what it declares."""

    def parse(self, text: str) -> ParsedHtml:
        """Return the declarations found in *text*.

        Implementations never raise for malformed markup. A page that cannot be
        parsed to the end yields what was collected before the trouble started,
        because half a page of links is worth more than none.
        """
        ...


class _Collector(HTMLParser):
    """Collects link targets, the base, the title, and prose."""

    def __init__(self, *, max_links: int) -> None:
        super().__init__(convert_charrefs=True)
        self._max_links = max_links
        self.base_href: str | None = None
        self.title: str | None = None
        self.canonical_href: str | None = None
        self.links: list[RawLink] = []
        self.text_parts: list[str] = []
        self.truncated = False
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if name in NON_CONTENT_TAGS:
            self._skip_depth += 1
        if name == "title":
            self._in_title = True
        values = {key.lower(): value for key, value in attrs}
        if name == "base":
            self._remember_base(values)
        elif name == "meta":
            self._remember_meta_refresh(values)
        if name == "link":
            self._remember_canonical(values)
        self._remember_links(name, values)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Treat ``<img />`` exactly like ``<img>``.

        The base class routes a self-closing tag here and *not* to
        ``handle_starttag``, so an XHTML page would otherwise yield no links at
        all.
        """
        self.handle_starttag(tag, attrs)
        if tag.lower() in NON_CONTENT_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in NON_CONTENT_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if name == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and self.title is None:
            stripped = data.strip()
            if stripped:
                self.title = stripped
        if self._skip_depth == 0:
            self.text_parts.append(data)

    def _remember_base(self, values: dict[str, str | None]) -> None:
        """Record the first ``<base href>``; the standard ignores later ones."""
        href = _clean(values.get("href"))
        if href is not None and self.base_href is None:
            self.base_href = href

    def _remember_canonical(self, values: dict[str, str | None]) -> None:
        """Record ``<link rel="canonical">``, which is metadata, not a link."""
        rel = (values.get("rel") or "").lower().split()
        href = _clean(values.get("href"))
        if "canonical" in rel and href is not None and self.canonical_href is None:
            self.canonical_href = href

    def _remember_meta_refresh(self, values: dict[str, str | None]) -> None:
        """Record the target of ``<meta http-equiv="refresh">``."""
        equiv = (values.get("http-equiv") or "").strip().lower()
        if equiv != "refresh":
            return
        match = _META_REFRESH_URL.match(values.get("content") or "")
        if match is None:
            return
        self._add(RawLink(match.group(1), LinkKind.REDIRECT, "meta", "content"))

    def _remember_links(self, tag: str, values: dict[str, str | None]) -> None:
        """Record every link the element table claims for *tag*."""
        for (source_tag, attribute), kind in LINK_SOURCES.items():
            if source_tag != tag:
                continue
            target = _clean(values.get(attribute))
            if target is not None:
                self._add(RawLink(target, kind, tag, attribute))

    def _add(self, link: RawLink) -> None:
        """Append *link* unless the ceiling has been reached."""
        if len(self.links) >= self._max_links:
            self.truncated = True
            return
        self.links.append(link)


class HtmlLinkParser:
    """An :class:`HtmlParser` built on the standard library.

    Character references are resolved in both text and attribute values, so
    ``?a=1&amp;b=2`` arrives as the URL the page meant rather than as the one
    it had to escape.
    """

    def __init__(self, *, max_links: int = DEFAULT_MAX_LINKS) -> None:
        if max_links < 1:
            msg = "max_links must be at least 1"
            raise ValueError(msg)
        self._max_links = max_links

    def parse(self, text: str) -> ParsedHtml:
        """Return what *text* declares, never raising for bad markup."""
        collector = _Collector(max_links=self._max_links)
        try:
            collector.feed(text)
            collector.close()
        except (AssertionError, ValueError):
            # HTMLParser is tolerant but not total, and a page is written by a
            # stranger. Whatever was collected before the trouble is still a
            # true statement about the document, and a crawl that stops over
            # one malformed page is worse than one that reads it partially.
            pass
        return ParsedHtml(
            base_href=collector.base_href,
            title=collector.title,
            canonical_href=collector.canonical_href,
            raw_links=tuple(collector.links),
            text="".join(collector.text_parts),
            truncated=collector.truncated,
        )


def _clean(value: str | None) -> str | None:
    """Return *value* without surrounding or embedded whitespace, or ``None``.

    Tabs, newlines, and carriage returns are removed outright: they are legal
    inside an ``href`` that a page wrapped across lines, and the URL standard
    strips them rather than encoding them.
    """
    if value is None:
        return None
    cleaned = value.strip().translate(_WHITESPACE)
    return cleaned or None


_WHITESPACE = str.maketrans("", "", "\t\n\r\f\v")
"""Characters a URL standard removes from a reference before parsing it."""
