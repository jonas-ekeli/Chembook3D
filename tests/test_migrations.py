"""The schema of a new investigation (built from the models) and of one brought up to date by
the migrations (an investigation saved by an earlier version) must be the same. An upgrade from
every revision keeps the rows it finds, a failed one changes nothing (NFR-DATA-03), and every
revision can be undone."""

import shutil
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


REVISIONS = [script.revision for script in reversed(list(inv._scripts().walk_revisions()))]


@pytest.fixture(scope="session")
def at_revision(tmp_path_factory) -> dict:
    """An empty database at every revision, from one replay of the migrations."""
    folder = tmp_path_factory.mktemp("revisions")
    path = folder / "replay.sqlite"
    engine = create_engine(f"sqlite:///{path}")
    files = {}
    for revision in REVISIONS:
        with engine.begin() as connection:
            inv.command.upgrade(inv._alembic_config(connection), revision)
        engine.dispose()  # Windows cannot copy a file SQLite still has open
        files[revision] = shutil.copy(path, folder / f"{revision}.sqlite")
    return files


@pytest.fixture(scope="session")
def new_database(tmp_path_factory):
    return created(tmp_path_factory.mktemp("created"))


# What a generic row cannot guess: the CHECK constraints, and JSON a migration reads.
SEED_VALUES = {
    "transitions": {"source_group_id": None, "target_group_id": None},  # one_source, one_target
    "transition_species": {"direction": "joins"},  # direction_known
    "selectivity_members": {"group_id": None},  # one_member
    "investigation_info": {"view_state": '{"reference_id": "energy_profiles.id"}'},  # 0031
}


def _seed_value(table: str, column: str, declared: str):
    kind = declared.split("(")[0]
    values = {
        "VARCHAR": f"{table}.{column}",
        "TEXT": f"{table}.{column}",
        "INTEGER": 1,
        "BOOLEAN": 0,
        "DOUBLE": 1.5,
        "DATETIME": "2026-01-02 03:04:05.000000",
        "JSON": "[]",
        "BLOB": b"\x89PNG",
    }
    if kind not in values:
        raise AssertionError(f"no seed value for {table}.{column} ({declared}); add one")
    return values[kind]


def tables(db) -> list[str]:
    return [
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            " AND name != 'alembic_version' ORDER BY name"
        )
    ]


def counts(path) -> dict[str, int]:
    db = sqlite3.connect(path)
    try:
        return {
            table: db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in tables(db)
        }
    finally:
        db.close()


def broken_references(path) -> list:
    db = sqlite3.connect(path)
    try:
        return db.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        db.close()


def seed(path) -> dict[str, int]:
    """A row in every empty table, with every column filled and every foreign key pointing at
    a row of its parent table, so the migrations have data to carry in each table and column.
    Foreign keys are off on a plain sqlite3 connection, so the order of inserts does not
    matter; the check at the end makes sure every reference has its row."""
    db = sqlite3.connect(path)
    try:
        rows, new = {}, []
        for table in tables(db):
            cursor = db.execute(f'SELECT * FROM "{table}" LIMIT 1')
            existing = cursor.fetchone()
            if existing is not None:
                rows[table] = dict(zip([d[0] for d in cursor.description], existing, strict=True))
                continue
            rows[table] = {
                name: _seed_value(table, name, declared)
                for _cid, name, declared, *_ in db.execute(f"PRAGMA table_info('{table}')")
            }
            new.append(table)
        for table in new:
            for _id, _seq, parent, column, key, *_ in db.execute(
                f"PRAGMA foreign_key_list('{table}')"
            ):
                rows[table][column] = rows[parent][key]
            rows[table].update(
                (column, value)
                for column, value in SEED_VALUES.get(table, {}).items()
                if column in rows[table]
            )
            columns = ", ".join(f'"{column}"' for column in rows[table])
            marks = ", ".join("?" for _ in rows[table])
            db.execute(
                f'INSERT INTO "{table}" ({columns}) VALUES ({marks})', list(rows[table].values())
            )
        db.commit()
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        db.close()
    return counts(path)


