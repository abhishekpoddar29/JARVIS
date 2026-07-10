from tools.base import BaseTool, ToolResult
import random

class CustomDieRollTool(BaseTool):
    name: str = "custom_die_roll_tool"
    description: str = "Simulates rolling a 6-sided die and returns the result (1-6)."

    async def run(self, **kwargs) -> ToolResult:
        roll = random.randint(1, 6)
        return ToolResult(ok=True, content=f"You rolled a {roll}.", data={"roll": roll})
