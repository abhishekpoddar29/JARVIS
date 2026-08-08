from tools.base import BaseTool, ToolResult
import httpx
import json

class CustomHttpRequestTool(BaseTool):
    name: str = "custom_http_request_tool"
    description: str = "Makes an HTTP GET or POST request to a specified URL. Supports headers and JSON bodies."

    async def run(self, method: str, url: str, headers: str = "{}", body: str = "{}", **kwargs) -> ToolResult:
        try:
            parsed_headers = json.loads(headers)
            parsed_body = json.loads(body) if body != "{}" else {}

            async with httpx.AsyncClient() as client:
                if method.upper() == "GET":
                    response = await client.get(url, headers=parsed_headers)
                elif method.upper() == "POST":
                    response = await client.post(url, headers=parsed_headers, json=parsed_body)
                else:
                    return ToolResult(ok=False, content=f"Unsupported HTTP method: {method}. Only GET and POST are supported.")

                response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)
                return ToolResult(ok=True, content=f"Request successful. Status: {response.status_code}", data={"status_code": response.status_code, "headers": dict(response.headers), "body": response.text})
        except json.JSONDecodeError:
            return ToolResult(ok=False, content="Invalid JSON for headers or body.")
        except httpx.RequestError as e:
            return ToolResult(ok=False, content=f"An HTTP request error occurred: {e}")
        except Exception as e:
            return ToolResult(ok=False, content=f"An unexpected error occurred: {e}")
