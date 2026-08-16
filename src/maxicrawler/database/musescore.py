"""SQLite adapter for a worklist that is measured in weeks.

Everything else on the download side lives in memory and is emptied on
shutdown, deliberately (ADR-033): a queue is a person's afternoon, and a
half-finished transfer has nothing sensible to come back as. This one is
different in kind. MuseScore answers a subscription with twenty downloads a
day, so a few hundred pieces is a matter of weeks — a backlog that does not
survive a restart is not a backlog at all.

What survives here is an **intent**, not a transfer. "Fetch the PDF of score
4217351" means the same thing tomorrow as it did today, which is exactly what
ADR-033's objection was about and exactly why it does not apply: there is no
partial file to resume, only a line still to be crossed off.

**Why one table rather than two.** The obvious second table is a ledger of days
and how much each one spent. It is not needed: a day's spend *is* the number of
rows that settled on that day, so deriving it cannot drift out of step with the
rows it describes, while a counter kept beside them can. What the rows cannot
know is where a day begins — that is a policy about somebody else's reset time
— so the day a row counts against is written into it by the caller rather than
computed here. The database stays a record; the opinion lives above it.

Nothing about a session is written here, and no column exists for one. This is
precisely the file where a credential would end up on disk outliving every
reason to have kept it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from maxicrawler.database.sqlite import SQLiteDatabase

TABLE = "musescore_requests"
"""The one table this adapter owns."""

ADDED_COLUMNS: Mapping[str, str] = {}
"""Columns that arrived after ``musescore_requests`` was first released.

Empty, because there have not been any yet. It exists anyway so that the day
there is one, the pattern is already here to follow rather than to remember:
``CREATE TABLE IF NOT EXISTS`` does nothing to a table that already exists, so
a column added without an entry here is a column missing from every database
written by an earlier release. Each definition must carry a default, because an
existing row has to stay valid without being rewritten, and
``tests/test_musescore_requests.py`` asserts this mapping and the ``CREATE
TABLE`` below stay in step.
"""

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS musescore_requests (
        request_id TEXT PRIMARY KEY,
        score_id TEXT NOT NULL,
        format TEXT NOT NULL,
        score_url TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        state TEXT NOT NULL,
        position INTEGER NOT NULL DEFAULT 0,
        added_at TEXT NOT NULL,
        offered_at TEXT,
        settled_at TEXT,
        settled_day TEXT NOT NULL DEFAULT '',
        entry_key TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT ''
    )
    """,
    # The identity is the score and the rendering, never the URL: the same
    # score is reachable under a vanity profile and under a numeric one, and
    # queueing it twice would spend two days of an allowance on one file.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_musescore_requests_identity "
    "ON musescore_requests(score_id, format)",
    "CREATE INDEX IF NOT EXISTS idx_musescore_requests_state ON musescore_requests(state)",
    "CREATE INDEX IF NOT EXISTS idx_musescore_requests_day ON musescore_requests(settled_day)",
)


class RequestState(StrEnum):
    """Where one line of the worklist has got to."""

    WAITING = "waiting"
    """In the backlog, not yet asked for."""

    OFFERED = "offered"
    """On today's list. Handed to a person, not yet accounted for.

    Not the same as spent. A list of twenty that produced fifteen files spent
    fifteen, and the five nobody clicked are still owed — which is why an offer
    left over from an earlier day goes back to waiting rather than counting
    against anything.
    """

    STORED = "stored"
    """The file arrived and is in the library. This is what spends a day."""

    DROPPED = "dropped"
    """Taken off the list on purpose, and not offered again."""


@dataclass(frozen=True, slots=True)
class ScoreRequest:
    """One thing to fetch: a rendering of a score."""

    score_id: str
    format: str
    score_url: str
    title: str = ""


@dataclass(frozen=True, slots=True)
class StoredRequest:
    """One line of the worklist as it was persisted."""

    request_id: str
    score_id: str
    format: str
    score_url: str
    title: str
    state: RequestState
    position: int
    """Where this sits in the order the list was built.

    Its own column rather than a tie-break on ``added_at``, because a pasted
    collection of two hundred links arrives with one timestamp on all of it.
    Without this the drain order inside a batch would be whatever the row
    identifiers happened to sort as, which is to say random — and the order
    somebody put their list in is information worth keeping.
    """

    added_at: datetime
    offered_at: datetime | None
    settled_at: datetime | None
    settled_day: str
    entry_key: str
    note: str

    @property
    def label(self) -> str:
        """Return what to call this on a page a person reads."""
        stem = self.title or f"score {self.score_id}"
        return f"{stem} ({self.format})"


