from tools.base import BaseTool, ToolResult
import random

class CustomTechJokesTool(BaseTool):
    name: str = "custom_tech_jokes_tool"
    description: str = "Tells a random tech joke."

    async def run(self, **kwargs) -> ToolResult:
        jokes = [
            "Why do programmers prefer dark mode? Because light attracts bugs!",
            "There are 10 types of people in the world: those who understand binary, and those who don't.",
            "Why was the JavaScript developer sad? Because he didn't Node how to Express himself.",
            "Debugging: Removing the needles from the haystack, one by one.",
            "My computer's favorite type of music is dubstep. It loves the drop."
        ]
        return ToolResult(ok=True, content=random.choice(jokes), data={})