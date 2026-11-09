import asyncio
import math
from collections import defaultdict, deque
from time import monotonic
from typing import Any


class MetricsStore:
    """Small in-memory metrics store. It never records transcript or audio content."""

    def __init__(self, capacity: int = 500) -> None:
        self.capacity = capacity
        self._values: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=capacity))
        self._counts: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def observe(self, name: str, duration_ms: float) -> None:
        if math.isfinite(duration_ms) and duration_ms >= 0:
            async with self._lock:
                self._values[name].append(round(duration_ms, 3))

    async def increment(self, name: str) -> None:
        async with self._lock:
            self._counts[name] += 1

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            values = {key: list(items) for key, items in self._values.items()}
            counts = dict(self._counts)
        return {
            "durations_ms": {name: summarize(items) for name, items in values.items()},
            "counts": counts,
        }


def summarize(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "p50": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0}

    def percentile(fraction: float) -> float:
        index = max(0, math.ceil(len(ordered) * fraction) - 1)
        return ordered[index]

    return {
        "count": len(ordered),
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "min": ordered[0],
        "max": ordered[-1],
    }


class TurnTimer:
    def __init__(self) -> None:
        self.started = monotonic()
        self.marks: dict[str, float] = {}

    def mark(self, name: str) -> float:
        now = monotonic()
        self.marks.setdefault(name, now)
        return now

    def elapsed_ms(self, start: str | None = None, end: str | None = None) -> float:
        start_time = self.started if start is None else self.marks[start]
        end_time = monotonic() if end is None else self.marks[end]
        return (end_time - start_time) * 1000


metrics = MetricsStore()
