from __future__ import annotations

import asyncio
import contextvars
import json
import re
import importlib.util
from pathlib import Path
from typing import Callable, Awaitable

from agents.gemini_client import GeminiClientError, generate_reply_with_history
from agents.graph_state import JarvisState
from agents.personality import PersonalityEngine
from config import get_settings
from agents.prompts.prompts import TOOL_RESPONSE_PROMPT, TOOL_ROUTER_PROMPT
from memory.semantic import semantic_memory 
from tools.parser import try_parse_tool_call
from tools.tool_executer import tool_executor
from langgraph.types import interrupt
from tools.registry import registry
from tools.base import BaseTool

settings = get_settings()

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
personality = PersonalityEngine(
    config_path=_PROJECT_ROOT / "personality.yaml",
    templates_dir=_PROJECT_ROOT / "agents" / "prompts",
)

# ── Orb broadcast context var ──
orb_emitter: contextvars.ContextVar[
    Callable[[str], Awaitable[None]] | None
] = contextvars.ContextVar("orb_emitter", default=None)

# F14: Progress broadcast context var
progress_emitter: contextvars.ContextVar[
    Callable[[str], Awaitable[None]] | None
] = contextvars.ContextVar("progress_emitter", default=None)

async def _emit_orb(state: str) -> None:
    emit = orb_emitter.get()
    if emit:
        try:
            await emit(state)
        except Exception:
            pass


# ── New F9 Node: Memory Retrieval ─────────────────────────────────────────────
async def memory_retrieval_node(state: JarvisState) -> dict:
    """
    Queries ChromaDB for long-term memories relevant to the user's message.
    Runs in a thread because ChromaDB calls are synchronous.
    """
    user_message = state.get("user_message", "")
    if not user_message:
        return {"memory_context": ""}

    loop = asyncio.get_running_loop()
    
    def _query_db():
        return semantic_memory.query_memories(query_text=user_message, n_results=3)
        
    memories = await loop.run_in_executor(None, _query_db)
    
    if not memories:
        return {"memory_context": ""}

    context_lines = []
    for mem in memories:
        context_lines.append(f"- {mem.content} (Type: {mem.type.value}, Source: {mem.source_date})")
        
    memory_context = "\n".join(context_lines)
    print(f"[MEMORY F9] Retrieved context:\n{memory_context}")
    
    return {"memory_context": memory_context}


# ── Nodes ──────────────────────────────────────────────────────────────────────

async def personality_node(state: JarvisState) -> dict:
    personality_prompt = personality.build_system_prompt(
        user_message=state["user_message"],
        memory_facts=state.get("memory_facts") or [],
    )
    
    system_prompt = personality_prompt + "\n\n" + TOOL_ROUTER_PROMPT
    
    memory_context = state.get("memory_context", "")
    if memory_context:
        system_prompt += f"\n\nLONG-TERM MEMORY CONTEXT:\nYou remember the following facts about the user. Use them naturally if relevant:\n{memory_context}"

    # FIX: Clear final_reply so the graph doesn't skip tools from the previous turn!
    return {"system_prompt": system_prompt, "final_reply": None}    


async def gemini_node(state: JarvisState) -> dict:
    await _emit_orb("processing")

    loop = asyncio.get_running_loop()
    
    history = state.get("history", [])
    image_data = state.get("image_data")
    
    if image_data:
        match = re.match(r"data:(image/[\w+]+);base64,(.*)", image_data)
        if match:
            mime_type = match.group(1)
            b64_data = match.group(2)
            if history and history[-1].get("role") == "user":
                last_user_text = history[-1].get("content", state.get("user_message", ""))
                history[-1] = {
                    "role": "user",
                    "parts": [
                        {"text": last_user_text},
                        {"inline_data": {"mime_type": mime_type, "data": b64_data}}
                    ]
                }
            else:
                history.append({
                    "role": "user",
                    "parts": [
                        {"text": state.get("user_message", "")},
                        {"inline_data": {"mime_type": mime_type, "data": b64_data}}
                    ]
                })

    try:
        reply = await loop.run_in_executor(
            None,
            lambda: generate_reply_with_history(
                api_key=settings.gemini_api_key,
                model=settings.gemini_model,
                messages=history,
                system_instruction=state["system_prompt"],
                max_output_tokens=4000,
            ),
        )
    except GeminiClientError as exc:
        await _emit_orb("error")
        return {
            "gemini_reply": f"Gemini request failed. Details: {exc}",
            "tool_call": None,
            "final_reply": f"Gemini request failed. Details: {exc}",
        }

    tool_call_obj = try_parse_tool_call(reply.text)
    
    # FIX: Convert ToolCall object to a dictionary so LangGraph can serialize it safely
    tool_call_dict = tool_call_obj.model_dump() if tool_call_obj else None

    print("=" * 80)
    print("RAW GEMINI RESPONSE:")
    print(reply.text[:300])
    print(f"TOOL CALL: {tool_call_dict}")
    print("=" * 80)
    
    return {"gemini_reply": reply.text, "tool_call": tool_call_dict}


