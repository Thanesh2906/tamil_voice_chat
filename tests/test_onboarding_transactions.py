"""Local regression coverage with actual SQLite foreign-key enforcement.

The PostgreSQL integration suite remains the production-database proof. These
isolated tests keep the FK/order and rollback checks in the default fast suite.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import services.api as api
from packages.db import (
    AuditEvent,
    Base,
    Conversation,
    Message,
    Project,
    ProjectMember,
    RefreshToken,
    Tenant,
    TenantMember,
    User,
)

PASSWORD = "correct-horse-battery-staple"
WORKSPACE_MODELS = (User, Tenant, TenantMember, Project, ProjectMember, AuditEvent, RefreshToken)


@pytest.fixture
def isolated_onboarding():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    def dependency():
        with sessions() as session:
            yield session

    previous_overrides = dict(api.app.dependency_overrides)
    api.app.dependency_overrides[api.db_session] = dependency
    # No lifespan: this fixture supplies a separate schema and tests bootstrap
    # explicitly rather than starting the global DB or unrelated adapters.
    client = TestClient(api.app, raise_server_exceptions=False)
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        yield client, sessions
    finally:
        client.close()
        api.app.dependency_overrides.clear()
        api.app.dependency_overrides.update(previous_overrides)
        engine.dispose()


def register(client, email="new@example.com"):
    return client.post("/auth/register", json={
        "email": email, "password": PASSWORD, "display_name": "New User",
        "tenant_name": "New Tenant",
    })


def row_counts(sessions):
    with sessions() as session:
        return {model.__tablename__: session.scalar(select(func.count()).select_from(model))
                for model in WORKSPACE_MODELS}


def test_registration_persists_complete_workspace_and_valid_token(isolated_onboarding):
    client, sessions = isolated_onboarding
    response = register(client)
    assert response.status_code == 201, response.text
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    user = client.get("/me", headers=headers).json()
    assert user["email"] == "new@example.com"
    assert user["scopes"] == ["chat", "rag"]
    projects = client.get("/projects", headers=headers)
    assert projects.status_code == 200
    assert len(projects.json()) == 1
    assert projects.json()[0]["name"] == "Personal"
    assert projects.json()[0]["role"] == "owner"
    assert set(row_counts(sessions).values()) == {1}
    with sessions() as session:
        tenant_member = session.scalar(select(TenantMember))
        project_member = session.scalar(select(ProjectMember))
        assert tenant_member.user_id == project_member.user_id == user["id"]
        assert tenant_member.role == project_member.role == "owner"
    snapshot = client.get("/api/v1/office/snapshot", headers=headers)
    assert snapshot.status_code == 200
    assert any(item["type"] == "auth.register" for item in snapshot.json()["events"])
    assert client.post("/auth/login", json={
        "email": "NEW@example.com", "password": PASSWORD,
    }).status_code == 200
    assert client.post("/auth/refresh", json={
        "refresh_token": response.json()["refresh_token"],
    }).status_code == 200


@pytest.mark.parametrize("email", ["new@example.com", "NEW@example.com"])
def test_duplicate_email_is_409_and_does_not_leave_rows(isolated_onboarding, email):
    client, sessions = isolated_onboarding
    assert register(client).status_code == 201
    before = row_counts(sessions)
    duplicate = register(client, email)
    assert duplicate.status_code == 409
    assert duplicate.json() == {"detail": "email is already registered"}
    assert row_counts(sessions) == before
    assert register(client, "another@example.com").status_code == 201


@pytest.mark.parametrize("failure_model,failure_field,failure_value", [
    (ProjectMember, "user_id", "missing-user"),
    (AuditEvent, "action", None),
    (RefreshToken, "user_id", "missing-user"),
])
def test_integrity_failures_are_sanitized_and_roll_back_every_row(
    isolated_onboarding, failure_model, failure_field, failure_value,
):
    client, sessions = isolated_onboarding

    def fail_insert(_mapper, _connection, target):
        setattr(target, failure_field, failure_value)

    event.listen(failure_model, "before_insert", fail_insert)
    try:
        response = register(client)
    finally:
        event.remove(failure_model, "before_insert", fail_insert)
    assert response.status_code == 500
    assert response.json() == {"detail": "registration failed"}
    assert set(row_counts(sessions).values()) == {0}
    assert register(client).status_code == 201


def test_other_unique_violation_is_not_reported_as_duplicate_email(
    isolated_onboarding, monkeypatch,
):
    client, sessions = isolated_onboarding
    assert register(client).status_code == 201
    before = row_counts(sessions)
    with sessions() as session:
        existing_id = session.scalar(select(User.id))
    original_new_id = api.new_id
    monkeypatch.setattr(api, "new_id", lambda prefix: (
        existing_id if prefix == "usr" else original_new_id(prefix)
    ))
    response = register(client, "different@example.com")
    assert response.status_code == 500
    assert response.json() == {"detail": "registration failed"}
    assert row_counts(sessions) == before


def test_bootstrap_is_atomic_case_insensitive_and_preserves_existing_data(
    isolated_onboarding, monkeypatch,
):
    client, sessions = isolated_onboarding
    assert register(client).status_code == 201
    before = row_counts(sessions)
    monkeypatch.setattr(api.settings, "bootstrap_admin_email", "BootStrap@example.com")
    monkeypatch.setattr(api.settings, "bootstrap_admin_password", PASSWORD)

    def fail_insert(_mapper, _connection, target):
        target.user_id = "missing-user"

    event.listen(ProjectMember, "before_insert", fail_insert)
    try:
        with sessions() as session:
            with pytest.raises(IntegrityError):
                api._bootstrap(session)
            # A failed bootstrap explicitly rolls back so the session is usable.
            assert session.scalar(select(func.count()).select_from(User)) == 1
    finally:
        event.remove(ProjectMember, "before_insert", fail_insert)
    assert row_counts(sessions) == before
    with sessions() as session:
        api._bootstrap(session)
    after = row_counts(sessions)
    assert after == {name: count + (name not in {"audit_events", "refresh_tokens"})
                     for name, count in before.items()}
    with sessions() as session:
        api._bootstrap(session)
    assert row_counts(sessions) == after
    login = client.post("/auth/login", json={
        "email": "BOOTSTRAP@example.com", "password": PASSWORD,
    })
    assert login.status_code == 200


def test_new_projects_and_conversation_messages_obey_foreign_keys(isolated_onboarding):
    client, sessions = isolated_onboarding
    response = register(client)
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    user = client.get("/me", headers=headers).json()
    project = client.post("/projects", headers=headers, json={"name": "Next Project"})
    assert project.status_code == 201, project.text
    with sessions() as session:
        api._record_message(
            session, user_id=user["id"], client_session_id="new-conversation",
            project_id=project.json()["id"], role="user", content="Hello",
        )
        assert session.scalar(select(func.count()).select_from(Conversation)) == 1
        assert session.scalar(select(Message.content)) == "Hello"


@pytest.mark.parametrize("sqlstate,table_name,constraint_name,expected", [
    ("23505", "users", "ix_users_email", True),
    ("23505", "users", "users_email_key", True),
    ("23505", "users", "users_pkey", False),
    ("23505", "projects", "ix_users_email", False),
    ("23503", "users", "ix_users_email", False),
])
def test_postgres_duplicate_detection_is_constraint_specific(
    sqlstate, table_name, constraint_name, expected,
):
    class DriverError(Exception):
        pass

    original = DriverError("internal database diagnostic")
    original.sqlstate = sqlstate
    original.diag = SimpleNamespace(table_name=table_name, constraint_name=constraint_name)
    assert api._is_duplicate_email(IntegrityError("INSERT", {}, original)) is expected
