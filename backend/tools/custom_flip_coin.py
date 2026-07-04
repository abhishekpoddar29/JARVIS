import random
from pydantic import Field
from tools.base import BaseTool, ToolResult

class CustomFlipCoinTool(BaseTool):
    name: str = "custom_flip_coin_tool"
    # An updated description helps the LLM understand it can now accept inputs
    description: str = (
        "Simulates flipping one or more coins. Returns the results, "
        "along with a summary count of 'Heads' and 'Tails'."
    )

    async def run(self, flips: int = 1, **kwargs) -> ToolResult:
        # Enforce a reasonable boundary for testing
        if flips < 1:
            return ToolResult(ok=False, content="You must flip at least 1 coin.", data={})
        if flips > 100:
            return ToolResult(ok=False, content="To avoid spam, you can only flip up to 100 coins at once.", data={})

        # Perform the simulation
        results = [random.choice(["Heads", "Tails"]) for _ in range(flips)]
        heads_count = results.count("Heads")
        tails_count = results.count("Tails")

        # Create a clean readable string for the chat bubble
        if flips == 1:
            content = f"The coin landed on: **{results[0]}**."
        else:
            content = f"Flipped {flips} coins: **{heads_count} Heads** and **{tails_count} Tails**."

        # Return rich structured data for your UI to parse or inspect
        return ToolResult(
            ok=True,
            content=content,
            data={
                "flips_requested": flips,
                "outcomes": results,
                "summary": {
                    "heads": heads_count,
                    "tails": tails_count
                }
            }
        )