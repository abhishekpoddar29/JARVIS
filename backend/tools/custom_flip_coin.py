import random
from tools.base import BaseTool, ToolResult

class CustomFlipCoinTool(BaseTool):
    name: str = "custom_flip_coin_tool"
    description: str = "Simulates a coin flip and returns 'Heads' or 'Tails'."

    async def run(self, **kwargs) -> ToolResult:
        result = random.choice(["Heads", "Tails"])
        return ToolResult(ok=True, content=result, data={"result": result})
