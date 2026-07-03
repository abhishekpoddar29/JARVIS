from tools.base import BaseTool, ToolResult
import random

class CustomDiceTool(BaseTool):
    name: str = "custom_dice_tool"
    description: str = "Simulates rolling a 6-sided die and returns the result."

    async def run(self, **kwargs) -> ToolResult:
        """
        Simulates the roll of a 6-sided die.
        Returns a ToolResult with the rolled number.
        """
        roll = random.randint(1, 6)
        return ToolResult(ok=True, content=f"You rolled a {roll}.", data={"roll": roll})
