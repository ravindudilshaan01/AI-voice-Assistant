import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import APP_TIMEZONE
from app.tools.errors import ToolError


_EVENTS: list[dict[str, str | bool]] = []
_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60,
}


def _duration_amount(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    parts = value.replace("-", " ").split()
    if not parts or any(part not in _NUMBER_WORDS for part in parts):
        return None
    if len(parts) == 1:
        return _NUMBER_WORDS[parts[0]]
    if len(parts) == 2 and _NUMBER_WORDS[parts[0]] >= 20 and _NUMBER_WORDS[parts[1]] < 10:
        return _NUMBER_WORDS[parts[0]] + _NUMBER_WORDS[parts[1]]
    return None


def _timezone() -> ZoneInfo:
    try:
        return ZoneInfo(APP_TIMEZONE)
    except ZoneInfoNotFoundError as error:
        raise ToolError("The reminder timezone is not configured correctly.") from error


def parse_reminder_time(when: str, now: datetime | None = None) -> datetime:
    """Resolve common spoken reminder times without silently guessing."""
    current = now or datetime.now(_timezone())
    if current.tzinfo is None:
        current = current.replace(tzinfo=_timezone())
    value = " ".join(when.casefold().strip().split())

    relative = re.fullmatch(r"in\s+([a-z\d-]+(?:\s+[a-z-]+)?)\s+(second|seconds|minute|minutes|hour|hours)", value)
    if relative:
        amount = _duration_amount(relative.group(1))
        if amount is None:
            raise ToolError("I could not understand that reminder duration. Say, for example, 'in ten minutes'.")
        if amount < 1:
            raise ToolError("The reminder time must be in the future.")
        unit = relative.group(2)
        seconds = amount * (1 if unit.startswith("second") else 60 if unit.startswith("minute") else 3600)
        return current + timedelta(seconds=seconds)

    iso_value = when.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_value)
    except ValueError:
        parsed = None
    if parsed is not None:
        due = parsed.replace(tzinfo=_timezone()) if parsed.tzinfo is None else parsed.astimezone(_timezone())
        if due <= current:
            raise ToolError("The reminder time has already passed.")
        return due

    day_offset = 1 if "tomorrow" in value else 0
    time_match = re.search(r"(?:^|\bat\s+)(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", value)
    if not time_match:
        raise ToolError("I could not understand that reminder time. Say a time such as 'in 10 minutes' or 'tomorrow at 9 AM'.")
    hour = int(time_match.group(1))
    minute = int(time_match.group(2) or 0)
    meridiem = time_match.group(3)
    if minute > 59 or hour > 23 or hour < 0:
        raise ToolError("That reminder time is invalid.")
    if meridiem:
        if not 1 <= hour <= 12:
            raise ToolError("Use an hour from 1 to 12 with AM or PM.")
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    elif hour <= 12:
        raise ToolError("Please say AM or PM so the reminder time is exact.")

    due = current.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=day_offset)
    if day_offset == 0 and due <= current:
        if "today" in value:
            raise ToolError("That reminder time has already passed today.")
        due += timedelta(days=1)
    return due


def schedule_event(title: str, when: str) -> dict:
    title = title.strip()
    when = when.strip()
    if not 1 <= len(title) <= 120 or not 1 <= len(when) <= 120:
        raise ToolError("Provide a title and time shorter than 120 characters.")
    due = parse_reminder_time(when)
    event = {
        "title": title,
        "when": when,
        "due_at": due.isoformat(),
        "display_time": due.strftime("%A, %d %B at %I:%M:%S %p %Z").replace(" 0", " "),
    }
    _EVENTS.append(event)
    return {**event, "saved": True, "storage": "this connection"}


def list_events() -> list[dict[str, str | bool]]:
    return list(_EVENTS)
