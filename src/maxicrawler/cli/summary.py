"""Rendering of discovery summaries for the terminal."""

from maxicrawler.crawler import DiscoverySummary


def render_summary(summary: DiscoverySummary) -> str:
    """Return the concise terminal report for *summary*.

    The function is pure so the wording can be tested without running a
    discovery session.
    """
    lines = [
        f"Documents processed: {summary.documents_processed}",
        f"URLs discovered: {summary.total_urls}",
        f"Unique URLs: {summary.unique_urls}",
        f"Duplicates removed: {summary.duplicates_removed}",
    ]
    if summary.statistics.unresolved_urls:
        lines.append(f"Unresolved URLs: {summary.statistics.unresolved_urls}")
    lines.extend(("", "Plugin usage:"))
    if summary.plugin_usage:
        lines.extend(f"{usage.name}: {usage.count}" for usage in summary.plugin_usage)
    else:
        lines.append("none")
    return "\n".join(lines)
