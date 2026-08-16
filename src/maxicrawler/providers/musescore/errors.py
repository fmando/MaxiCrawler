"""What can go wrong between a score page and a file, in the vocabulary above.

Three failures are separated here because three different people fix them, and
a queue that treated them alike would spend a day's allowance discovering the
same thing twenty times.

:class:`SessionExpiredError` is fixed by exporting a session again.
:class:`ChallengeEncounteredError` is fixed by nobody: solving a bot check is a
non-goal, so meeting one is where this program stops and says so.
:class:`ScorePageError` means the page changed shape, which is a bug here.

All three are :class:`~maxicrawler.providers.errors.ProviderError` subclasses,
so everything that already handles provider failures keeps working; the
distinctions matter only to the caller that wants to act on them.
"""

from maxicrawler.providers.errors import ProviderError


class MuseScoreError(ProviderError):
    """Base class for every MuseScore failure."""


class ScorePageError(MuseScoreError):
    """Raised when a score page holds no state this can read.

    The page changed, or something that is not a score page answered. Retrying
    will not help, which is why this is not a transport error.
    """


class SessionExpiredError(MuseScoreError):
    """Raised when the host did not honour the session it was given.

    A queue meeting this should stop and ask for a new session rather than
    continue: every remaining request would fail the same way, and failing them
    all would turn one expired cookie into a backlog of dead entries.
    """


class ChallengeEncounteredError(MuseScoreError):
    """Raised when a bot check answered instead of the page.

    **This is not a problem to be solved.** MaxiCrawler does not answer
    challenges (VISION.md), so this exists to carry the fact upward intact: the
    queue pauses, the interface says what happened, and a person decides what
    to do about it.
    """
