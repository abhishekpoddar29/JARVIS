import json
from tools.base import ToolResult
from tools.registry import registry
from tools.schemas import ToolCall, ToolExecutionResult


class ToolExecutor:
    async def execute(
        self,
        tool_call: ToolCall,
    ) -> ToolExecutionResult:

        print(
            f"[TOOL] Executing: "
            f"{tool_call.tool} "
            f"{tool_call.arguments}"
        )

        tool = registry.get(tool_call.tool)

        if not tool:
            return ToolExecutionResult(
                tool=tool_call.tool,
                success=False,
                content="Tool not found.",
                error=f"Unknown tool: {tool_call.tool}",
            )

        try:
            result = None
            
            # F16/MCP FIX: Handle custom tools and MCP tools differently
            from tools.base import BaseTool
            
            if isinstance(tool, BaseTool):
                # 1. Custom Jarvis tool (expects **kwargs, returns ToolResult)
                try:
                    result = await tool.run(**tool_call.arguments)
                except TypeError as e:
                    # Gemini hallucinated an invalid argument
                    return ToolExecutionResult(
                        tool=tool.name,
                        success=False,
                        content=f"Invalid arguments for {tool.name}: {e}",
                        error=str(e)
                    )
            else:
                # 2. Standard LangChain / MCP tool (expect 'tool_input', return string)
                if hasattr(tool, 'arun'):
                    output_str = await tool.arun(tool_input=tool_call.arguments)
                    result = ToolResult(ok=True, content=str(output_str), data={})
                elif hasattr(tool, 'run'):
                    output_str = tool.run(tool_input=tool_call.arguments)
                    result = ToolResult(ok=True, content=str(output_str), data={})
                else:
                    raise ValueError("Tool does not have a valid run or arun method")

            # Ensure we always have a ToolResult object
            if not isinstance(result, ToolResult):
                result = ToolResult(ok=True, content=str(result), data={})

            print("================================")
            print("TOOL RESULT")
            print(result.content)
            print(result.data)
            print("SUCCESS:", result.ok)
            print("CONTENT:", result.content)
            print("DATA:", result.data)
            print("ERROR:", result.error)
            print("====================================")

            return ToolExecutionResult(
                tool=tool.name,
                success=result.ok,
                content=result.content,
                data=result.data,
                error=result.error,
            )

        except Exception as exc:
            print(
                f"[TOOL ERROR] "
                f"{tool.name}: {exc}"
            )

            return ToolExecutionResult(
                tool=tool.name,
                success=False,
                content="Tool execution failed.",
                error=str(exc),
            )


tool_executor = ToolExecutor()