"""
In-memory scheduling tool. Demonstrates the tool-calling pattern for a stateful
action without needing OAuth against a real calendar provider. Swap this for a
Google Calendar / CalDAV client later without touching the agent loop.
"""

SCHEMA = {
    "name": "schedule_event",
    "description": "Schedule a reminder or event at a given time.",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "What the event is"},
            "when": {
                "type": "string",
                "description": "When it happens, e.g. 'tomorrow 3pm' or '2026-07-15 09:00'",
            },
        },
        "required": ["title", "when"],
    },
}

_EVENTS: list[dict] = []


async def run(title: str, when: str) -> str:
    _EVENTS.append({"title": title, "when": when})
    return f"Scheduled '{title}' for {when}."


def list_events() -> list[dict]:
    return list(_EVENTS)
