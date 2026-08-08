"""Tests for the SQLite persistence adapter."""

from contextlib import closing
from pathlib import Path

import pytest

from maxicrawler.database import SQLiteDatabase
from maxicrawler.database.sqlite import require_identifier


def test_database_stores_and_updates_metadata(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "maxicrawler.db")
    database.initialize()

    database.set_metadata("schema_version", "1")
    database.set_metadata("schema_version", "2")

    assert database.get_metadata("schema_version") == "2"
    assert database.get_metadata("unknown") is None


# --- additive migrations ------------------------------------------------------


def test_columns_of_a_missing_table_are_empty(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "empty.db")

    assert database.table_columns("nothing") == frozenset()


def test_columns_of_an_existing_table_are_reported(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "app.db")
    with closing(database.connect()) as connection, connection:
        connection.execute("CREATE TABLE things (a INTEGER, b TEXT)")

    assert database.table_columns("things") == frozenset({"a", "b"})


def test_a_missing_column_is_appended(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "app.db")
    with closing(database.connect()) as connection, connection:
        connection.execute("CREATE TABLE things (a INTEGER)")

    added = database.add_missing_columns("things", {"b": "INTEGER NOT NULL DEFAULT 0"})

    assert added == ("b",)
    assert database.table_columns("things") == frozenset({"a", "b"})


def test_appending_is_idempotent(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "app.db")
    with closing(database.connect()) as connection, connection:
        connection.execute("CREATE TABLE things (a INTEGER)")
    columns = {"b": "INTEGER NOT NULL DEFAULT 0"}

    assert database.add_missing_columns("things", columns) == ("b",)
    assert database.add_missing_columns("things", columns) == ()


def test_appending_keeps_the_rows_that_are_already_there(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "app.db")
    with closing(database.connect()) as connection, connection:
        connection.execute("CREATE TABLE things (a INTEGER)")
        connection.execute("INSERT INTO things(a) VALUES(7)")

    database.add_missing_columns("things", {"b": "INTEGER NOT NULL DEFAULT 3"})

    with closing(database.connect()) as connection:
        row = connection.execute("SELECT a, b FROM things").fetchone()
    assert (row["a"], row["b"]) == (7, 3)


def test_several_missing_columns_are_appended_in_order(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "app.db")
    with closing(database.connect()) as connection, connection:
        connection.execute("CREATE TABLE things (a INTEGER)")

    added = database.add_missing_columns(
        "things", {"b": "INTEGER DEFAULT 0", "c": "TEXT DEFAULT ''"}
    )

    assert added == ("b", "c")


def test_a_table_that_does_not_exist_is_left_to_the_schema(tmp_path: Path) -> None:
    """Creating it is CREATE TABLE's job, and it will already have the column."""
    database = SQLiteDatabase(tmp_path / "app.db")

    assert database.add_missing_columns("absent", {"b": "INTEGER DEFAULT 0"}) == ()
    assert database.table_columns("absent") == frozenset()


@pytest.mark.parametrize(
    "name", ["things; DROP TABLE users", "no-hyphens", "1leading", "", "with space", "quo'te"]
)
def test_an_unusable_identifier_is_refused(name: str) -> None:
    with pytest.raises(ValueError, match="not a usable SQL identifier"):
        require_identifier(name)


@pytest.mark.parametrize("name", ["things", "crawl_sessions", "_private", "T1"])
def test_a_plain_identifier_is_accepted(name: str) -> None:
    assert require_identifier(name) == name


def test_a_hostile_table_name_never_reaches_a_statement(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "app.db")

    with pytest.raises(ValueError, match="not a usable SQL identifier"):
        database.add_missing_columns("things; DROP TABLE things", {"b": "INTEGER DEFAULT 0"})


def test_a_hostile_column_name_never_reaches_a_statement(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "app.db")
    with closing(database.connect()) as connection, connection:
        connection.execute("CREATE TABLE things (a INTEGER)")

    with pytest.raises(ValueError, match="not a usable SQL identifier"):
        database.add_missing_columns("things", {"b INTEGER, c": "INTEGER DEFAULT 0"})
