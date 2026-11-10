"""The schema of a new investigation (built from the models) and of one brought up to date by
the migrations (an investigation saved by an earlier version) must be the same."""

import sqlite3

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from chembook3d import investigation as inv
from chembook3d.models import Base


def replayed(path):
    """A database built the way an investigation from the first version reaches today's
    schema: every migration in turn, from an empty file."""
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        inv.command.upgrade(inv._alembic_config(connection), "head")
    engine.dispose()
    return path


def created(tmp_path):
    inv.create_investigation(tmp_path / "new", "New").close()
    return tmp_path / "new" / inv.DB_NAME


def schema(path) -> dict:
    """Every table's columns (type, NOT NULL, default, key), indexes and foreign keys, and the
    schema revision, as SQLite itself reports them."""
    db = sqlite3.connect(path)
    try:
        tables = [
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        out = {}
        for table in sorted(tables):
            columns = sorted(row[1:] for row in db.execute(f"PRAGMA table_info('{table}')"))
            indexes = sorted(
                (
                    unique,
                    tuple(col[2] for col in db.execute(f"PRAGMA index_info('{name}')")),
                )
                for _seq, name, unique, *_ in db.execute(f"PRAGMA index_list('{table}')")
            )
            keys = sorted(row[2:] for row in db.execute(f"PRAGMA foreign_key_list('{table}')"))
            out[table] = {"columns": columns, "indexes": indexes, "foreign keys": keys}
        out["other objects"] = sorted(
            db.execute("SELECT type, name FROM sqlite_master WHERE type IN ('trigger', 'view')")
        )
        out["revision"] = db.execute("SELECT version_num FROM alembic_version").fetchall()
        return out
    finally:
        db.close()


def test_a_new_investigation_has_the_schema_the_migrations_build(tmp_path):
    new, migrated = schema(created(tmp_path)), schema(replayed(tmp_path / "old.sqlite"))
    assert new.keys() == migrated.keys()
    for table in new:
        assert new[table] == migrated[table], table


def test_the_migrations_match_the_models(tmp_path):
    engine = create_engine(f"sqlite:///{replayed(tmp_path / 'db.sqlite')}")
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": True}
        )
        assert compare_metadata(context, Base.metadata) == []
    engine.dispose()


def test_a_new_database_is_written_in_one_commit(tmp_path, monkeypatch):
    # A create that fails partway leaves no half-built schema behind.
    def fail(*_args):
        raise RuntimeError("stopped")

    monkeypatch.setattr(MigrationContext, "stamp", fail)
    engine = inv._make_engine(tmp_path / "db.sqlite")
    with pytest.raises(RuntimeError):
        inv._create_schema(engine, inv._scripts())
    engine.dispose()
    db = sqlite3.connect(tmp_path / "db.sqlite")
    assert db.execute("SELECT name FROM sqlite_master").fetchall() == []
    db.close()


def test_tables_without_a_schema_revision_are_left_alone(tmp_path):
    db = sqlite3.connect(tmp_path / "db.sqlite")
    db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY)")
    db.commit()
    db.close()
    engine = inv._make_engine(tmp_path / "db.sqlite")
    with pytest.raises(inv.InvestigationError, match="no schema revision"):
        inv._migrate(engine, tmp_path / "db.sqlite")
    engine.dispose()
