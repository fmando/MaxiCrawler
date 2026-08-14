"""What a browser may be asked to display, and how.

One table, read by whoever needs it. The rule it encodes is the whole viewer:
**MaxiCrawler renders nothing itself.** It does not parse a PDF, does not turn
Markdown into HTML, does not convert an image. It states a content type, hands
the bytes over, and lets the browser do what browsers already do well.

Three decisions are worth stating before the table.

**The table is explicit; :mod:`mimetypes` is not used.** That module reads the
Windows registry, so the type of a ``.webp`` differs between a developer's
machine and the CI that is supposed to check it — measured, not assumed: this
project's Windows install has no entry for ``.webp`` at all. A content type
decides whether a browser executes something, which makes "it depends on the
machine" the wrong property for it to have.

**Markdown is served as plain text.** No browser renders Markdown, and
``text/markdown`` makes Chrome download the file instead of showing it. Turning
it into HTML would mean rendering it ourselves. Showing the source is therefore
not a shortcut; it is the only reading of "let the browser display it" that is
also "do not convert it".

**Two of these types are executable code.** HTML and SVG can carry script, and
script served from this application's own origin can reach everything the
interface can — there is no authentication in front of it. Which types those are
is recorded here as :attr:`MediaVerdict.is_script_capable` so the delivery layer
cannot forget; what it does about them is that layer's business.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from maxicrawler.utils import format_size

DEFAULT_MAX_VIEW_BYTES = 32 * 1024 * 1024
"""How large a file may be before the viewer declines to show it.

A ceiling rather than a promise: a browser handed a 400 MB text file stops
answering, and "here is a link to download it" is a better page than one that
hangs.
"""

DOWNLOAD_CONTENT_TYPE = "application/octet-stream"
"""What a file offered for download is called, whatever it turns out to be.

Deliberately uninformative. A download states no type, so no browser gets to
decide to render it, and the one route that does state a type is the one that
went through this table first.
"""


class Display(StrEnum):
    """How a page should embed a file it is showing."""

    IFRAME = "iframe"
    """A document in its own frame: a PDF, plain text, a stored HTML page."""

    IMAGE = "image"
    """An image element. Also the answer for SVG, because an ``<img>`` runs no
    script even when the file it points at contains some."""

    NONE = "none"
    """Not shown. :attr:`MediaVerdict.reason` says why."""


@dataclass(frozen=True, slots=True)
class MediaVerdict:
    """What may be done with one stored file."""

    content_type: str
    display: Display
    reason: str | None = None
    """Why it is not shown, when it is not. ``None`` when it is."""

    @property
    def can_display(self) -> bool:
        """Return whether a browser will be asked to show this at all."""
        return self.display is not Display.NONE

    @property
    def is_script_capable(self) -> bool:
        """Return whether this type can execute script in our own origin.

        True for HTML and SVG and nothing else. A PDF may contain script, but it
        runs inside the browser's PDF viewer rather than in the page that framed
        it; an image and plain text cannot execute anything at all.
        """
        return self.content_type in _SCRIPT_CAPABLE


PLAIN_TEXT = "text/plain; charset=utf-8"
"""Declared for every text-shaped file.

The encoding is asserted rather than detected, because detecting it would be
interpreting the file. A document in some other encoding shows the wrong
characters — visibly wrong, and one download away from being read properly.
"""

HTML = "text/html; charset=utf-8"
SVG = "image/svg+xml"

_SCRIPT_CAPABLE = frozenset({HTML, SVG})
"""The two types whose content runs in the origin that served it."""

VIEWABLE: dict[str, tuple[str, Display]] = {
    ".pdf": ("application/pdf", Display.IFRAME),
    ".png": ("image/png", Display.IMAGE),
    ".jpg": ("image/jpeg", Display.IMAGE),
    ".jpeg": ("image/jpeg", Display.IMAGE),
    ".gif": ("image/gif", Display.IMAGE),
    ".webp": ("image/webp", Display.IMAGE),
    ".bmp": ("image/bmp", Display.IMAGE),
    ".ico": ("image/x-icon", Display.IMAGE),
    ".avif": ("image/avif", Display.IMAGE),
    ".svg": (SVG, Display.IMAGE),
    ".txt": (PLAIN_TEXT, Display.IFRAME),
    ".log": (PLAIN_TEXT, Display.IFRAME),
    ".csv": (PLAIN_TEXT, Display.IFRAME),
    ".tsv": (PLAIN_TEXT, Display.IFRAME),
    ".json": (PLAIN_TEXT, Display.IFRAME),
    ".xml": (PLAIN_TEXT, Display.IFRAME),
    ".yaml": (PLAIN_TEXT, Display.IFRAME),
    ".yml": (PLAIN_TEXT, Display.IFRAME),
    ".toml": (PLAIN_TEXT, Display.IFRAME),
    ".ini": (PLAIN_TEXT, Display.IFRAME),
    ".md": (PLAIN_TEXT, Display.IFRAME),
    ".markdown": (PLAIN_TEXT, Display.IFRAME),
    ".html": (HTML, Display.IFRAME),
    ".htm": (HTML, Display.IFRAME),
}
"""Every suffix this release will show, and nothing else.

