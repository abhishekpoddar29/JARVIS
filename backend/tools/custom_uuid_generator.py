from tools.base import BaseTool, ToolResult
import uuid

class CustomUUIDGenerator(BaseTool):
    name: str = "custom_uuid_generator"
    description: str = "Generates a random UUID."

    async def run(self, **kwargs) -> ToolResult:
        return ToolResult(ok=True, content=str(uuid.uuid4()), data={})
