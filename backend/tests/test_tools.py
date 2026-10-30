import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.agents.voice_agent import MAX_TOOL_ROUNDS, stream_agent
from app.tools.calculator import calculate
from app.tools.errors import ToolError
from app.tools.registry import execute_tool
from app.tools.scheduler import schedule_event
from app.tools.search import web_search
from app.tools.weather import get_weather


def chunk(content=None, calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=calls))])


def call(index, call_id=None, name=None, arguments=None):
    return SimpleNamespace(index=index, id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


async def chunks(*items):
    for item in items:
        yield item


class FakeGroq:
    def __init__(self, streams):
        self.streams = streams
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=self.create)))

    async def create(self, **kwargs):
        return self.streams.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


class CalculatorTests(unittest.TestCase):
    def test_arithmetic_and_precedence(self):
        self.assertEqual(calculate("2 + 3 * (4 - 1)")["result"], "11")
        self.assertEqual(calculate("-2 ** 3 + 10 / 4")["result"], "-5.5")

    def test_rejects_unsafe_and_excessive_expressions(self):
        for expression in ("__import__('os').system('whoami')", "2 / 0", "2 ** 1000", "[1, 2]", "True + 1", "9" * 100):
            with self.subTest(expression=expression), self.assertRaises(ToolError):
                calculate(expression)

    def test_registry_rejects_invalid_arguments_and_unknown_tools(self):
        for name, arguments in (("calculate", '{"expression": 3}'), ("calculate", '{"expression":"1+1","extra":1}'), ("unknown", "{}"), ("calculate", "not json")):
            with self.subTest(name=name, arguments=arguments), self.assertRaises(ToolError):
                asyncio.run(execute_tool(name, arguments))


class ToolWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiple_native_tool_calls_then_final_stream(self):
        first = chunks(
            chunk(calls=[call(0, "call_calc", "calculate", '{"expression":"2')]),
            chunk(calls=[call(0, arguments='+2"}'), call(1, "call_weather", "get_weather", '{"location":"Colombo"}')]),
        )
        second = chunks(chunk(content="Four"), chunk(content=" and sunny."))
        fake = FakeGroq([first, second])
        with patch("app.agents.voice_agent.make_client", return_value=fake), \
             patch("app.agents.voice_agent.execute_tool", new=AsyncMock(side_effect=[{"result": "4"}, {"temperature": 30}])) as execute:
            events = [event async for event in stream_agent([], "Calculate 2+2 and check Colombo weather")]

        self.assertEqual([event["type"] for event in events],
                         ["tool_activity"] * 4 + ["chat_delta"] * 2)
        self.assertEqual("".join(event["text"] for event in events if event["type"] == "chat_delta"), "Four and sunny.")
        self.assertEqual(execute.await_args_list[0].args, ("calculate", '{"expression":"2+2"}'))
        self.assertEqual(execute.await_args_list[1].args, ("get_weather", '{"location":"Colombo"}'))
        first_request = fake.chat.completions.create.await_args_list[0].kwargs
        self.assertEqual(first_request["tool_choice"], "auto")
        self.assertEqual(
            {schema["function"]["name"] for schema in first_request["tools"]},
            {"calculate", "get_weather", "web_search", "schedule_event"},
        )
        second_messages = fake.chat.completions.create.await_args_list[1].kwargs["messages"]
        self.assertEqual(second_messages[-3]["role"], "assistant")
        self.assertEqual([message["tool_call_id"] for message in second_messages[-2:]], ["call_calc", "call_weather"])
        self.assertTrue(all(json.loads(message["content"])["ok"] for message in second_messages[-2:]))

    async def test_normal_reply_uses_one_model_call(self):
        fake = FakeGroq([chunks(chunk(content="Hello"))])
        with patch("app.agents.voice_agent.make_client", return_value=fake):
            events = [event async for event in stream_agent([], "Hello")]
        self.assertEqual(events, [{"type": "chat_delta", "text": "Hello"}])
        self.assertEqual(fake.chat.completions.create.await_count, 1)

    async def test_invalid_tool_arguments_are_returned_to_model(self):
        fake = FakeGroq([
            chunks(chunk(calls=[call(0, "bad", "calculate", '{"expression":9}') ])),
            chunks(chunk(content="Please give a valid expression.")),
        ])
        with patch("app.agents.voice_agent.make_client", return_value=fake):
            events = [event async for event in stream_agent([], "Calculate this")]
        self.assertEqual(events[-1]["type"], "chat_delta")
        result = fake.chat.completions.create.await_args_list[1].kwargs["messages"][-1]
        self.assertFalse(json.loads(result["content"])["ok"])

    async def test_tool_round_limit(self):
        fake = FakeGroq([chunks(chunk(calls=[call(0, f"id{i}", "calculate", '{"expression":"1+1"}')])) for i in range(MAX_TOOL_ROUNDS + 1)])
        with patch("app.agents.voice_agent.make_client", return_value=fake), \
             patch("app.agents.voice_agent.execute_tool", new=AsyncMock(return_value={"result": "2"})):
            with self.assertRaisesRegex(Exception, "tool request"):
                [event async for event in stream_agent([], "Loop")]
        self.assertEqual(fake.chat.completions.create.await_count, MAX_TOOL_ROUNDS + 1)

    async def test_weather_geocoding_and_units(self):
        def respond(request: httpx.Request) -> httpx.Response:
            if "geocoding" in str(request.url):
                self.assertEqual(request.url.params["name"], "Colombo")
                return httpx.Response(200, json={"results": [{"name": "Colombo", "country": "Sri Lanka", "latitude": 6.9, "longitude": 79.9, "timezone": "Asia/Colombo"}]})
            self.assertEqual(request.url.params["temperature_unit"], "fahrenheit")
            self.assertEqual(request.url.params["timezone"], "Asia/Colombo")
            return httpx.Response(200, json={"timezone": "Asia/Colombo", "current": {"time": "2026-10-30T10:00", "temperature_2m": 86, "weather_code": 0}, "current_units": {"temperature_2m": "°F"}})

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch("app.tools.weather.httpx.AsyncClient", return_value=client):
            result = await get_weather("Colombo", "fahrenheit")
        self.assertEqual(result["temperature"], 86)
        self.assertEqual(result["temperature_unit"], "°F")
        self.assertEqual(result["timezone"], "Asia/Colombo")
        self.assertEqual(result["condition"], "Clear sky")

    async def test_weather_timeout_is_safe(self):
        def timeout(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(timeout))
        with patch("app.tools.weather.httpx.AsyncClient", return_value=client):
            with self.assertRaisesRegex(ToolError, "timed out"):
                await get_weather("Colombo")

    async def test_web_search_returns_quick_answer(self):
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"AbstractText": "A concise result.", "AbstractURL": "https://example.test"})
        ))
        with patch("app.tools.search.httpx.AsyncClient", return_value=client):
            result = await web_search("example query")
        self.assertEqual(result["answer"], "A concise result.")
        self.assertEqual(result["source"], "https://example.test")

    async def test_scheduler_is_explicitly_session_memory(self):
        result = schedule_event("Demo", "tomorrow at 9")
        self.assertTrue(result["saved"])
        self.assertEqual(result["storage"], "session memory")
