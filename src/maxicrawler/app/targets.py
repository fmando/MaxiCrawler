"""What a URL says it points at.

The question a report is actually asked — *"show me the documents"*, *"show me
the images"* — and it is not the question
:class:`~maxicrawler.web.models.LinkKind` answers. That one records how a
reference was *written*: an ``<img src>`` is an image and an ``<a href>`` is an
anchor, so a link to a photograph written as a link is not an image and a
tracking pixel is. It describes the source of the reference, which is the right
thing for a crawler to count and the wrong thing to filter a report by.

This table describes the target instead, and it can do so without a request,
without a stored file, and without a schema change — which is why it exists as a
table rather than as a column somebody has to migrate into ``discovered_urls``.

Four decisions are worth stating.

**Only the path is read.** The query string and the fragment are cut away first.
That is not tidiness: a Mega share carries its decryption key in the fragment,
and a key is forty random characters that will eventually contain ``.png``.
Classifying a share link as an image because of its key would be a bug that
appears once in a few hundred links and looks like nothing else.

**A URL that does not say is :attr:`TargetKind.UNKNOWN`, not a guess.** Most
pages on most sites have no suffix at all. Calling those pages would be right
often enough to be trusted and wrong often enough to matter, and a filter nobody
can trust is worse than one that admits what it does not know.
:attr:`TargetKind.PAGE` therefore means *the URL names a page file*, and nothing
weaker.

**The table is explicit, and :mod:`mimetypes` is not used.** The same rule
:mod:`maxicrawler.app.viewing` follows, for the same measured reason: that module
reads the Windows registry, so its answers differ between one machine and the
next.

**An ambiguous suffix is left out rather than guessed at.** ``.ts`` is a video
transport stream and it is TypeScript; ``.dat`` is anything at all. A suffix that
would be wrong for a whole class of links earns no entry here, and the URLs
carrying it are honestly unknown.
"""

from enum import StrEnum
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit


class TargetKind(StrEnum):
    """What kind of thing a URL names.

    Ordered the way a report lists them, which is the order they are worth
    scanning in: the things somebody crawled a page to collect first, the
    things a site is built from after, and *"the URL does not say"* last.
    """

    DOCUMENT = "document"
    IMAGE = "image"
    ARCHIVE = "archive"
    VIDEO = "video"
    AUDIO = "audio"
    PAGE = "page"
    """The URL names a page file — ``.html`` and its relatives.

    Not "this is a page". A URL with no suffix is far more often a page than
    this is, and is deliberately :attr:`UNKNOWN`.
    """

    UNKNOWN = "unknown"
    """The path names no suffix this table knows."""


TARGETS: dict[str, TargetKind] = {
    # Documents somebody would collect.
    ".pdf": TargetKind.DOCUMENT,
    ".doc": TargetKind.DOCUMENT,
    ".docx": TargetKind.DOCUMENT,
    ".odt": TargetKind.DOCUMENT,
    ".rtf": TargetKind.DOCUMENT,
    ".txt": TargetKind.DOCUMENT,
    ".md": TargetKind.DOCUMENT,
    ".markdown": TargetKind.DOCUMENT,
    ".epub": TargetKind.DOCUMENT,
    ".mobi": TargetKind.DOCUMENT,
    ".azw3": TargetKind.DOCUMENT,
    ".djvu": TargetKind.DOCUMENT,
    ".xls": TargetKind.DOCUMENT,
    ".xlsx": TargetKind.DOCUMENT,
    ".ods": TargetKind.DOCUMENT,
    ".csv": TargetKind.DOCUMENT,
    ".tsv": TargetKind.DOCUMENT,
    ".ppt": TargetKind.DOCUMENT,
    ".pptx": TargetKind.DOCUMENT,
    ".odp": TargetKind.DOCUMENT,
    # Images.
    ".png": TargetKind.IMAGE,
    ".jpg": TargetKind.IMAGE,
    ".jpeg": TargetKind.IMAGE,
    ".gif": TargetKind.IMAGE,
    ".webp": TargetKind.IMAGE,
    ".bmp": TargetKind.IMAGE,
    ".ico": TargetKind.IMAGE,
    ".avif": TargetKind.IMAGE,
    ".svg": TargetKind.IMAGE,
    ".tif": TargetKind.IMAGE,
    ".tiff": TargetKind.IMAGE,
    ".heic": TargetKind.IMAGE,
    ".heif": TargetKind.IMAGE,
    ".psd": TargetKind.IMAGE,
    # Archives and disc images.
    ".zip": TargetKind.ARCHIVE,
    ".rar": TargetKind.ARCHIVE,
    ".7z": TargetKind.ARCHIVE,
    ".tar": TargetKind.ARCHIVE,
    ".tgz": TargetKind.ARCHIVE,
    ".gz": TargetKind.ARCHIVE,
    ".bz2": TargetKind.ARCHIVE,
    ".xz": TargetKind.ARCHIVE,
    ".zst": TargetKind.ARCHIVE,
    ".iso": TargetKind.ARCHIVE,
    # Video.
    ".mp4": TargetKind.VIDEO,
    ".m4v": TargetKind.VIDEO,
    ".mkv": TargetKind.VIDEO,
    ".avi": TargetKind.VIDEO,
    ".mov": TargetKind.VIDEO,
    ".webm": TargetKind.VIDEO,
    ".wmv": TargetKind.VIDEO,
    ".flv": TargetKind.VIDEO,
    ".mpg": TargetKind.VIDEO,
    ".mpeg": TargetKind.VIDEO,
    # Audio.
    ".mp3": TargetKind.AUDIO,
    ".flac": TargetKind.AUDIO,
    ".wav": TargetKind.AUDIO,
    ".ogg": TargetKind.AUDIO,
    ".oga": TargetKind.AUDIO,
    ".opus": TargetKind.AUDIO,
    ".m4a": TargetKind.AUDIO,
    ".aac": TargetKind.AUDIO,
    ".wma": TargetKind.AUDIO,
    # Pages, in the narrow sense this module means it.
    ".html": TargetKind.PAGE,
    ".htm": TargetKind.PAGE,
    ".xhtml": TargetKind.PAGE,
}
"""Every suffix this release recognises, and nothing else.

An allow-list rather than a rule, so a suffix nobody thought about is honestly
unknown instead of quietly miscounted. ``.php``, ``.asp`` and their relatives
are absent on purpose: they name how a page is produced, not what comes back,
and a report that filed them under anything would be filing a server's
implementation detail.
"""


def target_of(url: str) -> TargetKind:
    """Return what *url* says it points at.

    Reads the path and only the path. A query string can name anything at all —
    ``?next=/a.pdf`` is a redirect target, not this URL's own content — and a
    fragment can be a credential, which must never decide anything visible.
    """
    return TARGETS.get(suffix_of(url), TargetKind.UNKNOWN)


def suffix_of(url: str) -> str:
    """Return the lowercase suffix of *url*'s path, or an empty string.

    Percent-encoding is undone before the suffix is taken, because ``%2E`` is a
    dot to every server that will answer this URL, and a link written that way
    points at exactly the same file as one that was not.
    """
    try:
        path = urlsplit(url.strip()).path
    except ValueError:
        # An address urlsplit refuses -- a bracketed host that is not an
        # address, most often. Nothing here needs to know which: a URL that
        # cannot be read names no suffix.
        return ""
    return PurePosixPath(unquote(path)).suffix.lower()
