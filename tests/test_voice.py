import asyncio
import base64
import unittest

from jarvis.adapters import FakeLlm, FakeStt, FakeTts, normalize_pcm16_mono
from jarvis.voice import VoiceSession


class SlowLlm(FakeLlm):
    async def stream(self, messages):
        for _ in range(100):
            await asyncio.sleep(0.01)
            yield "token "


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    def test_pcm_is_resampled_to_16khz(self):
        source = b"\x00\x00" * 800
        self.assertEqual(len(normalize_pcm16_mono(source, 8000)), len(source) * 2)

    async def test_binary_audio_to_transcript_tokens_and_wav(self):
        events = []

        async def send(event):
            events.append(event)

        session = VoiceSession(
            FakeStt("தமிழ் வணக்கம்"), FakeLlm("சரி நண்பா"), FakeTts(), "system safety", 4096
        )
        ready = session.start({"request_id": "turn-1", "sample_rate": 16000, "locale": "ta-IN"})
        session.add_audio(b"\x00\x00" * 100)
        await session.stop(send)
        self.assertEqual(ready["request_id"], "turn-1")
        self.assertEqual(
            [e["type"] for e in events], ["transcript", "token", "token", "audio", "final"]
        )
        audio = next(e for e in events if e["type"] == "audio")
        self.assertEqual(audio["media_type"], "audio/wav")
        self.assertTrue(base64.b64decode(audio["data"]).startswith(b"RIFF"))

    async def test_client_transcript_not_required(self):
        events = []
        session = VoiceSession(FakeStt("server transcript"), FakeLlm(), FakeTts(), "system", 1000)
        session.start({})
        session.add_audio(b"\x00\x00")
        await session.stop(events.append if False else self._sender(events))
        self.assertEqual(events[0]["text"], "server transcript")

    def _sender(self, events):
        async def send(event):
            events.append(event)

        return send

    async def test_barge_in_cancels_active_turn(self):
        events = []

        async def send(event):
            events.append(event)

        session = VoiceSession(FakeStt(), SlowLlm(), FakeTts(), "system", 1000)
        session.start({})
        session.add_audio(b"\x00\x00")
        task = asyncio.create_task(session.stop(send))
        await asyncio.sleep(0.03)
        await session.cancel(send)
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(events[-1]["type"], "cancelled")


if __name__ == "__main__":
    unittest.main()
