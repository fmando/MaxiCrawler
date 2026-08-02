"""The library layer: how downloaded resources are stored and managed.

The provider layer in :mod:`maxicrawler.providers` answers *"what can I do with
this resource?"*. This layer answers the last question in the chain, *"where
does what we fetched live?"*, and is the only place in MaxiCrawler that decides
a path on disk for a downloaded payload.

It knows nothing about providers, transfers, or queues: a Mega file, a
Pixeldrain file, and a GoFile entry are stored by exactly the same rules.
"""

from maxicrawler.library.errors import (
    LibraryError,
    LibraryLayoutError,
    LibraryRecordError,
)
from maxicrawler.library.naming import (
    FALLBACK_FILENAME,
    MAX_FILENAME_LENGTH,
    provider_directory,
    resource_key,
    safe_filename,
)
from maxicrawler.library.records import (
    CONTENT_DIRECTORY,
    METADATA_FILENAME,
    RECORD_SCHEMA,
    ContentRecord,
    ResourceRecord,
    new_record,
)
from maxicrawler.library.store import (
    DEFAULT_LIBRARY_PATH,
    DESCRIPTOR_FILENAME,
    LIBRARY_SCHEMA,
    STAGING_DIRECTORY,
    Library,
    LibraryEntry,
)

__all__ = [
    "CONTENT_DIRECTORY",
    "DEFAULT_LIBRARY_PATH",
    "DESCRIPTOR_FILENAME",
    "FALLBACK_FILENAME",
    "LIBRARY_SCHEMA",
    "MAX_FILENAME_LENGTH",
    "METADATA_FILENAME",
    "RECORD_SCHEMA",
    "STAGING_DIRECTORY",
    "ContentRecord",
    "Library",
    "LibraryEntry",
    "LibraryError",
    "LibraryLayoutError",
    "LibraryRecordError",
    "ResourceRecord",
    "new_record",
    "provider_directory",
    "resource_key",
    "safe_filename",
]
