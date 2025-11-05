"""Session helper tools exposed via MCP."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping

from src.session import store as session_store

from .tools_read import ToolError


@dataclass(slots=True)
class _SessionToolDefinition:
    name: str
    description: str
    input_schema: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.name,
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


_SESSION_TOOLS = (
    _SessionToolDefinition(
        name="session.set_user_id",
        description="Persist user_id in the in-memory session store.",
        input_schema={
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "required": ["user_id"],
            "properties": {"user_id": {"type": "integer"}},
        },
    ),
    _SessionToolDefinition(
        name="session.get_user_id",
        description="Return the currently stored user_id, if any.",
        input_schema={
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {},
        },
    ),
    _SessionToolDefinition(
        name="session.clear_user_id",
        description="Clear the stored user_id from the session store.",
        input_schema={
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {},
        },
    ),
)


_TOOL_DEFINITIONS = {tool.name: tool for tool in _SESSION_TOOLS}


def get_tool_definitions() -> List[Dict[str, Any]]:
    return [tool.as_dict() for tool in _SESSION_TOOLS]


def has_tool(name: str) -> bool:
    return name in _TOOL_DEFINITIONS


async def call_tool(name: str, arguments: Mapping[str, Any]) -> Any:
    if name == "session.set_user_id":
        return _call_set_user_id(arguments)
    if name == "session.get_user_id":
        return _call_get_user_id()
    if name == "session.clear_user_id":
        return _call_clear_user_id()
    raise ToolError("InvalidParams", f"Unknown tool '{name}'")


def _call_set_user_id(arguments: Mapping[str, Any]) -> Dict[str, Any]:
    if "user_id" not in arguments:
        raise ToolError("InvalidParams", "user_id is required")
    try:
        session_store.set_user_id(arguments["user_id"])
    except ValueError as exc:
        raise ToolError("InvalidParams", str(exc)) from exc
    return {"ok": True, "user_id": session_store.get_user_id()}


def _call_get_user_id() -> Dict[str, Any]:
    return {"user_id": session_store.get_user_id()}


def _call_clear_user_id() -> Dict[str, Any]:
    session_store.clear_user_id()
    return {"ok": True}


__all__ = [
    "get_tool_definitions",
    "has_tool",
    "call_tool",
]
