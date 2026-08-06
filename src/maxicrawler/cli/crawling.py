"""Rendering of crawl results for the terminal.

Both renderers are pure, so the wording and the JSON shape can be tested
without fetching anything. The discovery half of the report is
:func:`~maxicrawler.cli.summary.render_summary` verbatim, which is what keeps
``crawl`` and ``discover`` reading the same way.
"""

import json

from maxicrawler.cli.summary import render_summary
from maxicrawler.web import CrawlResult, LinkKind

EXIT_CRAWLED = 0
"""The page was fetched and read."""

EXIT_FETCH_FAILED = 5
"""The page could not be retrieved at all."""

EXIT_NOT_A_PAGE = 6
"""Something answered, but it was not HTML."""

_KIND_LABELS: dict[LinkKind, str] = {
    LinkKind.ANCHOR: "anchor",
    LinkKind.IMAGE: "image",
    LinkKind.SCRIPT: "script",
    LinkKind.STYLESHEET: "stylesheet",
    LinkKind.FRAME: "frame",
    LinkKind.REDIRECT: "meta refresh",
    LinkKind.TEXT: "plain text",
}


def render_crawl(result: CrawlResult) -> str:
    """Return the terminal report for *result*."""
    lines = [
        f"Fetched:   {result.requested_url}",
        f"Status:    {result.page.status} {_describe_body(result)}",
    ]
    if result.was_redirected:
        lines.append(f"Redirects: {len(result.redirects)} -> {result.final_url}")
    if result.document.base_url != result.final_url:
        lines.append(f"Base URL:  {result.document.base_url}")
    if result.document.title:
        lines.append(f"Title:     {result.document.title}")
    if result.document.canonical_url:
        lines.append(f"Canonical: {result.document.canonical_url}")
    lines.extend(("", f"Links found: {result.link_count}"))
    for kind, count in result.links_by_kind().items():
        lines.append(f"  {_KIND_LABELS[kind]}: {count}")
    if result.skipped_links:
        lines.append(f"Skipped (not HTTP(S)): {result.skipped_links}")
    if result.document.truncated:
        lines.append("Note: the page holds more links than the configured limit.")
    lines.extend(("", render_summary(result.summary)))
    return "\n".join(lines)


def render_crawl_json(result: CrawlResult) -> str:
    """Return *result* as a JSON document.

    The same shape a future API or user interface would serialize, which is
    why it states both URLs rather than only the one that answered.
    """
    document = {
        "requested_url": result.requested_url,
        "final_url": result.final_url,
        "redirects": list(result.redirects),
        "status": result.page.status,
        "content_type": result.page.content_type,
        "content_encoding": result.page.content_encoding,
        "encoding": result.page.encoding,
        "size": result.page.size,
        "base_url": result.document.base_url,
        "title": result.document.title,
        "canonical_url": result.document.canonical_url,
        "links": [
            {
                "raw_url": link.raw_url,
                "url": link.resolved_url,
                "kind": str(link.kind),
                "tag": link.tag,
                "attribute": link.attribute,
            }
            for link in result.links
        ],
        "skipped_links": result.skipped_links,
        "truncated": result.document.truncated,
        "discovery": {
            "documents_processed": result.summary.documents_processed,
            "total_urls": result.summary.total_urls,
            "unique_urls": result.summary.unique_urls,
            "duplicates_removed": result.summary.duplicates_removed,
            "unresolved_urls": result.summary.statistics.unresolved_urls,
            "plugin_usage": [
                {"name": usage.name, "count": usage.count} for usage in result.summary.plugin_usage
            ],
        },
    }
    return json.dumps(document, indent=2, sort_keys=False)


def _describe_body(result: CrawlResult) -> str:
    """Return the parenthesised description of what came back."""
    parts = [result.page.content_type or "no content type", result.page.encoding]
    if result.page.content_encoding:
        parts.append(result.page.content_encoding)
    return f"{parts[0]} ({', '.join(parts[1:])}, {result.page.size} bytes)"
