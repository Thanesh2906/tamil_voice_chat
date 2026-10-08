from __future__ import annotations

import asyncio

import pytest

from services.api import _voice_reply


class Socket:
    def __init__(self):
        self.events = []
        self.audio = asyncio.Event()

    async def send_json(self, event):
        self.events.append(event)
        if event["type"] == "audio":
            self.audio.set()


@pytest.mark.asyncio
async def test_speech_begins_before_model_finishes(monkeypatch):
    socket = Socket()

    class Adapter:
        async def llm(self, *args, **kwargs):
            yield "வணக்கம்! "
            # First audio must be sent before the model continues.
            await asyncio.wait_for(socket.audio.wait(), 1)
            yield "Let's build it."

        async def synthesize(self, phrase):
            return {"audio": "00", "phrase": phrase}

    monkeypatch.setattr("services.api._record_message", lambda *args, **kwargs: None)
    await _voice_reply(socket, Adapter(), request_id="req", session_id="sess",
                       message="hello", context=None, user_id="user", project_id=None)
    types = [event["type"] for event in socket.events]
    assert types == ["token", "audio", "token", "audio", "final"]
    assert [event["data"]["sequence"] for event in socket.events if event["type"] == "audio"] == [0, 1]
    assert socket.events[-1]["data"]["text"] == "வணக்கம்! Let's build it."


@pytest.mark.asyncio
async def test_synthesis_failure_emits_safe_error_and_cancels_generation():
    socket = Socket()
    cancelled = asyncio.Event()

    class Adapter:
        async def llm(self, *args, **kwargs):
            try:
                yield "Hello!"
                await asyncio.sleep(10)
            finally:
                cancelled.set()

        async def synthesize(self, phrase):
            raise RuntimeError("secret-token-in-upstream-error")

    await asyncio.wait_for(_voice_reply(socket, Adapter(), request_id="req", session_id="sess",
                           message="hello", context=None, user_id="user", project_id=None), 1)
    assert cancelled.is_set()
    assert socket.events[-1]["type"] == "error"
    assert "secret-token" not in str(socket.events)


@pytest.mark.asyncio
async def test_cancel_stops_synthesis_and_no_final_is_sent():
    socket = Socket()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class Adapter:
        async def llm(self, *args, **kwargs):
            yield "Hello!"

        async def synthesize(self, phrase):
            started.set()
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()

    task = asyncio.create_task(_voice_reply(socket, Adapter(), request_id="req", session_id="sess",
                               message="hello", context=None, user_id="user", project_id=None))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
    assert all(event["type"] != "final" for event in socket.events)
