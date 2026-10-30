import json
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.tools.calculator import calculate
from app.tools.errors import ToolError
from app.tools.scheduler import schedule_event
from app.tools.search import web_search
from app.tools.weather import get_weather


@dataclass(frozen=True)
class ToolSpec:
    name: str
    label: str
    description: str
    parameters: dict[str, Any]
    execute: Callable[..., Awaitable[dict] | dict]

    @property
    def groq_schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters,
        }}


TOOLS = {
    "get_weather": ToolSpec(
        name="get_weather", label="Checking weather",
        description="Get current weather for a city or place. Use when the user asks for current weather or temperature; do not use for forecasts.",
        parameters={"type": "object", "properties": {
            "location": {"type": "string", "description": "City or place, optionally with country, e.g. Colombo, Sri Lanka"},
            "unit": {"type": "string", "enum": ["celsius", "fahrenheit"], "description": "Temperature unit; default celsius"},
        }, "required": ["location"], "additionalProperties": False},
        execute=get_weather,
    ),
    "calculate": ToolSpec(
        name="calculate", label="Calculating",
        description="Calculate an arithmetic expression. Use when exact arithmetic is needed.",
        parameters={"type": "object", "properties": {
            "expression": {"type": "string", "description": "Arithmetic with numbers, parentheses, +, -, *, /, %, and **"},
        }, "required": ["expression"], "additionalProperties": False},
        execute=calculate,
    ),
    "web_search": ToolSpec(
        name="web_search", label="Searching the web",
        description="Search the web for a current factual answer when built-in knowledge may be outdated.",
        parameters={"type": "object", "properties": {
            "query": {"type": "string", "description": "A concise web search query"},
        }, "required": ["query"], "additionalProperties": False},
        execute=web_search,
    ),
    "schedule_event": ToolSpec(
        name="schedule_event", label="Saving reminder",
        description="Save a reminder or event in this assistant session. Tell the user it is not synced to an external calendar.",
        parameters={"type": "object", "properties": {
            "title": {"type": "string", "description": "What the event or reminder is for"},
            "when": {"type": "string", "description": "When it should happen, as stated by the user"},
        }, "required": ["title", "when"], "additionalProperties": False},
        execute=schedule_event,
    ),
}
TOOL_SCHEMAS = [spec.groq_schema for spec in TOOLS.values()]


def parse_arguments(name: str, raw_arguments: str) -> dict[str, Any]:
    spec = TOOLS.get(name)
    if spec is None:
        raise ToolError("Unknown tool requested.")
    if not isinstance(raw_arguments, str) or len(raw_arguments) > 2048:
        raise ToolError("Tool arguments are too large or invalid.")
    try:
        args = json.loads(raw_arguments)
    except (ValueError, TypeError) as error:
        raise ToolError("Tool arguments are not valid JSON.") from error
    if not isinstance(args, dict):
        raise ToolError("Tool arguments must be a JSON object.")
    properties = spec.parameters["properties"]
    if set(args) - set(properties) or set(spec.parameters["required"]) - set(args):
        raise ToolError("Tool arguments do not match the required schema.")
    for key, value in args.items():
        schema = properties[key]
        if schema["type"] == "string" and not isinstance(value, str):
            raise ToolError(f"{key} must be text.")
        if "enum" in schema and value not in schema["enum"]:
            raise ToolError(f"{key} has an unsupported value.")
    return args


async def execute_tool(name: str, raw_arguments: str) -> dict:
    args = parse_arguments(name, raw_arguments)
    result = TOOLS[name].execute(**args)
    if inspect.isawaitable(result):
        result = await result
    return result
