import os
import logging
from contextlib import AsyncExitStack
from config import get_settings
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_mcp_adapters.tools import load_mcp_tools

logger = logging.getLogger(__name__)
settings = get_settings()

MCP_SERVERS = {
    "filesystem": StdioServerParameters(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", os.path.abspath("sandbox")],
    ),
}

class MCPManager:
    def __init__(self):
        self.exit_stack = AsyncExitStack()
        self.tools = []

    async def initialize(self) -> list:
        if not settings.enable_mcp_filesystem:
            logger.info("[MCP] Filesystem MCP disabled by ENABLE_MCP_FILESYSTEM.")
            return []

        os.makedirs("sandbox", exist_ok=True)
        for name, params in MCP_SERVERS.items():
            try:
                stdio_transport = await self.exit_stack.enter_async_context(stdio_client(params))
                read, write = stdio_transport
                session = await self.exit_stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                mcp_tools = await load_mcp_tools(session)
                for tool in mcp_tools:
                    tool.name = f"mcp_{name}_{tool.name}"
                self.tools.extend(mcp_tools)
                logger.info(f"[MCP] Loaded {len(mcp_tools)} tools from '{name}' server.")
            except Exception as e:
                logger.error(f"[MCP] Failed to connect to '{name}' server: {e}")
        return self.tools

    async def close(self):
        await self.exit_stack.aclose()
