"""Translation between Mega's wire vocabulary and MaxiCrawler's domain models.

Keeping the tables here means the API client stays about the wire, the
provider stays about orchestration, and neither of them spells out a magic
number.
"""

from collections.abc import Mapping
from datetime import UTC, datetime

from maxicrawler.domain import Availability, ResourceKind

ERROR_NAMES: Mapping[int, str] = {
    -1: "EINTERNAL",
    -2: "EARGS",
    -3: "EAGAIN",
    -4: "ERATELIMIT",
    -5: "EFAILED",
    -6: "ETOOMANY",
    -7: "ERANGE",
    -8: "EEXPIRED",
    -9: "ENOENT",
    -10: "ECIRCULAR",
    -11: "EACCESS",
    -12: "EEXIST",
    -13: "EINCOMPLETE",
    -14: "EKEY",
    -15: "ESID",
    -16: "EBLOCKED",
    -17: "EOVERQUOTA",
    -18: "ETEMPUNAVAIL",
}
"""The negative status codes Mega returns, as named by its own SDK."""

RETRYABLE_ERRORS = frozenset({-3, -4, -6})
"""Codes that mean *ask again later*: EAGAIN, ERATELIMIT, and ETOOMANY."""

_AVAILABILITY: Mapping[int, Availability] = {
    -2: Availability.ACCESS_DENIED,
    -3: Availability.RATE_LIMITED,
    -4: Availability.RATE_LIMITED,
    -6: Availability.RATE_LIMITED,
    -8: Availability.ACCESS_DENIED,
    -9: Availability.NOT_FOUND,
    -11: Availability.ACCESS_DENIED,
    -14: Availability.ACCESS_DENIED,
    -16: Availability.BLOCKED,
    -17: Availability.QUOTA_EXCEEDED,
}
"""How a status code answers *"can this resource still be reached?"*.

``EARGS`` and ``EKEY`` land on :attr:`Availability.ACCESS_DENIED` because for a
public link they mean the same thing in practice: the link as published is not
usable. Codes that describe our own request rather than the resource — an
internal error, a temporary outage — stay :attr:`Availability.UNKNOWN`.
"""

NODE_FILE = 0
NODE_FOLDER = 1
NODE_ROOT = 2

_NODE_KINDS: Mapping[int, ResourceKind] = {
    NODE_FILE: ResourceKind.FILE,
    NODE_FOLDER: ResourceKind.FOLDER,
    NODE_ROOT: ResourceKind.FOLDER,
    3: ResourceKind.FOLDER,
    4: ResourceKind.FOLDER,
}
"""Mega node types; 3 and 4 are the inbox and rubbish bin of a full account."""


def error_name(code: int) -> str:
    """Return Mega's name for *code*, or a readable placeholder."""
    return ERROR_NAMES.get(code, "EUNKNOWN")


def is_retryable(code: int) -> bool:
    """Return whether *code* means the request should be repeated later."""
    return code in RETRYABLE_ERRORS


def availability_for(code: int) -> Availability:
    """Return what *code* says about the reachability of a resource."""
    return _AVAILABILITY.get(code, Availability.UNKNOWN)


def node_kind(value: object) -> ResourceKind:
    """Return the resource kind of a node type, tolerating an unknown one."""
    if not isinstance(value, int) or isinstance(value, bool):
        return ResourceKind.UNKNOWN
    return _NODE_KINDS.get(value, ResourceKind.UNKNOWN)


def node_size(value: object) -> int | None:
    """Return a node size, ignoring anything that is not a plain integer."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def node_timestamp(value: object) -> datetime | None:
    """Return a node timestamp as an aware UTC datetime, if it is usable."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
