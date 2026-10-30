from app.tools.errors import ToolError


_EVENTS: list[dict[str, str]] = []


def schedule_event(title: str, when: str) -> dict:
    title = title.strip()
    when = when.strip()
    if not 1 <= len(title) <= 120 or not 1 <= len(when) <= 120:
        raise ToolError("Provide a title and time shorter than 120 characters.")
    event = {"title": title, "when": when}
    _EVENTS.append(event)
    return {**event, "saved": True, "storage": "session memory"}


def list_events() -> list[dict[str, str]]:
    return list(_EVENTS)
