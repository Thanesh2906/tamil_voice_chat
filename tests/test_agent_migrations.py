"""Migration regression tests use isolated SQLite databases, never the app DB."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]


def migration_head():
    return ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()


def migrate(path: Path):
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{path}"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_fresh_migration_chain_and_repeat_are_safe(tmp_path):
    path = tmp_path / "fresh.db"
    migrate(path)
    migrate(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == (migration_head(),)
        columns = {row[1] for row in db.execute("PRAGMA table_info(agent_runs)")}
        assert {"agent_id", "execution_agent_id", "checkpoint_json", "claim_token",
                "lease_expires_at", "cancellation_requested_at", "revision"} <= columns


def test_identity_migration_preserves_old_runs(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        db.execute("INSERT INTO alembic_version VALUES ('0005')")
        db.execute("CREATE TABLE conversations (id VARCHAR(64) PRIMARY KEY)")
        db.execute("CREATE TABLE agent_runs (id VARCHAR(64) PRIMARY KEY, input_text TEXT, status VARCHAR(16))")
        db.execute("INSERT INTO agent_runs VALUES ('old-run', 'preserve my history', 'completed')")
    migrate(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT id, input_text, status, agent_id, execution_agent_id FROM agent_runs").fetchone() == (
            "old-run", "preserve my history", "completed", "manager", "manager",
        )


def test_migrations_accept_percent_in_database_url(tmp_path):
    path = tmp_path / "percent%name.db"
    migrate(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == (migration_head(),)


def test_continuation_migration_preserves_legacy_status_and_linked_records(tmp_path):
    path = tmp_path / "legacy-linked.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY);
            INSERT INTO alembic_version VALUES ('0006');
            CREATE TABLE agent_runs (id VARCHAR(64) PRIMARY KEY, status VARCHAR(16));
            INSERT INTO agent_runs VALUES ('waiting', 'awaiting_approval');
            CREATE TABLE run_events (id TEXT PRIMARY KEY, run_id TEXT REFERENCES agent_runs(id), data_json TEXT);
            INSERT INTO run_events VALUES ('event', 'waiting', '{"exact":"timeline"}');
            CREATE TABLE tool_invocations (id TEXT PRIMARY KEY, run_id TEXT REFERENCES agent_runs(id), status TEXT, args_json TEXT);
            INSERT INTO tool_invocations VALUES ('action', 'waiting', 'pending', '{"content":"preserve exactly"}');
        """)
    migrate(path)
    migrate(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT id, status, checkpoint_json, revision FROM agent_runs").fetchone() == (
            "waiting", "awaiting_approval", None, 0,
        )
        columns = {row[1]: row[2] for row in db.execute("PRAGMA table_info(agent_runs)")}
        assert columns["status"] == "VARCHAR(32)"
        assert db.execute("SELECT * FROM run_events").fetchone() == ('event', 'waiting', '{"exact":"timeline"}')
        assert db.execute("SELECT * FROM tool_invocations").fetchone() == (
            'action', 'waiting', 'pending', '{"content":"preserve exactly"}',
        )
