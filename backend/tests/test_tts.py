import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from groq import RateLimitError

from app.main import app, deliver_reminder
from app.services.groq_errors import ServiceError
from app.services.speech_session import SpeechSession
from app.services.tts import MAX_TTS_CHARS, TTS_VOICE, SentenceSegmenter, _quota_wait_text, _rate_limit_details, generate_wav, spoken_text


WAV = b"RIFF" + (36).to_bytes(4, "little") + b"WAVE" + b"\x00" * 32


class SegmenterTests(unittest.TestCase):
    def test_rate_limit_headers_distinguish_short_waits_from_daily_quota(self):
        transient = SimpleNamespace(response=SimpleNamespace(headers={"retry-after": "8", "x-ratelimit-remaining-requests": "42"}))
        self.assertEqual(_rate_limit_details(transient), (8.0, False))
        daily = SimpleNamespace(response=SimpleNamespace(headers={"retry-after": "3600", "x-ratelimit-remaining-requests": "0"}))
        self.assertEqual(_rate_limit_details(daily), (30.0, True))
        token_daily = SimpleNamespace(response=SimpleNamespace(headers={"retry-after": "3456", "x-ratelimit-remaining-requests": "90"}), body={"error": {"message": "tokens per day (TPD) reached"}})
        self.assertEqual(_rate_limit_details(token_daily), (30.0, True))
        self.assertEqual(_quota_wait_text(3456), "about 58 minutes")

    def test_sentence_chunks_are_bounded_and_ordered(self):
        segmenter = SentenceSegmenter()
        self.assertEqual(segmenter.feed("Hello there. How "), [])
        self.assertEqual(segmenter.feed("are you?"), [])
        self.assertEqual(segmenter.flush(), ["Hello there. How are you?"])
        long_text = "This is a long sentence " * 20
        long_segmenter = SentenceSegmenter()
        chunks = long_segmenter.feed(long_text) + long_segmenter.flush()
        self.assertTrue(chunks)
        self.assertTrue(all(0 < len(chunk) <= MAX_TTS_CHARS for chunk in chunks))

    def test_spoken_numbers_and_weather_units_are_normalized(self):
        self.assertEqual(spoken_text("It is 26.5°C with 71.2% humidity."),
                         "It is 26 point 5 degrees Celsius with 71 point 2 percent humidity.")
        self.assertEqual(spoken_text("Wind is 12.5 km/h."),
                         "Wind is 12 point 5 kilometers per hour.")

    def test_internal_json_is_not_spoken(self):
        segmenter = SentenceSegmenter()
        self.assertEqual(segmenter.feed('{"tool_calls": [{"name": "get_weather", "text": "Sentence one. Sentence two."}]}. '), [])
        self.assertEqual(segmenter.flush(), [])
        segmenter = SentenceSegmenter()
        self.assertEqual(segmenter.feed('```json\n{"answer":"Do not say this. Or this."}\n```'), [])
        self.assertEqual(segmenter.flush(), [])


class TTSServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_rate_limit_waits_for_another_slot_and_keeps_cloud_voice(self):
        response = httpx.Response(
            429,
            request=httpx.Request("POST", "https://api.groq.com/openai/v1/audio/speech"),
            headers={"retry-after": "1", "x-ratelimit-remaining-requests": "12"},
        )
        rate_error = RateLimitError("rate limited", response=response, body={"error": {"message": "rate limited"}})
        create = AsyncMock(side_effect=[rate_error, SimpleNamespace(read=AsyncMock(return_value=WAV))])
        with patch("app.services.tts.groq_api_key", return_value="test-key"), \
             patch("app.services.tts._wait_for_tts_slot", new=AsyncMock()), \
             patch("app.services.tts.asyncio.sleep", new=AsyncMock()) as wait, \
             patch("app.services.tts.AsyncGroq") as client_factory:
            client_factory.return_value.__aenter__.return_value.audio.speech.create = create
            result = await generate_wav("Keep the natural voice.", "autumn")
        self.assertEqual(result, WAV)
        self.assertEqual(create.await_count, 2)
        wait.assert_awaited_once()

    async def test_reminder_event_and_audio_are_delivered_as_one_priority_response(self):
        class FakeSpeech:
            def __init__(self):
                self.events = []
                self.audio = []

            async def send_json(self, event):
                self.events.append(event)

            async def send_audio(self, response_id, index, text, audio):
                self.audio.append((response_id, index, text, audio))

        speech = FakeSpeech()
        reminder = {"title": "Check the oven", "due_at": "2020-11-07T20:00:00+05:30"}
        with patch("app.main.generate_wav", new=AsyncMock(return_value=WAV)):
            await deliver_reminder(speech, reminder, "autumn")
        self.assertEqual(speech.events[0]["type"], "reminder")
        self.assertEqual(speech.events[-1], {"type": "tts_complete", "responseId": speech.events[0]["reminderId"], "segmentCount": 1})
        self.assertEqual(speech.audio[0][1:], (0, "Reminder. Check the oven.", WAV))

    async def test_sdk_generates_wav_with_required_model_and_voice(self):
        with patch("app.services.tts.groq_api_key", return_value="test-key"), \
             patch("app.services.tts._wait_for_tts_slot", new=AsyncMock()), \
             patch("app.services.tts.AsyncGroq") as client_factory:
            response = AsyncMock(return_value=SimpleNamespace(read=AsyncMock(return_value=WAV)))
            client_factory.return_value.__aenter__.return_value.audio.speech.create = response
            result = await generate_wav("Hello there.")
            self.assertEqual(result, WAV)
            response.assert_awaited_once_with(
                model="canopylabs/orpheus-v1-english", voice=TTS_VOICE,
                input="Hello there.", response_format="wav",
            )
            response.reset_mock()
            self.assertEqual(await generate_wav("Hello again.", "autumn"), WAV)
            response.assert_awaited_once_with(
                model="canopylabs/orpheus-v1-english", voice="autumn",
                input="Hello again.", response_format="wav",
            )

    async def test_too_long_or_internal_text_never_calls_sdk(self):
        with patch("app.services.tts.AsyncGroq") as client_factory:
            for text in ("x" * 201, '{"tool_calls": []}', ""):
                with self.assertRaises(ServiceError):
                    await generate_wav(text)
            with self.assertRaises(ServiceError):
                await generate_wav("Hello", "unsupported")
            client_factory.assert_not_called()

    async def test_audio_metadata_binary_order_and_consistent_cloud_failure(self):
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
            await session.start("reply-1", True, "autumn")
            session.enqueue("First sentence.")
            session.enqueue("Second sentence.")
            session.finish()
            await asyncio.wait_for(session.task, timeout=1)
        self.assertEqual([kind for kind, _ in websocket.frames], ["json", "binary", "json", "binary", "json"])
        self.assertEqual([websocket.frames[0][1]["index"], websocket.frames[2][1]["index"]], [0, 1])
        self.assertEqual(websocket.frames[0][1]["segmentId"], "reply-1:0")
        self.assertEqual(websocket.frames[-1][1]["segmentCount"], 2)
        self.assertEqual(synthesize.await_count, 2)
        self.assertEqual([call.args[1] for call in synthesize.await_args_list], ["autumn", "autumn"])

        websocket = FakeWebSocket()
        session = SpeechSession(websocket)
        with patch("app.services.speech_session.generate_wav", new=AsyncMock(side_effect=ServiceError("rate limit"))) as synthesize:
            await session.start("reply-2", True)
            session.enqueue("First sentence.")
            session.enqueue("Second sentence.")
            session.finish()
            await asyncio.wait_for(session.task, timeout=1)
        self.assertEqual([event["type"] for _, event in websocket.frames], ["tts_error", "tts_complete"])
        self.assertEqual(websocket.frames[-1][1]["segmentCount"], 0)
        self.assertEqual(synthesize.await_count, 1)


class SpeechProtocolTests(unittest.TestCase):
    def test_websocket_streams_text_and_identified_wav_segments(self):
        async def fake_agent(history, text):
            yield {"type": "chat_delta", "text": "Hello."}
            yield {"type": "chat_delta", "text": " How are you?"}

        with patch("app.main.stream_agent", fake_agent), \
             patch("app.services.speech_session.generate_wav", new=AsyncMock(return_value=WAV)) as synthesize, \
             TestClient(app) as client:
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()
                ws.send_json({"type": "message", "requestId": "spoken", "text": "Hi", "speak": True, "voice": "autumn"})
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
                self.assertEqual(len(audio_positions), 1)
                self.assertEqual([frames[i]["segmentId"] for i in audio_positions], ["spoken:0"])
                self.assertTrue(all(frames[i + 1] == WAV for i in audio_positions))
                self.assertIn("chat_done", [frame["type"] for frame in frames if isinstance(frame, dict)])
                self.assertEqual([call.args[0] for call in synthesize.await_args_list], ["Hello. How are you?"])
                self.assertEqual([call.args[1] for call in synthesize.await_args_list], ["autumn"])
                ws.close()
