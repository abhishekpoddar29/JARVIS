from langchain_community.utilities import GoogleSerperAPIWrapper

from config import get_settings
from tools.base import BaseTool, ToolResult

settings = get_settings()


class WebSearchTool(BaseTool):
    name: str = "web_search_tool"
    description: str = (
        "Search the internet for current information, "
        "news, facts, research, and recent events."
    )

    async def run(self,query: str,) -> ToolResult:

        try:

            if not settings.serper_api_key:
                return ToolResult(
                    ok=False,
                    content="SERPER_API_KEY is not configured.",
                    data={},
                )

            serper = GoogleSerperAPIWrapper(
                serper_api_key=settings.serper_api_key
            )

            results = serper.results(query)

            organic_results = results.get("organic", [])[:5]

            formatted_results = []

            for item in organic_results:

                formatted_results.append(
                    {
                        "title": item.get("title", ""),
                        "link": item.get("link", ""),
                        "snippet": item.get("snippet", ""),
                    }
                )

            if not formatted_results:
                return ToolResult(
                    ok=False,
                    content=f"No search results found for '{query}'.",
                    data={
                        "query": query,
                        "results": [],
                    },
                )

            # Human-readable summary for Gemini
            summary_lines = []

            for idx, item in enumerate(formatted_results, start=1):

                summary_lines.append(
                    f"{idx}. {item['title']}\n"
                    f"{item['snippet']}\n"
                    f"Source: {item['link']}\n"
                )

            search_summary = "\n".join(summary_lines)

            return ToolResult(
                ok=True,
                content=search_summary,
                data={
                    "query": query,
                    "results": formatted_results,
                },
            )

        except Exception as exc:

            return ToolResult(
                ok=False,
                content="Web search failed.",
                data={
                    "query": query,
                    "error": str(exc),
                },
            )