from tools.base import BaseTool, ToolResult
import random

class CustomFlipCoin(BaseTool):
    name: str = "custom_flip_coin"
    description: str = "Flips a coin and returns 'Heads' or 'Tails'."

    async def run(self, **kwargs) -> ToolResult:
        result = random.choice(['Heads', 'Tails'])
        return ToolResult(ok=True, content=result, data={"result": result})
