from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from packages.db import Conversation, Message, User, session_scope
from services.api import app
from services.api.router import route_agent


class FakeAdapters:
    async def aclose(self):
        return None

    async def retrieve(self, *args):
        return []

    async def llm(self, messages, *, mode, context):
        yield f"mode={mode}"

    async def monitoring_tool(self, name, args):
        return {"healthy": True, "tool": name}


def test_router_selects_only_controlled_modes_and_tools() -> None:
    assert route_agent("Fix this Python API error").mode == "coding"
    monitoring = route_agent("Show CPU health", project_id="project-1")
    assert monitoring.mode == "monitoring"
    assert monitoring.tool_name == "get_project_summary"
    assert route_agent("hello", requested_mode="arbitrary").mode == "personal"


def test_registration_projects_and_chat_messages_persist() -> None:
    email = f"persist-{uuid.uuid4().hex}@example.com"
    with TestClient(app) as client:
        app.state.adapters = FakeAdapters()
        registered = client.post(
            "/auth/register",
            json={
                "email": email,
                "password": "correct-horse-battery-staple",
                "display_name": "Persistence Test",
                "tenant_name": "Test Tenant",
            },
        )
        assert registered.status_code == 201
        headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
        project = client.post("/projects", headers=headers, json={"name": "Clinic"})
        assert project.status_code == 201
        chat = client.post(
            "/chat",
            headers=headers,
            json={"session_id": "persistent-session", "message": "hello"},
        )
        assert chat.status_code == 200
        with session_scope() as session:
            user = session.scalar(select(User).where(User.email == email))
            conversation = session.scalar(
                select(Conversation).where(Conversation.user_id == user.id)
            )
            assert conversation is not None
            messages = session.scalars(
                select(Message).where(Message.conversation_id == conversation.id)
            ).all()
            assert [message.role for message in messages] == ["user", "assistant"]