class SQLiteRequestQueue:
    """Stores a MuseScore worklist in SQLite.

    Composes :class:`SQLiteDatabase` rather than extending it, and opens a
    short-lived connection per operation, matching the existing adapters.
    """

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    @property
    def database(self) -> SQLiteDatabase:
        """Return the underlying database adapter."""
        return self._database

    def initialize(self) -> tuple[str, ...]:
        """Create the table, or bring an existing one up to date.

        Safe to call on every run, and it has to be: this is a backlog that
        outlives releases, so a database written months ago is the normal case
        rather than the awkward one.
        """
        with closing(self._database.connect()) as connection, connection:
            for statement in SCHEMA:
                connection.execute(statement)
        return self._database.add_missing_columns(TABLE, ADDED_COLUMNS)

    def add(self, requests: Iterable[ScoreRequest], *, now: datetime) -> tuple[StoredRequest, ...]:
        """Queue every request that is not already known, and return those.

        Already-known ones are **left exactly as they are** rather than reset.
        Queueing the same collection twice is the ordinary way somebody adds to
        a list, and it must not resurrect what was dropped on purpose or
        re-offer what already arrived.
        """
        added: list[str] = []
        with closing(self._database.connect()) as connection, connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(position), 0) AS last FROM musescore_requests"
            ).fetchone()
            position = 0 if row is None else int(row["last"])
            for request in requests:
                request_id = uuid4().hex
                position += 1
                cursor = connection.execute(
                    "INSERT INTO musescore_requests("
                    "request_id, score_id, format, score_url, title, state, position, added_at"
                    ") VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(score_id, format) DO NOTHING",
                    (
                        request_id,
                        request.score_id,
                        request.format,
                        request.score_url,
                        request.title,
                        str(RequestState.WAITING),
                        position,
                        now.isoformat(),
                    ),
                )
                if cursor.rowcount:
                    added.append(request_id)
        return self._by_ids(added)

    def offer(self, count: int, *, now: datetime) -> tuple[StoredRequest, ...]:
        """Move up to *count* waiting requests onto today's list and return them.

        Oldest first, so a backlog drains in the order it was built rather than
        by whatever the database felt like. Asking for nothing, or asking when
        nothing waits, returns nothing rather than complaining: an empty list
        is a perfectly good answer to *"what should I do today?"*.
        """
        if count <= 0:
            return ()
        waiting = self.by_state(RequestState.WAITING)[:count]
        chosen = [request.request_id for request in waiting]
        if not chosen:
            return ()
        placeholders = ", ".join("?" for _ in chosen)
        with closing(self._database.connect()) as connection, connection:
            connection.execute(
                f"UPDATE musescore_requests SET state = ?, offered_at = ? "  # noqa: S608
                f"WHERE request_id IN ({placeholders})",
                (str(RequestState.OFFERED), now.isoformat(), *chosen),
            )
        return self._by_ids(chosen)

    def withdraw_offers(self, *, before_day: str) -> int:
        """Return offers older than *before_day* to the backlog, and say how many.

        Nothing was lost when a list was not worked through; it was simply not
        clicked. Leaving those rows offered would let a stale list hide behind
        today's, and counting them as spent would charge somebody for files
        they never got.
        """
        with closing(self._database.connect()) as connection, connection:
            cursor = connection.execute(
                "UPDATE musescore_requests SET state = ?, offered_at = NULL "
                "WHERE state = ? AND (offered_at IS NULL OR offered_at < ?)",
                (str(RequestState.WAITING), str(RequestState.OFFERED), before_day),
            )
            return int(cursor.rowcount)

    def mark_stored(
        self, request_id: str, *, now: datetime, day: str, entry_key: str = ""
    ) -> StoredRequest | None:
        """Record that the file arrived, against the allowance of *day*.

        *day* is passed in rather than derived from *now* because where a day
        begins is a policy about somebody else's reset time, and a database is
        the wrong place to hold an opinion about that.
        """
        return self._settle(
            request_id, RequestState.STORED, now=now, day=day, entry_key=entry_key, note=""
        )

    def drop(self, request_id: str, *, now: datetime, note: str = "") -> StoredRequest | None:
        """Take a request off the list, with a note saying why."""
        return self._settle(
            request_id, RequestState.DROPPED, now=now, day="", entry_key="", note=note
        )

    def spent_on(self, day: str) -> int:
        """Return how many files were stored against the allowance of *day*."""
        with closing(self._database.connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS spent FROM musescore_requests "
                "WHERE state = ? AND settled_day = ?",
                (str(RequestState.STORED), day),
            ).fetchone()
        return 0 if row is None else int(row["spent"])

    def counts(self) -> Mapping[RequestState, int]:
        """Return how many requests sit in each state, including the empty ones."""
        with closing(self._database.connect()) as connection:
            rows = connection.execute(
                "SELECT state, COUNT(*) AS total FROM musescore_requests GROUP BY state"
            ).fetchall()
        found = {str(row["state"]): int(row["total"]) for row in rows}
        return {state: found.get(str(state), 0) for state in RequestState}

    def by_state(self, state: RequestState) -> tuple[StoredRequest, ...]:
        """Return every request in *state*, oldest first."""
        with closing(self._database.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM musescore_requests WHERE state = ? ORDER BY position, request_id",
                (str(state),),
            ).fetchall()
        return tuple(_to_stored_request(row) for row in rows)

    def requests(self) -> tuple[StoredRequest, ...]:
        """Return every request, oldest first."""
        with closing(self._database.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM musescore_requests ORDER BY position, request_id"
            ).fetchall()
        return tuple(_to_stored_request(row) for row in rows)

    def request(self, request_id: str) -> StoredRequest | None:
        """Return the request called *request_id*, if it is known."""
        found = self._by_ids([request_id])
        return found[0] if found else None

    def _settle(
        self,
        request_id: str,
        state: RequestState,
        *,
        now: datetime,
        day: str,
        entry_key: str,
        note: str,
    ) -> StoredRequest | None:
        """Move one request into a final state, if it is still open.

        A request that already settled is left alone and reported as ``None``.
        Two arrivals for one line — a folder scanned twice, a page reloaded —
        must not spend two days of an allowance on one file.
        """
        with closing(self._database.connect()) as connection, connection:
            cursor = connection.execute(
                "UPDATE musescore_requests SET state = ?, settled_at = ?, settled_day = ?, "
                "entry_key = ?, note = ? WHERE request_id = ? AND state IN (?, ?)",
                (
                    str(state),
                    now.isoformat(),
                    day,
                    entry_key,
                    note,
                    request_id,
                    str(RequestState.WAITING),
                    str(RequestState.OFFERED),
                ),
            )
            if not cursor.rowcount:
                return None
        return self.request(request_id)

    def _by_ids(self, request_ids: list[str]) -> tuple[StoredRequest, ...]:
        """Return the named requests, in the order they were named."""
        if not request_ids:
            return ()
        placeholders = ", ".join("?" for _ in request_ids)
        with closing(self._database.connect()) as connection:
            rows = connection.execute(
                f"SELECT * FROM musescore_requests WHERE request_id IN ({placeholders})",  # noqa: S608
                tuple(request_ids),
            ).fetchall()
        found = {str(row["request_id"]): _to_stored_request(row) for row in rows}
        return tuple(found[request_id] for request_id in request_ids if request_id in found)


def _to_stored_request(row: sqlite3.Row) -> StoredRequest:
    """Convert a database row into a :class:`StoredRequest`."""
    return StoredRequest(
        request_id=str(row["request_id"]),
        score_id=str(row["score_id"]),
        format=str(row["format"]),
        score_url=str(row["score_url"]),
        title=str(row["title"]),
        state=RequestState(str(row["state"])),
        position=int(row["position"]),
        added_at=datetime.fromisoformat(str(row["added_at"])),
        offered_at=_timestamp(row["offered_at"]),
        settled_at=_timestamp(row["settled_at"]),
        settled_day=str(row["settled_day"]),
        entry_key=str(row["entry_key"]),
        note=str(row["note"]),
    )


def _timestamp(value: object) -> datetime | None:
    """Return *value* as a datetime, or ``None`` when the column was empty."""
    if value is None or value == "":
        return None
    return datetime.fromisoformat(str(value))