An allow-list, so a type nobody thought about is a download rather than a
guess. ``.xml`` is plain text on purpose: served as XML it could carry a
stylesheet and become script-capable, and nobody asked to view XML *rendered*.
"""


class MediaKind(StrEnum):
    """What sort of thing a stored file is, for somebody sorting through them.

    A different question from :data:`VIEWABLE`, and kept in a different table
    for a reason worth stating. That one is a **security boundary**: an
    allow-list of what may be handed to a browser with a content type, where
    everything absent is a download and a suffix nobody thought about is
    therefore harmless. This one is an **estimate**, and is allowed to be
    generous — a ``.rar`` gets a category and still gets no content type.

    Merging them would tie a filter's vocabulary to a decision about executing
    code, so that adding "show me the archives" meant editing the list that
    decides what a browser may run.
    """

    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    PDF = "pdf"
    """Its own kind rather than a document, because it is the one document type
    every browser here renders, and because it is what most of a crawl's
    keepable output turns out to be."""

    DOCUMENT = "document"
    """Word processing, spreadsheets, presentations, e-books, stored web pages."""

    ARCHIVE = "archive"
    TEXT = "text"
    OTHER = "other"
    """Everything with no category, including a file with no extension.

    Never an error: a library holds whatever a crawl found, and a suffix nobody
    listed is an ordinary thing to have rather than a fault to report.
    """

    @classmethod
    def parse(cls, value: str | None) -> "MediaKind | None":
        """Return the kind *value* names, or ``None`` when it names none.

        Lenient like :meth:`~maxicrawler.app.library.LibrarySort.parse`, and for
        the same reason: the value arrives in a query string, where a stale
        bookmark is ordinary and a refusal is worse than an unfiltered listing.
        """
        try:
            return cls(value or "")
        except ValueError:
            return None


KINDS: dict[str, MediaKind] = {
    # Images. `.svg` belongs here although it is markup: what a person is
    # looking for when they ask for pictures is what looks like one.
    ".png": MediaKind.IMAGE,
    ".jpg": MediaKind.IMAGE,
    ".jpeg": MediaKind.IMAGE,
    ".jfif": MediaKind.IMAGE,
    ".gif": MediaKind.IMAGE,
    ".webp": MediaKind.IMAGE,
    ".bmp": MediaKind.IMAGE,
    ".ico": MediaKind.IMAGE,
    ".avif": MediaKind.IMAGE,
    ".svg": MediaKind.IMAGE,
    ".tif": MediaKind.IMAGE,
    ".tiff": MediaKind.IMAGE,
    ".heic": MediaKind.IMAGE,
    ".heif": MediaKind.IMAGE,
    ".psd": MediaKind.IMAGE,
    # Video.
    ".mp4": MediaKind.VIDEO,
    ".m4v": MediaKind.VIDEO,
    ".mkv": MediaKind.VIDEO,
    ".webm": MediaKind.VIDEO,
    ".avi": MediaKind.VIDEO,
    ".mov": MediaKind.VIDEO,
    ".wmv": MediaKind.VIDEO,
    ".flv": MediaKind.VIDEO,
    ".mpg": MediaKind.VIDEO,
    ".mpeg": MediaKind.VIDEO,
    ".ts": MediaKind.VIDEO,
    ".m2ts": MediaKind.VIDEO,
    ".ogv": MediaKind.VIDEO,
    ".3gp": MediaKind.VIDEO,
    # Audio.
    ".mp3": MediaKind.AUDIO,
    ".m4a": MediaKind.AUDIO,
    ".aac": MediaKind.AUDIO,
    ".flac": MediaKind.AUDIO,
    ".ogg": MediaKind.AUDIO,
    ".oga": MediaKind.AUDIO,
    ".opus": MediaKind.AUDIO,
    ".wav": MediaKind.AUDIO,
    ".wma": MediaKind.AUDIO,
    ".aiff": MediaKind.AUDIO,
    ".aif": MediaKind.AUDIO,
    ".mid": MediaKind.AUDIO,
    ".midi": MediaKind.AUDIO,
    ".m3u": MediaKind.AUDIO,
    ".m3u8": MediaKind.AUDIO,
    # Documents, PDF apart.
    ".pdf": MediaKind.PDF,
    ".doc": MediaKind.DOCUMENT,
    ".docx": MediaKind.DOCUMENT,
    ".odt": MediaKind.DOCUMENT,
    ".rtf": MediaKind.DOCUMENT,
    ".xls": MediaKind.DOCUMENT,
    ".xlsx": MediaKind.DOCUMENT,
    ".ods": MediaKind.DOCUMENT,
    ".ppt": MediaKind.DOCUMENT,
    ".pptx": MediaKind.DOCUMENT,
    ".odp": MediaKind.DOCUMENT,
    ".epub": MediaKind.DOCUMENT,
    ".mobi": MediaKind.DOCUMENT,
    ".azw3": MediaKind.DOCUMENT,
    ".djvu": MediaKind.DOCUMENT,
    ".chm": MediaKind.DOCUMENT,
    ".html": MediaKind.DOCUMENT,
    ".htm": MediaKind.DOCUMENT,
    # Archives, disk images included: what a person means by "archive" is a
    # file they will have to open something else to get inside.
    ".zip": MediaKind.ARCHIVE,
    ".rar": MediaKind.ARCHIVE,
    ".7z": MediaKind.ARCHIVE,
    ".tar": MediaKind.ARCHIVE,
    ".gz": MediaKind.ARCHIVE,
    ".tgz": MediaKind.ARCHIVE,
    ".bz2": MediaKind.ARCHIVE,
    ".tbz2": MediaKind.ARCHIVE,
    ".xz": MediaKind.ARCHIVE,
    ".zst": MediaKind.ARCHIVE,
    ".lz": MediaKind.ARCHIVE,
    ".lzh": MediaKind.ARCHIVE,
    ".arj": MediaKind.ARCHIVE,
    ".cab": MediaKind.ARCHIVE,
    ".iso": MediaKind.ARCHIVE,
    ".dmg": MediaKind.ARCHIVE,
    # Text, which is also where Markdown sits. The viewer tells the two apart
    # when it renders a preview; a filter has no use for the distinction.
    ".txt": MediaKind.TEXT,
    ".log": MediaKind.TEXT,
    ".csv": MediaKind.TEXT,
    ".tsv": MediaKind.TEXT,
    ".json": MediaKind.TEXT,
    ".xml": MediaKind.TEXT,
    ".yaml": MediaKind.TEXT,
    ".yml": MediaKind.TEXT,
    ".toml": MediaKind.TEXT,
    ".ini": MediaKind.TEXT,
    ".cfg": MediaKind.TEXT,
    ".conf": MediaKind.TEXT,
    ".nfo": MediaKind.TEXT,
    ".srt": MediaKind.TEXT,
    ".vtt": MediaKind.TEXT,
    ".md": MediaKind.TEXT,
    ".markdown": MediaKind.TEXT,
}
"""Which category each suffix falls into; anything absent is :attr:`MediaKind.OTHER`.

