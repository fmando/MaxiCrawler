"""Tests for the SQLite persistence adapter."""

from pathlib import Path

from maxicrawler.database import SQLiteDatabase


def test_database_stores_and_updates_metadata(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "maxicrawler.db")
    database.initialize()

    database.set_metadata("schema_version", "1")
    database.set_metadata("schema_version", "2")

    assert database.get_metadata("schema_version") == "2"
    assert database.get_metadata("unknown") is None
