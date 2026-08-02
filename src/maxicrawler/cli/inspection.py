"""Rendering of resource inspections for the terminal.

Both renderers are pure, so the wording and the JSON shape can be tested
without performing an inspection. Neither of them can emit a credential: a
:class:`~maxicrawler.domain.providers.ResourceSecret` is never read here, and
the reference URL it belongs to has already had its fragment removed.
"""

import json
from collections.abc import Mapping
from typing import Any

from maxicrawler.domain import (
    Availability,
    ProviderInfo,
    ResourceEntry,
    ResourceInspection,
    ResourceKind,
)

SIZE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")
"""Decimal units, so a size matches what the provider advertises."""

_AVAILABILITY_TEXT: Mapping[Availability, str] = {
    Availability.AVAILABLE: "Yes",
    Availability.NOT_FOUND: "No (not found)",
    Availability.ACCESS_DENIED: "No (access denied)",
    Availability.BLOCKED: "No (blocked by the provider)",
    Availability.RATE_LIMITED: "Unknown (provider is rate limiting)",
    Availability.QUOTA_EXCEEDED: "Unknown (quota exceeded)",
    Availability.UNKNOWN: "Unknown",
}

UNREADABLE_NAME = "unavailable (encrypted)"
"""Shown when a resource exists but its name needs a key we do not have."""

EXIT_AVAILABLE = 0
"""The resource was reached."""

EXIT_UNAVAILABLE = 2
"""The provider stated that the resource is gone, revoked, or blocked."""

EXIT_UNDETERMINED = 3
"""No statement could be obtained, so the resource may well still exist."""


def exit_code_for(availability: Availability) -> int:
    """Return the process exit code that reports *availability*.

    A rate-limited or failed lookup is kept distinct from a resource that is
    genuinely gone, so a script checking links cannot mistake one for the other.
    """
    if availability.is_available:
        return EXIT_AVAILABLE
    return EXIT_UNAVAILABLE if availability.is_determined else EXIT_UNDETERMINED


def format_size(size: int | None) -> str:
    """Return *size* in bytes as a short human-readable string."""
    if size is None:
        return "unknown"
    if size < 1000:
        return f"{size} B"
    value = float(size)
    for unit in SIZE_UNITS[1:]:
        value /= 1000
        if value < 1000:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} {SIZE_UNITS[-1]}"


def format_availability(availability: Availability) -> str:
    """Return the human-readable verdict for *availability*."""
    return _AVAILABILITY_TEXT.get(availability, "Unknown")


def render_inspection(inspection: ResourceInspection, provider: ProviderInfo) -> str:
    """Return the terminal report for *inspection*."""
    lines = [
        f"Provider: {provider.label}",
        f"Type: {inspection.kind.value.title()}",
    ]
    metadata = inspection.metadata
    if metadata is not None:
        lines.append(f"Name: {metadata.name or UNREADABLE_NAME}")
        lines.append(f"Size: {format_size(inspection.total_size)}")
    lines.append(f"Available: {format_availability(inspection.availability)}")
    if metadata is not None and inspection.kind is ResourceKind.FOLDER:
        lines.extend(_container_lines(inspection))
    if metadata is not None and not inspection.names_available:
        lines.append("")
        lines.append("Names stay encrypted: the link carries no usable decryption key.")
    return "\n".join(lines)


def render_json(inspection: ResourceInspection, provider: ProviderInfo) -> str:
    """Return the machine-readable report for *inspection*.

    The document describes the resource, never the credential: only whether a
    key travelled with the link is reported, and never the key itself.
    """
    return json.dumps(inspection_document(inspection, provider), indent=2)


def inspection_document(inspection: ResourceInspection, provider: ProviderInfo) -> dict[str, Any]:
    """Return the serializable description of *inspection*."""
    metadata = inspection.metadata
    document: dict[str, Any] = {
        "provider": provider.name,
        "resource_id": inspection.ref.resource_id,
        "url": inspection.ref.url,
        "type": inspection.kind.value,
        "availability": inspection.availability.value,
        "available": inspection.availability.is_available,
        "has_key": inspection.ref.has_secret,
        "names_available": inspection.names_available,
        "name": metadata.name if metadata is not None else None,
        "size": inspection.total_size,
    }
    if metadata is not None and metadata.modified_at is not None:
        document["modified_at"] = metadata.modified_at.isoformat()
    if metadata is not None and inspection.kind is ResourceKind.FOLDER:
        document["file_count"] = inspection.file_count
        document["folder_count"] = inspection.folder_count
        document["truncated"] = inspection.truncated
        document["entries"] = [_entry_document(entry) for entry in inspection.entries]
    return document


def _container_lines(inspection: ResourceInspection) -> list[str]:
    """Return the lines describing what a container holds."""
    lines = [
        f"Files: {inspection.file_count}",
        f"Folders: {inspection.folder_count}",
    ]
    if not inspection.entries:
        return lines
    width = max(len(_entry_label(entry)) for entry in inspection.entries)
    lines.extend(("", "Contents:"))
    lines.extend(
        f"  {_entry_label(entry).ljust(width)}  {_entry_size(entry)}".rstrip()
        for entry in inspection.entries
    )
    if inspection.truncated:
        lines.append("  ... more entries were not listed")
    return lines


def _entry_label(entry: ResourceEntry) -> str:
    """Return the display name of *entry*, marking folders with a slash."""
    name = entry.metadata.name or entry.ref.resource_id
    return f"{name}/" if entry.metadata.kind is ResourceKind.FOLDER else name


def _entry_size(entry: ResourceEntry) -> str:
    """Return the size column of *entry*; folders report none."""
    if entry.metadata.kind is ResourceKind.FOLDER:
        return ""
    return format_size(entry.metadata.size)


def _entry_document(entry: ResourceEntry) -> dict[str, Any]:
    """Return the serializable description of one container entry."""
    return {
        "resource_id": entry.ref.resource_id,
        "type": entry.metadata.kind.value,
        "name": entry.metadata.name,
        "size": entry.metadata.size,
    }