Longer than :data:`VIEWABLE` on purpose. That table lists what a browser may be
shown, so a video and an archive are missing from it entirely — and those are
exactly the two a person sorting through a crawl most wants to separate out.
"""


def kind_for(filename: str | None) -> MediaKind:
    """Return what sort of file *filename* is, by its suffix alone.

    ``None`` and a name with no suffix both answer :attr:`MediaKind.OTHER`. The
    content is never opened: a category is a hint for sorting, and reading a
    thousand files to compute one would cost more than the sorting saves.
    """
    if not filename:
        return MediaKind.OTHER
    suffix = PurePosixPath(filename).suffix.lower()
    return KINDS.get(suffix, MediaKind.OTHER)


def verdict_for(
    filename: str, size: int | None = None, *, max_bytes: int = DEFAULT_MAX_VIEW_BYTES
) -> MediaVerdict:
    """Return what may be done with a file called *filename*.

    The suffix decides the type; *size* only decides whether the answer is used.
    A known type that is too large keeps its type and loses its display, so a
    page can say "a PDF, too large to show" rather than "unknown file".

    An unknown *size* is treated as within the limit. The library records a size
    for everything it stores, so the case does not arise from a stored entry —
    and refusing to show a file because nobody measured it would be the wrong
    way round.
    """
    suffix = PurePosixPath(filename).suffix.lower()
    known = VIEWABLE.get(suffix)
    if known is None:
        extension = suffix or "file with no extension"
        return MediaVerdict(
            content_type=DOWNLOAD_CONTENT_TYPE,
            display=Display.NONE,
            reason=f"nothing here can show a {extension} in a browser",
        )
    content_type, display = known
    if size is not None and size > max_bytes:
        return MediaVerdict(
            content_type=content_type,
            display=Display.NONE,
            reason=(
                f"the file is {format_size(size)}, above the viewer's "
                f"{format_size(max_bytes)} limit"
            ),
        )
    return MediaVerdict(content_type=content_type, display=display)