def route_after_gemini(state: JarvisState) -> str:
    if state.get("final_reply"):
        return "done"
    return "tool" if state.get("tool_call") else "done"


async def tool_node(state: JarvisState) -> dict:
    tool_call_dict = state.get("tool_call")
    tool_name = tool_call_dict.get("tool") if tool_call_dict else "tool"
    
    if tool_name == "code_execution_tool":
        await _emit_orb("processing_code")
    else:
        await _emit_orb("processing")

    from tools.schemas import ToolCall
    tool_call_obj = ToolCall(**tool_call_dict) if tool_call_dict else None

    tool_result = await tool_executor.execute(tool_call_obj)
    
    # F13/F17: Human-in-the-Loop Approval Gate
    if tool_result.data and tool_result.data.get("action_type"):
        action_type = tool_result.data["action_type"]
        draft = tool_result.data["draft"]
        
        approval_payload = {
            "action_type": action_type,
            "draft": draft,
            "tool_name": tool_name
        }
        resume_value = interrupt(approval_payload)
        
        if resume_value.get("approved"):
            new_args = {**tool_call_obj.arguments, "mode": "execute", "draft": draft}
            
            if hasattr(tool_call_obj, "model_copy"):
                execute_call = tool_call_obj.model_copy(update={"arguments": new_args})
            else:
                import copy
                execute_call = copy.copy(tool_call_obj)
                execute_call.arguments = new_args
                
            tool_result = await tool_executor.execute(execute_call)
            
            # F17: Controlled Self-Modification Execution
            if tool_name == "self_modify_tool" and tool_result.ok:
                filename = draft.get("filename", "").replace(".py", "")
                if filename:
                    try:
                        # 1. Dynamically load the new python file
                        spec = importlib.util.spec_from_file_location(filename, f"tools/{filename}.py")
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)
                        
                        # 2. Find the Tool class and register it
                        loaded = False
                        for attr_name in dir(module):
                            attr = getattr(module, attr_name)
                            if isinstance(attr, type) and issubclass(attr, BaseTool) and attr != BaseTool:
                                registry.register(attr())
                                loaded = True
                        
                        if loaded:
                            tool_result.content = f"Tool {filename}.py successfully loaded and registered! You can now use it."
                            tool_result.success = True
                        else:
                            tool_result.content = "File loaded, but no valid BaseTool class found."
                            tool_result.success = False
                    except Exception as e:
                        tool_result.content = f"Failed to dynamically load tool: {e}"
                        tool_result.success = False
        else:
            tool_result.content = "User rejected the action."
            tool_result.success = False
            tool_result.data = {}

    # FIX: Convert Pydantic model to dictionary so LangGraph MemorySaver can serialize it!
    return {
        "tool_result": tool_result.model_dump() if hasattr(tool_result, "model_dump") else tool_result,
        "tool_data": tool_result.data
    }

async def tool_response_node(state: JarvisState) -> dict:
    await _emit_orb("speaking")

    # FIX: Handle tool_result as either a dict or an object
    tool_result_dict = state["tool_result"]
    if isinstance(tool_result_dict, dict):
        tool_content = tool_result_dict.get("content", "")
        tool_data = tool_result_dict.get("data", {})
    else:
        tool_content = tool_result_dict.content
        tool_data = tool_result_dict.data

    loop = asyncio.get_running_loop()
    
    reply = await loop.run_in_executor(
        None,
        lambda: generate_reply_with_history(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Original question:\n{state['user_message']}\n\n"
                        f"Tool result:\n{tool_content}\n\n"
                        f"Tool data:\n{json.dumps(tool_data, indent=2)}"
                    ),
                }
            ],
            system_instruction=TOOL_RESPONSE_PROMPT,
            max_output_tokens=5000,
        ),
    )
    
    tool_call = try_parse_tool_call(reply.text)
    if tool_call:
        print("[CHAIN] Jarvis decided to call another tool!")
        return {"tool_call": tool_call.model_dump(), "final_reply": None}
        
    return {"final_reply": reply.text, "tool_call": None}


async def finalize_node(state: JarvisState) -> dict:
    await _emit_orb("speaking")
    if state.get("final_reply"):
        # FIX: Clear tool state to prevent checkpoint bloat/corruption on next turn
        return {"tool_call": None, "tool_result": None}
    return {"final_reply": state["gemini_reply"], "tool_call": None, "tool_result": None}