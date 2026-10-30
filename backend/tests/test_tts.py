import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.groq_errors import ServiceError
from app.services.speech_session import SpeechSession
from app.services.tts import MAX_TTS_CHARS, SentenceSegmenter, generate_wav


WAV = b"RIFF" + (36).to_bytes(4, "little") + b"WAVE" + b"\x00" * 32


class SegmenterTests(unittest.TestCase):
    def test_sentence_chunks_are_bounded_and_ordered(self):
        segmenter = SentenceSegmenter()
        self.assertEqual(segmenter.feed("Hello there. How "), ["Hello there."])
        self.assertEqual(segmenter.feed("are you?"), ["How are you?"])
        self.assertEqual(segmenter.flush(), [])
        long_text = "This is a long sentence " * 20
        long_segmenter = SentenceSegmenter()
        chunks = long_segmenter.feed(long_text) + long_segmenter.flush()
        self.assertTrue(chunks)
        self.assertTrue(all(0 < len(chunk) <= MAX_TTS_CHARS for chunk in chunks))

    def test_internal_json_is_not_spoken(self):
        segmenter = SentenceSegmenter()
        self.assertEqual(segmenter.feed('{"tool_calls": [{"name": "get_weather", "text": "Sentence one. Sentence two."}]}. '), [])
        self.assertEqual(segmenter.flush(), [])
        segmenter = SentenceSegmenter()
        self.assertEqual(segmenter.feed('```json\n{"answer":"Do not say this. Or this."}\n```'), [])
        self.assertEqual(segmenter.flush(), [])


class TTSServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_generates_wav_with_required_model_and_voice(self):
        with patch("app.services.tts.groq_api_key", return_value="test-key"), \
             patch("app.services.tts.AsyncGroq") as client_factory:
            response = AsyncMock(return_value=SimpleNamespace(read=AsyncMock(return_value=WAV)))
            client_factory.return_value.__aenter__.return_value.audio.speech.create = response
            result = await generate_wav("Hello there.")
            self.assertEqual(result, WAV)
            response.assert_awaited_once_with(
                model="canopylabs/orpheus-v1-english", voice="troy",
                input="Hello there.", response_format="wav",
            )

    async def test_too_long_or_internal_text_never_calls_sdk(self):
        with patch("app.services.tts.AsyncGroq") as client_factory:
            for text in ("x" * 201, '{"tool_calls": []}', ""):
                with self.assertRaises(ServiceError):
                    await generate_wav(text)
            client_factory.assert_not_called()

    async def test_audio_metadata_binary_order_and_fallback(self):
        class FakeWebSocket:
            def __init__(self):
                self.frames = []

            async def send_json(self, event):
                self.frames.append(("json", event))

            async def send_bytes(self, data):
                self.frames.append(("binary", data))

        websocket = FakeWebSocket()
        session = SpeechSession(websocket)
        with patch("app.services.speech_session.generate_wav", new=AsyncMock(return_value=WAV)) as synthesize:
            await session.start("reply-1", True)
            session.enqueue("First sentence.")
            session.enqueue("Second sentence.")
            session.finish()
            await asyncio.wait_for(session.task, timeout=1)
        self.assertEqual([kind for kind, _ in websocket.frames], ["json", "binary", "json", "binary", "json"])
        self.assertEqual([websocket.frames[0][1]["index"], websocket.frames[2][1]["index"]], [0, 1])
        self.assertEqual(websocket.frames[0][1]["segmentId"], "reply-1:0")
        self.assertEqual(websocket.frames[-1][1]["segmentCount"], 2)
        self.assertEqual(synthesize.await_count, 2)

        websocket = FakeWebSocket()
        session = SpeechSession(websocket)
        with patch("app.services.speech_session.generate_wav", new=AsyncMock(side_effect=ServiceError("rate limit"))) as synthesize:
            await session.start("reply-2", True)
            session.enqueue("First sentence.")
            session.enqueue("Second sentence.")
            session.finish()
            await asyncio.wait_for(session.task, timeout=1)
        self.assertEqual([event["type"] for _, event in websocket.frames], ["tts_fallback", "tts_fallback", "tts_complete"])
        self.assertEqual(synthesize.await_count, 1)


class SpeechProtocolTests(unittest.TestCase):
    def test_websocket_streams_text_and_identified_wav_segments(self):
        async def fake_agent(history, text):
            yield {"type": "chat_delta", "text": "Hello."}
            yield {"type": "chat_delta", "text": " How are you?"}

        with patch("app.main.stream_agent", fake_agent), \
             patch("app.services.speech_session.generate_wav", new=AsyncMock(return_value=WAV)), \
             TestClient(app) as client:
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()
                ws.send_json({"type": "message", "requestId": "spoken", "text": "Hi", "speak": True})
                frames = []
                while True:
                    raw = ws.receive()
                    if raw.get("text") is not None:
                        event = json.loads(raw["text"])
                        frames.append(event)
                        if event["type"] == "tts_complete":
                            break
                    else:
                        frames.append(raw["bytes"])
                audio_positions = [i for i, frame in enumerate(frames) if isinstance(frame, dict) and frame["type"] == "tts_audio"]
                self.assertEqual(len(audio_positions), 2)
                self.assertEqual([frames[i]["segmentId"] for i in audio_positions], ["spoken:0", "spoken:1"])
                self.assertTrue(all(frames[i + 1] == WAV for i in audio_positions))
                self.assertIn("chat_done", [frame["type"] for frame in frames if isinstance(frame, dict)])
                ws.close()
