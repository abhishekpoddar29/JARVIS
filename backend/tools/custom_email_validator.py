from tools.base import BaseTool, ToolResult
import re

class CustomEmailValidatorTool(BaseTool):
    name: str = "custom_email_validator_tool"
    description: str = "Validates if a given string is a valid email address."

    async def run(self, email_address: str, **kwargs) -> ToolResult:
        # Basic regex for email validation
        # This regex covers most common email formats but is not exhaustive for all edge cases.
        email_regex = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
        
        if email_regex.fullmatch(email_address):
            return ToolResult(ok=True, content=f"'{email_address}' is a valid email address.", data={"is_valid": True})
        else:
            return ToolResult(ok=True, content=f"'{email_address}' is not a valid email address.", data={"is_valid": False})
