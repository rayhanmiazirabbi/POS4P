from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.models import Base

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _alembic(*command: str, database: Path) -> None:
    """Run Alembic against ``database`` the way a deployment would."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *command],
        cwd=BACKEND_ROOT,
        env={**os.environ, "PHARMACY_DATABASE_URL": f"sqlite+aiosqlite:///{database}"},
        capture_output=True,
        text=True,
        check=False,  # the assertion below reports Alembic's stderr, which is more useful
    )
    assert result.returncode == 0, f"alembic {' '.join(command)} failed:\n{result.stderr}"


def _flatten(diff: Any) -> list[tuple]:
    """``compare_metadata`` yields bare tuples for column ops and lists for table ops."""
    return [diff] if isinstance(diff, tuple) else list(diff)


def _is_sqlite_uuid_artifact(op: tuple) -> bool:
    """SQLite reflects ``Uuid`` columns as ``NUMERIC``; that is a dialect quirk, not drift.

    PostgreSQL, which is the deployment target, reflects them as ``UUID`` and reports no
    difference. Filtering this keeps the check meaningful on the SQLite test database.
    """
    return op[0] == "modify_type" and "NUMERIC" in repr(op[-2]) and "Uuid" in repr(op[-1])


@pytest.fixture(scope="module")
def migrated_database(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A database built by running every Alembic migration, not ``create_all``."""
    database = tmp_path_factory.mktemp("migrations") / "migrated.db"
    _alembic("upgrade", "head", database=database)
    return str(database)


def test_migrations_create_every_declared_table(migrated_database: str) -> None:
    """Every model has a migration.

    The test fixtures build their schema with ``Base.metadata.create_all``, so a table
    with no migration still passes every other test while being undeployable.
    """
    engine = create_engine(f"sqlite:///{migrated_database}")
    try:
        migrated = {
            name for name in inspect(engine).get_table_names() if name != "alembic_version"
        }
    finally:
        engine.dispose()

    declared = set(Base.metadata.tables)
    assert declared - migrated == set(), "tables declared in models but never migrated"
    assert migrated - declared == set(), "tables migrated but no longer declared in models"


def test_migrations_create_every_declared_column(migrated_database: str) -> None:
    engine = create_engine(f"sqlite:///{migrated_database}")
    try:
        inspector = inspect(engine)
        mismatched: dict[str, dict[str, list[str]]] = {}
        for name, table in sorted(Base.metadata.tables.items()):
            declared = {column.name for column in table.columns}
            migrated = {column["name"] for column in inspector.get_columns(name)}
            if declared != migrated:
                mismatched[name] = {
                    "missing": sorted(declared - migrated),
                    "unexpected": sorted(migrated - declared),
                }
    finally:
        engine.dispose()

    assert mismatched == {}


def test_migrated_schema_matches_models(migrated_database: str) -> None:
    """Alembic itself sees no difference between head and the model metadata."""
    engine = create_engine(f"sqlite:///{migrated_database}")
    try:
        with engine.connect() as connection:
            diffs = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    finally:
        engine.dispose()

    drift = [
        op
        for diff in diffs
        for op in _flatten(diff)
        if not _is_sqlite_uuid_artifact(op)
    ]
    assert drift == [], f"schema drift between migrations and models: {drift}"


def test_downgrade_reverses_every_migration(tmp_path: Path) -> None:
    """A full down-migration leaves no application tables behind."""
    database = tmp_path / "cycle.db"
    _alembic("upgrade", "head", database=database)
    _alembic("downgrade", "base", database=database)

    engine = create_engine(f"sqlite:///{database}")
    try:
        remaining = {
            name for name in inspect(engine).get_table_names() if name != "alembic_version"
        }
    finally:
        engine.dispose()
    assert remaining == set()
