from tools.base import BaseTool, ToolResult
from urllib.parse import urlparse

class CustomUrlDecomposerTool(BaseTool):
    name: str = "custom_url_decomposer"
    description: str = "Decomposes a URL string into its components (scheme, netloc, path, params, query, fragment)."

    async def run(self, url: str, **kwargs) -> ToolResult:
        parsed_url = urlparse(url)
        components = {
            "scheme": parsed_url.scheme,
            "netloc": parsed_url.netloc,
            "path": parsed_url.path,
            "params": parsed_url.params,
            "query": parsed_url.query,
            "fragment": parsed_url.fragment,
        }
        return ToolResult(ok=True, content="URL decomposed successfully.", data=components)