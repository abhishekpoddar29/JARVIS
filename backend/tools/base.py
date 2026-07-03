from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ToolRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ToolResult(BaseModel):
    ok: bool
    content: str
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class BaseTool(BaseModel):
    name: str
    description: str
    risk: ToolRisk = ToolRisk.LOW
    requires_approval: bool = False

    async def run(self, **kwargs: Any) -> ToolResult:
        raise NotImplementedError
