"""Exercise normal API onboarding against migrated PostgreSQL with real FKs."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from packages.auth import decode_token, verify_password
from packages.db import (
    AuditEvent,
    Conversation,
    Message,
    Project,
    ProjectMember,
    RefreshToken,
    Tenant,
    TenantMember,
    User,
    token_hash,
)
from services import api

pytestmark = pytest.mark.integration

if os.getenv("RUN_INTEGRATION") != "1":
    pytest.skip("set RUN_INTEGRATION=1 with disposable PostgreSQL", allow_module_level=True)

PASSWORD = "postgres-integration-password"
ONBOARDING_MODELS = (User, Tenant, TenantMember, Project, ProjectMember, AuditEvent, RefreshToken)


def registration(email: str = "new.person@example.com") -> dict[str, str]:
    return {
        "email": email,
        "password": PASSWORD,
        "display_name": "PostgreSQL Person",
        "tenant_name": "PostgreSQL Workspace",
        "preferred_language": "ta",
    }


def headers(tokens: dict) -> dict[str, str]:
    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] > 0
    assert tokens["access_token"]
    assert tokens["refresh_token"]
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def counts(database) -> dict[str, int]:
    with database.session() as session:
        return {
            model.__tablename__: session.scalar(select(func.count()).select_from(model))
            for model in ONBOARDING_MODELS
        }


def test_alembic_chain_and_repeat_preserve_real_foreign_keys(postgres_db) -> None:
    tables = set(inspect(postgres_db.engine).get_table_names(schema=postgres_db.schema))
    assert {
        "alembic_version", "users", "tenants", "tenant_members", "projects", "project_members",
        "refresh_tokens", "conversations", "messages", "documents", "document_versions",
        "ingestion_jobs", "audit_events", "agent_runs", "run_events", "tool_invocations",
    } <= tables
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    with postgres_db.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == script.get_current_head()

    postgres_db.migrate()
    assert set(inspect(postgres_db.engine).get_table_names(schema=postgres_db.schema)) == tables
    assert counts(postgres_db) == {model.__tablename__: 0 for model in ONBOARDING_MODELS}

    # Inspector metadata alone is insufficient: an orphan must actually fail.
    with pytest.raises(IntegrityError) as error, postgres_db.engine.begin() as connection:
        connection.execute(Project.__table__.insert().values(
            id="orphan-project", tenant_id="missing-tenant", name="Must not persist",
        ))
    assert error.value.orig.sqlstate == "23503"  # PostgreSQL foreign_key_violation
    assert counts(postgres_db)["projects"] == 0


def test_lifespan_bootstrap_is_normalized_and_idempotent(postgres_db, monkeypatch) -> None:
    monkeypatch.setattr(api.settings, "bootstrap_admin_email", "Admin.MixedCase@EXAMPLE.COM")
    monkeypatch.setattr(api.settings, "bootstrap_admin_password", PASSWORD)

    # TestClient context enters the real lifespan; no test manually seeds users,
    # tenants, projects or memberships before either startup.
    with TestClient(api.app) as client:
        assert client.get("/readyz").status_code == 200
        first_counts = counts(postgres_db)
        assert first_counts == {
            "users": 1, "tenants": 1, "tenant_members": 1, "projects": 1,
            "project_members": 1, "audit_events": 0, "refresh_tokens": 0,
        }
        with postgres_db.session() as session:
            user = session.scalar(select(User))
            project = session.scalar(select(Project))
            user_id, project_id = user.id, project.id
            assert user.email == "admin.mixedcase@example.com"
            assert verify_password(PASSWORD, user.password_hash)
            assert "admin" in user.scopes
            assert session.get(TenantMember, (project.tenant_id, user.id)).role == "owner"
            assert session.get(ProjectMember, (project.id, user.id)).role == "owner"

    with TestClient(api.app) as client:
        assert counts(postgres_db) == first_counts
        login = client.post("/auth/login", json={
            "email": "ADMIN.MIXEDCASE@example.com", "password": PASSWORD,
        })
        assert login.status_code == 200, login.text
        auth = headers(login.json())
        me = client.get("/me", headers=auth)
        assert me.status_code == 200, me.text
        assert me.json()["id"] == user_id
        projects = client.get("/projects", headers=auth)
        assert projects.status_code == 200, projects.text
        assert projects.json() == [{"id": project_id, "name": "Personal", "role": "owner"}]


def test_registration_login_projects_audit_chat_and_refresh_round_trip(postgres_db, monkeypatch) -> None:
    with TestClient(api.app) as client:
        assert all(count == 0 for count in counts(postgres_db).values())
        response = client.post("/auth/register", json=registration("New.Person@EXAMPLE.COM"))
        assert response.status_code == 201, response.text
        tokens = response.json()
        auth = headers(tokens)
        me = client.get("/me", headers=auth)
        assert me.status_code == 200, me.text
        identity = me.json()
        assert identity["email"] == "new.person@example.com"
        assert identity["display_name"] == "PostgreSQL Person"
        assert identity["preferred_language"] == "ta"
        assert set(identity["scopes"]) == {"chat", "rag"}
        assert identity["created_at"]
        user_id = identity["id"]
        projects = client.get("/projects", headers=auth)
        assert projects.status_code == 200, projects.text
        assert len(projects.json()) == 1
        personal_project = projects.json()[0]
        assert personal_project["name"] == "Personal"
        assert personal_project["role"] == "owner"
        assert counts(postgres_db) == {model.__tablename__: 1 for model in ONBOARDING_MODELS}

        original_hash = token_hash(decode_token(tokens["refresh_token"], expected_type="refresh")["jti"])
        with postgres_db.session() as session:
            user = session.get(User, user_id)
            assert verify_password(PASSWORD, user.password_hash)
            assert user.password_hash != PASSWORD
            tenant = session.scalar(select(Tenant))
            assert tenant.name == "PostgreSQL Workspace"
            project = session.get(Project, personal_project["id"])
            assert project.tenant_id == tenant.id
            assert session.get(TenantMember, (tenant.id, user_id)).role == "owner"
            assert session.get(ProjectMember, (project.id, user_id)).role == "owner"
            event = session.scalar(select(AuditEvent))
            assert (event.user_id, event.action) == (user_id, "auth.register")
            stored = session.get(RefreshToken, original_hash)
            assert stored.user_id == user_id
            assert stored.revoked_at is None

        login = client.post("/auth/login", json={
            "email": "NEW.PERSON@example.com", "password": PASSWORD,
        })
        assert login.status_code == 200, login.text
        assert client.get("/me", headers=headers(login.json())).json()["id"] == user_id
        created = client.post("/projects", headers=auth, json={"name": "Second project"})
        assert created.status_code == 201, created.text
        assert created.json()["role"] == "owner"
        project_id = created.json()["id"]
        visible = client.get("/projects", headers=auth)
        assert visible.status_code == 200, visible.text
        assert {item["id"] for item in visible.json()} == {project_id, personal_project["id"]}

        # Only the model response is deterministic. Routing, HTTP handlers,
        # conversation creation, and message commits use the real application.
        model_histories = []

        async def fake_llm(messages, **kwargs):
            model_histories.append(messages)
            yield "PostgreSQL "
            yield "response"

        monkeypatch.setattr(api.app.state.adapters, "llm", fake_llm)
        for message in ("Hello", "Hello again"):
            chat = client.post("/chat", headers=auth, json={
                "session_id": "postgres-onboarding-session",
                "message": message,
                "project_id": project_id,
                "context": {"mode": "personal"},
                "provider": "ollama",
                "model": "postgres-integration-model",
            })
            assert chat.status_code == 200, chat.text
            assert chat.json()["answer"] == "PostgreSQL response"
            assert chat.json()["session_id"] == "postgres-onboarding-session"
        assert [item for item in model_histories[1] if item["role"] != "system"] == [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "PostgreSQL response"},
            {"role": "user", "content": "Hello again"},
        ]

        refreshed = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert refreshed.status_code == 200, refreshed.text
        replacement = refreshed.json()
        assert replacement["refresh_token"] != tokens["refresh_token"]
        assert client.get("/me", headers=headers(replacement)).json()["id"] == user_id
        replay = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert replay.status_code == 401, replay.text
        replacement_hash = token_hash(decode_token(replacement["refresh_token"], expected_type="refresh")["jti"])
        with postgres_db.session() as session:
            project = session.get(Project, project_id)
            assert project.tenant_id == tenant.id
            assert session.get(ProjectMember, (project_id, user_id)).role == "owner"
            events = session.scalars(select(AuditEvent).where(AuditEvent.user_id == user_id)).all()
            assert {event.action for event in events} == {"auth.register", "auth.login", "project.created"}
            assert next(event for event in events if event.action == "project.created").target == project_id
            original = session.get(RefreshToken, original_hash)
            assert original.revoked_at is not None
            assert original.replaced_by_hash == replacement_hash
            assert session.get(RefreshToken, replacement_hash).revoked_at is None
            conversations = session.scalars(select(Conversation)).all()
            assert len(conversations) == 1
            conversation = conversations[0]
            assert (conversation.user_id, conversation.project_id) == (user_id, project_id)
            messages = session.scalars(select(Message).order_by(Message.created_at, Message.id)).all()
            assert {item.conversation_id for item in messages} == {conversation.id}
            assert [(item.role, item.content) for item in messages] == [
                ("user", "Hello"), ("assistant", "PostgreSQL response"),
                ("user", "Hello again"), ("assistant", "PostgreSQL response"),
            ]


def test_duplicate_email_preserves_existing_account_and_rows(postgres_db) -> None:
    with TestClient(api.app) as client:
        first = client.post("/auth/register", json=registration())
        assert first.status_code == 201, first.text
        before = counts(postgres_db)
        duplicate = client.post("/auth/register", json={
            **registration("NEW.PERSON@EXAMPLE.COM"),
            "display_name": "Must not replace original",
            "tenant_name": "Must not leave a tenant behind",
        })
        assert duplicate.status_code == 409, duplicate.text
        assert duplicate.json() == {"detail": "email is already registered"}
        assert counts(postgres_db) == before
        me = client.get("/me", headers=headers(first.json()))
        assert me.status_code == 200, me.text
        assert me.json()["display_name"] == "PostgreSQL Person"
        login = client.post("/auth/login", json={"email": "new.person@example.com", "password": PASSWORD})
        assert login.status_code == 200, login.text


def test_non_email_fk_failure_is_not_a_conflict_and_rolls_back_every_row(postgres_db) -> None:
    # Force a genuine PostgreSQL FK failure at refresh-token persistence, after
    # onboarding parents have been flushed. This catches both blanket 409 error
    # mapping and a premature onboarding commit before token issuance.
    with postgres_db.engine.begin() as connection:
        connection.execute(text("CREATE TABLE integration_allowed_refresh_users (id VARCHAR(64) PRIMARY KEY)"))
        connection.execute(text(
            "ALTER TABLE refresh_tokens ADD CONSTRAINT integration_refresh_user_fk "
            "FOREIGN KEY (user_id) REFERENCES integration_allowed_refresh_users (id)"
        ))

    with TestClient(api.app, raise_server_exceptions=False) as client:
        failed = client.post("/auth/register", json=registration())
        assert failed.status_code == 500, failed.text
        assert failed.json() == {"detail": "registration failed"}
        assert counts(postgres_db) == {model.__tablename__: 0 for model in ONBOARDING_MODELS}

        with postgres_db.engine.begin() as connection:
            connection.execute(text("ALTER TABLE refresh_tokens DROP CONSTRAINT integration_refresh_user_fk"))
            connection.execute(text("DROP TABLE integration_allowed_refresh_users"))
        retry = client.post("/auth/register", json=registration())
        assert retry.status_code == 201, retry.text
        assert client.get("/me", headers=headers(retry.json())).status_code == 200
        assert counts(postgres_db) == {model.__tablename__: 1 for model in ONBOARDING_MODELS}
