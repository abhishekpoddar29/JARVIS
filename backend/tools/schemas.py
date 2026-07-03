from typing import Any

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolExecutionResult(BaseModel):
    tool: str
    success: bool
    content: str
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None