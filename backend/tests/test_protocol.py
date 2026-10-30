import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.stt import MAX_AUDIO_BYTES, transcribe
from app.services.groq_errors import ServiceError


class ProtocolTests(unittest.TestCase):
    def test_health(self):
        with TestClient(app) as client:
            self.assertEqual(client.get("/health").json(), {"status": "ok"})

    def test_text_stream_and_session_history(self):
        seen = []

        async def fake_reply(history, text):
            seen.append((list(history), text))
            yield {"type": "chat_delta", "text": "Hello"}
            yield {"type": "chat_delta", "text": " there"}

        with patch("app.main.stream_agent", fake_reply), TestClient(app) as client:
            with client.websocket_connect("/ws") as ws:
                self.assertEqual(ws.receive_json(), {"type": "connection", "status": "connected"})
                ws.send_json({"type": "ping"})
                self.assertEqual(ws.receive_json(), {"type": "pong"})
                ws.send_json({"type": "message", "requestId": "one", "text": "Hi", "speak": False})
                self.assertEqual([ws.receive_json()["type"] for _ in range(4)],
                                 ["chat_started", "chat_delta", "chat_delta", "chat_done"])
                ws.send_json({"type": "message", "requestId": "two", "text": "Again", "speak": False})
                for _ in range(4):
                    ws.receive_json()
                self.assertEqual(seen[1][0], [
                    {"role": "user", "content": "Hi"},
                    {"role": "assistant", "content": "Hello there"},
                ])
                ws.send_json({"type": "clear"})
                self.assertEqual(ws.receive_json(), {"type": "cleared"})
                ws.send_json({"type": "message", "requestId": "three", "text": "Fresh", "speak": False})
                for _ in range(4):
                    ws.receive_json()
                self.assertEqual(seen[2][0], [])
                ws.close()

    def test_audio_transcript_then_chat(self):
        async def fake_reply(history, text):
            yield {"type": "chat_delta", "text": "Answer"}

        with patch("app.main.stream_agent", fake_reply), \
             patch("app.main.transcribe", new=AsyncMock(return_value="Recognized words")) as stt, \
             TestClient(app) as client:
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()
                audio = b"test audio bytes"
                ws.send_json({"type": "audio_start", "requestId": "audio-1", "mimeType": "audio/webm;codecs=opus", "size": len(audio), "speak": False})
                ws.send_bytes(audio)
                events = [ws.receive_json() for _ in range(4)]
                self.assertEqual([event["type"] for event in events],
                                 ["transcript", "chat_started", "chat_delta", "chat_done"])
                self.assertEqual(events[0]["text"], "Recognized words")
                stt.assert_awaited_once_with(audio, "audio/webm;codecs=opus")
                ws.close()

    def test_unsupported_audio_and_missing_key(self):
        with TestClient(app) as client, client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "audio_start", "requestId": "bad", "mimeType": "audio/unknown", "size": 10})
            self.assertEqual(ws.receive_json()["type"], "error")
            with patch("app.services.llm.groq_api_key", return_value=""):
                ws.send_json({"type": "message", "requestId": "missing", "text": "Hi", "speak": False})
                self.assertEqual(ws.receive_json()["type"], "chat_started")
                self.assertIn("GROQ_API_KEY", ws.receive_json()["message"])
            ws.close()

    def test_stt_uses_recorded_format_and_rejects_oversize(self):
        with patch("app.services.stt.groq_api_key", return_value="test-key"), \
             patch("app.services.stt.AsyncGroq") as client_factory:
            create = AsyncMock(return_value=SimpleNamespace(text="  Hello  ", no_speech_prob=0.01, avg_logprob=-0.1))
            client_factory.return_value.__aenter__.return_value.audio.transcriptions.create = create
            self.assertEqual(asyncio.run(transcribe(b"audio", "audio/ogg;codecs=opus")), "Hello")
            create.assert_awaited_once_with(
                model="whisper-large-v3-turbo",
                file=("recording.ogg", b"audio", "audio/ogg;codecs=opus"),
                response_format="verbose_json",
                temperature=0,
                language="en",
            )
            with self.assertRaises(ServiceError):
                asyncio.run(transcribe(b"x" * (MAX_AUDIO_BYTES + 1), "audio/ogg"))

    def test_stt_rejects_low_confidence_without_calling_agent(self):
        async def fail_agent(history, text):
            raise AssertionError("low-confidence transcript reached the agent")
            yield  # pragma: no cover

        with patch("app.services.stt.groq_api_key", return_value="test-key"), \
             patch("app.services.stt.AsyncGroq") as client_factory, \
             patch("app.main.stream_agent", fail_agent), \
             TestClient(app) as client:
            client_factory.return_value.__aenter__.return_value.audio.transcriptions.create = AsyncMock(
                return_value=SimpleNamespace(text="Thank you", no_speech_prob=0.995, avg_logprob=-3.0)
            )
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()
                audio = b"background noise"
                ws.send_json({"type": "audio_start", "requestId": "quiet", "mimeType": "audio/webm", "size": len(audio), "speak": False})
                ws.send_bytes(audio)
                event = ws.receive_json()
                self.assertEqual(event["type"], "error")
                self.assertIn("No clear speech", event["message"])


if __name__ == "__main__":
    unittest.main()
