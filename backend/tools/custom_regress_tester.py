from tools.base import BaseTool, ToolResult
import re

class CustomRegressTesterTool(BaseTool):
    name: str = "custom_regress_tester_tool"
    description: str = "Takes a regex pattern and a text string, returning all matches with their start/end positions. If no matches, returns a 'No match' message."

    async def run(self, pattern: str, text_string: str, **kwargs) -> ToolResult:
        matches_found = []
        for match in re.finditer(pattern, text_string):
            matches_found.append({
                "match": match.group(0),
                "start": match.start(),
                "end": match.end()
            })

        if not matches_found:
            return ToolResult(ok=True, content="No matches found for the given pattern.", data={"matches": []})
        else:
            return ToolResult(ok=True, content="Matches found.", data={"matches": matches_found})
