from __future__ import annotations
import os
import logging
from typing import Any

from tools.base import BaseTool, ToolResult
from tools.git_manager import git_manager

logger = logging.getLogger(__name__)

class SelfModifyTool(BaseTool):
    name: str = "self_modify_tool"
    description: str = (
        "Use this tool to write a NEW Python tool and add it to your own codebase. "
        "Provide the filename (must end with .py) and the exact Python code. "
        "Requires user approval to activate."
    )

    async def run(self, filename: str, code: str) -> ToolResult:
        # F17 Safety: Only allow editing the tools directory
        if not filename.endswith(".py"):
            return ToolResult(ok=False, content="Filename must end with .py")
        
        # Enforce a safe naming convention
        if not filename.startswith("custom_"):
            return ToolResult(ok=False, content="Filename must start with 'custom_' (e.g., custom_github_tool.py)")

        safe_path = os.path.abspath(os.path.join("tools", filename))
        if not safe_path.startswith(os.path.abspath("tools")):
            return ToolResult(ok=False, content="Access denied. Can only write to tools/ directory.")

        try:
            # 1. Create Git Branch
            branch_name = f"feature/{filename.replace('.py', '')}"
            if not git_manager.create_branch(branch_name):
                raise Exception("Failed to create Git branch.")

            # 2. Write the file to disk
            with open(safe_path, 'w', encoding='utf-8') as f:
                f.write(code)
                
            # 3. Commit to the branch
            git_manager.commit_file(safe_path, f"Feat: Add {filename}")
            
            return ToolResult(
                ok=True,
                content=f"Code written to {filename} on branch {branch_name}. Awaiting user approval to load.",
                data={
                    "action_type": "self_modify",
                    "branch_name": branch_name,
                    "filename": filename,
                    "code": code
                }
            )
        except Exception as e:
            return ToolResult(ok=False, content=f"Failed to write file: {e}")

# Register singleton
self_modify_tool = SelfModifyTool()