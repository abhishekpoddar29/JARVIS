from tools.base import BaseTool, ToolResult
import random

class CustomCoinFlipTool(BaseTool):
    name: str = "custom_coin_flip_tool"
    description: str = "Simulates flipping a coin and returns 'Heads' or 'Tails'."

    async def run(self, **kwargs) -> ToolResult:
        result = random.choice(["Heads", "Tails"])
        return ToolResult(ok=True, content=f"The coin landed on: {result}", data={"result": result})
