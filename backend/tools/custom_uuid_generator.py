import uuid
from tools.base import BaseTool

class CustomUUIDGeneratorTool(BaseTool):
    name = "custom_uuid_generator_tool"
    description = "Generates a random UUID (Universally Unique Identifier)."

    def __init__(self):
        super().__init__()

    def run(self):
        return str(uuid.uuid4())