import asyncio
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.config import GROQ_STT_MODEL, GROQ_STT_PROMPT
from app.services.stt import MAX_AUDIO_BYTES, transcribe
from app.services.groq_errors import ServiceError


class ProtocolTests(unittest.TestCase):
    def test_health(self):
        with TestClient(app) as client:
            self.assertEqual(client.get("/health").json(), {"status": "ok"})

    def test_local_ipv4_frontend_origin_can_connect(self):
        with TestClient(app) as client:
            with client.websocket_connect("/ws", headers={"origin": "http://127.0.0.1:5173"}) as ws:
                event = ws.receive_json()
                self.assertEqual((event["type"], event["status"], event["state"]), ("connection", "connected", "IDLE"))
                self.assertTrue(event["sessionId"])
                self.assertEqual(event["eventSeq"], 0)

    def test_text_stream_and_session_history(self):
        seen = []

        async def fake_reply(history, text):
            seen.append((list(history), text))
            yield {"type": "chat_delta", "text": "Hello"}
            yield {"type": "chat_delta", "text": " there"}

        with patch("app.main.stream_agent", fake_reply), TestClient(app) as client:
            with client.websocket_connect("/ws") as ws:
                connected = ws.receive_json()
                self.assertEqual((connected["type"], connected["status"]), ("connection", "connected"))
                ws.send_json({"type": "ping"})
                self.assertEqual(ws.receive_json()["type"], "pong")
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
                self.assertEqual(ws.receive_json()["type"], "cleared")
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

    def test_numbered_pcm_stream_reports_real_speech_activity(self):
        with TestClient(app) as client, client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "pcm_stream_start", "requestId": "pcm-1", "sampleRate": 16000, "format": "s16le"})
            self.assertEqual(ws.receive_json()["type"], "listening")
            samples = struct.pack("<320h", *([5000] * 320))
            for sequence in range(10):
                ws.send_bytes(struct.pack("<IdI", sequence, sequence * 20.0, 16000) + samples)
            started = ws.receive_json()
            self.assertEqual((started["type"], started["turnId"]), ("speech_started", "pcm-1"))
            ws.send_json({"type": "pcm_stream_end", "requestId": "pcm-1"})
            ended = ws.receive_json()
            self.assertEqual((ended["type"], ended["frames"], ended["speechDetected"]),
                             ("speech_ended", 10, True))

    def test_active_response_can_be_cancelled(self):
        async def slow_reply(history, text):
            yield {"type": "chat_delta", "text": "Starting"}
            await asyncio.Event().wait()

        with patch("app.main.stream_agent", slow_reply), TestClient(app) as client:
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()
                ws.send_json({"type": "message", "requestId": "cancel-me", "text": "Long answer", "speak": False})
                self.assertEqual(ws.receive_json()["type"], "chat_started")
                self.assertEqual(ws.receive_json()["type"], "chat_delta")
                ws.send_json({"type": "stop_generation"})
                cancelled = ws.receive_json()
                self.assertEqual((cancelled["type"], cancelled["requestId"], cancelled["turnId"]),
                                 ("chat_cancelled", "cancel-me", "cancel-me"))

    def test_tool_only_round_gets_a_safe_fallback_answer(self):
        async def tool_only(history, text):
            yield {"type": "tool_result", "name": "calculate", "label": "Calculating", "ok": True,
                   "result": {"expression": "15*8000", "result": "120000"}}

        with patch("app.main.stream_agent", tool_only), TestClient(app) as client:
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()
                ws.send_json({"type": "message", "requestId": "tool-only", "text": "Calculate", "speak": False})
                events = []
                while True:
                    event = ws.receive_json()
                    events.append(event)
                    if event["type"] in ("chat_done", "error"):
                        break
                deltas = "".join(event.get("text", "") for event in events if event["type"] == "chat_delta")
                self.assertEqual(deltas, "The result is 120000.")
                self.assertEqual(events[-1]["type"], "chat_done")

    def test_groq_response_releases_first_sentence_before_completion(self):
        from app.main import answer

        class FakeSpeech:
            def __init__(self):
                self.events = []

            async def start(self, *args):
                self.events.append("start")

            async def send_json(self, event):
                self.events.append(event.get("type"))

            async def enqueue(self, text):
                self.events.append(f"enqueue:{text}")

            def finish(self):
                self.events.append("finish")

            async def stop(self):
                self.events.append("stop")

        async def streamed_response(history, text):
            yield {"type": "chat_delta", "text": "First sentence. "}
            yield {"type": "chat_delta", "text": "Second sentence."}

        speech = FakeSpeech()
        with patch("app.main.stream_agent", streamed_response):
            asyncio.run(answer(speech, [], "Explain", "streaming", True, "female", "groq"))
        self.assertLess(speech.events.index("enqueue:First sentence."), speech.events.index("chat_done"))

    def test_speak_again_uses_cloud_tts_without_an_llm_call(self):
        with patch("app.services.speech_session.generate_wav", new=AsyncMock(return_value=b"wave")) as tts, \
             TestClient(app) as client:
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()
                ws.send_json({"type": "speak_text", "requestId": "replay-1", "text": "Twenty six point five degrees.", "voice": "autumn", "provider": "groq"})
                metadata = ws.receive_json()
                self.assertEqual(metadata["type"], "tts_audio")
                self.assertEqual(metadata["responseId"], "replay-1")
                self.assertEqual(ws.receive_bytes(), b"wave")
                complete = ws.receive_json()
                self.assertEqual((complete["type"], complete["responseId"], complete["segmentCount"]),
                                 ("tts_complete", "replay-1", 1))
                tts.assert_awaited_once_with("Twenty six point five degrees.", "autumn", "groq")

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
                model=GROQ_STT_MODEL,
                file=("recording.ogg", b"audio", "audio/ogg;codecs=opus"),
                response_format="verbose_json",
                temperature=0,
                language="en",
                prompt=GROQ_STT_PROMPT,
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
