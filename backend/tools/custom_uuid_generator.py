import uuid
from pydantic import BaseModel, Field
from tools.base import BaseTool

class CustomUuidGeneratorToolSchema(BaseModel):
    """Schema for CustomUuidGeneratorTool."""
    pass

class CustomUuidGeneratorTool(BaseTool):
    name: str = "custom_uuid_generator_tool"
    description: str = "Generates a random UUID and returns it."
    args_schema: type[BaseModel] = CustomUuidGeneratorToolSchema

    def _run(self) -> str:
        """Generates and returns a random UUID."""
        return str(uuid.uuid4())