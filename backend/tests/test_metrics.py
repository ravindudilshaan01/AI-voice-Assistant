import asyncio
import unittest
from unittest.mock import patch

from app.services.metrics import MetricsStore, summarize
from app.tools.errors import ToolError
from app.tools.registry import TOOLS, execute_tool


class MetricsTests(unittest.IsolatedAsyncioTestCase):
    def test_percentiles_use_observed_values(self):
        summary = summarize([1, 2, 3, 4, 100])
        self.assertEqual(summary, {"count": 5, "p50": 3, "p95": 100, "min": 1, "max": 100})

    async def test_store_is_bounded_and_tracks_counts(self):
        store = MetricsStore(capacity=2)
        for value in (1, 2, 3):
            await store.observe("latency", value)
        await store.increment("success")
        snapshot = await store.snapshot()
        self.assertEqual(snapshot["durations_ms"]["latency"]["count"], 2)
        self.assertEqual(snapshot["durations_ms"]["latency"]["min"], 2)
        self.assertEqual(snapshot["counts"]["success"], 1)

    async def test_async_tools_have_a_timeout(self):
        async def never_finishes(**_):
            await asyncio.Event().wait()

        from dataclasses import replace
        patched_tools = dict(TOOLS)
        patched_tools["get_weather"] = replace(TOOLS["get_weather"], execute=never_finishes)
        with patch("app.tools.registry.TOOLS", patched_tools), patch("app.tools.registry.TOOL_TIMEOUT_SECONDS", 0.01):
            with self.assertRaisesRegex(ToolError, "timed out"):
                await execute_tool("get_weather", '{"location":"Colombo"}')
