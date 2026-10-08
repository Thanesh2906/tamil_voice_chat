"""Real PostgreSQL fixtures; every case owns a newly migrated private schema."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class PostgresDatabase:
    engine: Engine
    schema: str
    database_url: str = field(repr=False)

    def migrate(self) -> None:
        # A separate process exercises the same Alembic entrypoint as deployment
        # and avoids the application's process-wide cached Settings instance.
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env={**os.environ, "DATABASE_URL": self.database_url},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)


@pytest.fixture
def postgres_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[PostgresDatabase]:
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("set RUN_INTEGRATION=1 with disposable PostgreSQL")
    url = make_url(os.environ["DATABASE_URL"])
    if url.get_backend_name() != "postgresql":
        pytest.fail("RUN_INTEGRATION=1 requires a real PostgreSQL DATABASE_URL, not SQLite")

    # No public-schema fallback: missing migrations must fail rather than finding
    # similarly named tables in another test or a developer's existing database.
    schema = f"jarvis_integration_{uuid.uuid4().hex}"
    scoped_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
    admin_engine = create_engine(url, pool_pre_ping=True)
    engine = create_engine(scoped_url, pool_pre_ping=True)
    created = False
    try:
        with admin_engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        created = True
        assert engine.dialect.name == "postgresql"
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT current_schema()")) == schema
        assert inspect(engine).get_table_names(schema=schema) == []

        database = PostgresDatabase(
            engine=engine,
            schema=schema,
            database_url=scoped_url.render_as_string(hide_password=False),
        )
        database.migrate()

        from packages import db
        from services import api

        # These are real SQLAlchemy sessions against the migrated PostgreSQL
        # schema. Do not replace API handlers, session behavior, or the lifespan.
        with monkeypatch.context() as patch:
            patch.setattr(db, "_engine", engine)
            patch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
            patch.setattr(api, "settings", api.settings.model_copy(update={
                "jarvis_env": "dev",
                "bootstrap_admin_email": None,
                "bootstrap_admin_password": None,
            }))
            yield database
    finally:
        engine.dispose()
        if created:
            # Only the UUID schema created above belongs to this fixture.
            # Never drop shared tables, another schema, or the supplied database.
            with admin_engine.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
        admin_engine.dispose()