@pytest.mark.parametrize("revision", REVISIONS[:-1])
def test_an_investigation_at_every_revision_upgrades_with_its_rows(
    revision, at_revision, new_database, tmp_path
):
    path = shutil.copy(at_revision[revision], tmp_path / inv.DB_NAME)
    seeded = seed(path)
    engine = inv._make_engine(path)
    try:
        backup = inv._migrate(engine, path)
    finally:
        engine.dispose()
    assert backup is not None and (tmp_path / inv.BACKUP_DIR).is_dir()
    assert broken_references(path) == []
    # No revision deletes or merges rows, so every seeded row is still there. A revision that
    # does so on purpose must be named here with the rows it may remove.
    after = counts(path)
    assert {table: after[table] for table in seeded if table in after} == seeded
    assert schema(path) == schema(new_database)


def _old_investigation(tmp_path, at_revision, revision: str) -> tuple:
    folder = tmp_path / "old"
    folder.mkdir()
    shutil.copy(at_revision[revision], folder / inv.DB_NAME)
    seed(folder / inv.DB_NAME)
    return folder, (folder / inv.DB_NAME).read_bytes()


def _assert_unchanged(folder, before: bytes) -> None:
    # open_investigation disposed of its engine on the way out, so the file can be read here.
    assert (folder / inv.DB_NAME).read_bytes() == before
    assert not (folder / f"{inv.DB_NAME}-journal").exists()
    backups = list((folder / inv.BACKUP_DIR).iterdir())
    assert len(backups) == 1 and backups[0].read_bytes() == before


def test_an_upgrade_that_fails_halfway_leaves_the_database_and_its_backup_as_they_were(
    tmp_path, at_revision, monkeypatch
):
    # NFR-DATA-03: the revisions before the failing one created a table and rebuilt others,
    # and all of it is undone, not only the failing step.
    start, reached = "0028", "0030"
    folder, before = _old_investigation(tmp_path, at_revision, start)
    upgrade = inv.command.upgrade

    def fail_halfway(config, revision):
        upgrade(config, reached)
        connection = config.attributes["connection"]
        assert MigrationContext.configure(connection).get_current_revision() == reached
        raise RuntimeError("the last revision failed")

    monkeypatch.setattr(inv.command, "upgrade", fail_halfway)
    with pytest.raises(RuntimeError, match="last revision failed"):
        inv.open_investigation(folder)
    _assert_unchanged(folder, before)


def test_broken_references_after_an_upgrade_undo_the_whole_upgrade(tmp_path, at_revision):
    folder, _ = _old_investigation(tmp_path, at_revision, REVISIONS[-2])
    db = sqlite3.connect(folder / inv.DB_NAME)
    db.execute("UPDATE calculations SET node_id = 'deleted node'")  # foreign keys are off here
    db.commit()
    db.close()
    before = (folder / inv.DB_NAME).read_bytes()
    with pytest.raises(inv.InvestigationError, match="1 broken references"):
        inv.open_investigation(folder)
    _assert_unchanged(folder, before)


@pytest.mark.parametrize("start", ["created", "migrated"])
def test_every_revision_can_be_undone_and_redone(start, tmp_path, at_revision, new_database):
    # From a database the models created (its foreign keys partly unnamed) and from one the
    # migrations built, one revision at a time: each step back gives the schema that step's
    # upgrade started from and keeps the rows of the tables it keeps. A downgrade that drops a
    # table drops its rows; that loss comes with going back past the revision that added it.
    source = new_database if start == "created" else at_revision[REVISIONS[-1]]
    path = shutil.copy(source, tmp_path / inv.DB_NAME)
    seed(path)
    engine = create_engine(f"sqlite:///{path}")  # foreign keys off, as while upgrading
    for below in [*reversed(REVISIONS[:-1]), None]:
        before = counts(path)
        with engine.begin() as connection:
            inv.command.downgrade(inv._alembic_config(connection), "-1")
        engine.dispose()
        assert broken_references(path) == [], below
        after = counts(path)
        assert after == {table: before[table] for table in after}, below
        if below is not None:
            assert schema(path) == schema(at_revision[below]), below
    assert counts(path) == {}

    with engine.begin() as connection:
        inv.command.upgrade(inv._alembic_config(connection), "head")
    engine.dispose()
    assert schema(path) == schema(new_database)
