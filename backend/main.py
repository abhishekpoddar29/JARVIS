import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from typing import Optional

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from langgraph.types import Command  # ← F13: Required for resuming paused graphs

from agents.graph import jarvis_graph
from config import get_settings
from memory.session_storage import session_store
from memory.export_manager import export_session, archive_to_disk, wipe_session
from memory.semantic import semantic_memory       
from memory.distiller import distiller            
from memory.chromadb_schemas import MemoryType, SensitivityLevel 
from auth.google_oauth import google_oauth       
from auth.token_store import token_store         
from tools.registry import registry
from tools.weather_tool import WeatherTool
from tools.news_tool import NewsTool
from tools.datetime_tool import DateTimeTool
from tools.location_tool import LocationTool
from tools.web_search_tool import WebSearchTool
from tools.system_info_tool import SystemInfoTool
from tools.task_memory_tool import TaskMemoryTool
from tools.research_tool import ResearchTool
from tools.code_execution_tool import CodeExecutionTool
from tools.calendar_tool import CalendarTool
from tools.email_tool import EmailTool
from agents.graph_nodes import orb_emitter, progress_emitter, tool_event_emitter
from tools.web_scraper_tool import WebScraperTool
from tools.mcp_bridge import MCPManager
from tools.self_modify_tool import SelfModifyTool
from tools.engineering_team_tool import EngineeringTeamTool
from tools.financial_analyst_tool import FinancialAnalystTool

from tracking.routes import router as tracking_router
from tracking.runtime import tracking_store
from tools.location_share_tool import LocationShareTool
from tracking.bridge import TrackingEventBridge
from tracking.runtime import tracking_service

from tools.music_player_tool import MusicPlayerTool
from music.routes import router as music_router
from music.runtime import music_store
from api.voice_routes import router as voice_router
from voice.approval_intent import VoiceApprovalDecision, parse_voice_approval_intent
from security.app_auth import require_app_auth, require_websocket_auth
from security.uploads import artifact_download_headers, store_upload


from fastapi import UploadFile, File


settings = get_settings()

# Ensure exports directory exists
EXPORTS_DIR = Path(__file__).resolve().parent / "exports"
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

# Ensure uploads directory exists
UPLOADS_DIR = Path(__file__).resolve().parent / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await session_store.connect()
    await tracking_store.connect()
    await music_store.connect()
    
    registry.register(DateTimeTool())
    registry.register(LocationTool())
    registry.register(WeatherTool())
    registry.register(NewsTool())
    registry.register(WebSearchTool())
    registry.register(SystemInfoTool())
    registry.register(TaskMemoryTool())
    registry.register(ResearchTool())
    if settings.enable_code_execution:
        registry.register(CodeExecutionTool())
    registry.register(CalendarTool())
    registry.register(EmailTool())
    if settings.enable_web_scraper:
        registry.register(WebScraperTool())
    if settings.enable_self_modify:
        registry.register(SelfModifyTool())
    registry.register(EngineeringTeamTool())
    registry.register(FinancialAnalystTool())
    
    registry.register(LocationShareTool())
    registry.register(MusicPlayerTool())
    
    mcp_manager = MCPManager()
    mcp_tools = await mcp_manager.initialize()
    for tool in mcp_tools:
        registry.register(tool)

    print("Registered tools:", [tool.name for tool in registry.list_tools()])

    yield

    await mcp_manager.close()
    await session_store.disconnect()
    await tracking_store.disconnect()
    await music_store.disconnect()


app = FastAPI(title=settings.app_name, version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(tracking_router)
app.include_router(music_router, dependencies=[Depends(require_app_auth)])
app.include_router(voice_router, dependencies=[Depends(require_app_auth)])


# ── Request Models ──────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(min_length=1)

class ChatResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: str
    state: str
    provider: str = "stub"

class MemoryUpdateRequest(BaseModel):
    content: Optional[str] = None
    pinned: Optional[bool] = None
    type: Optional[str] = None
    sensitivity: Optional[str] = None


# ── Core API Endpoints ──────────────────────────────────────────────────────────

@app.get("/api/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.environment,
        "time": datetime.now(UTC).isoformat(),
    }

@app.get("/api/session")
async def get_session(_auth: None = Depends(require_app_auth)) -> dict:
    messages = await session_store.load_session()
    return {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "message_count": len(messages),
        "messages": messages,
    }

@app.post("/api/export")
async def export_conversation(_auth: None = Depends(require_app_auth)) -> PlainTextResponse:
    markdown, payload, timestamp_str = await export_session()
    await archive_to_disk(payload, timestamp_str)
    await wipe_session()

    filename = f"jarvis-{timestamp_str}.md"
    return PlainTextResponse(
        content=markdown,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Export-Filename": filename,
        },
    )

@app.get("/api/files/{filename}")
async def download_file(filename: str, _auth: None = Depends(require_app_auth)):
    safe_path = (EXPORTS_DIR / filename).resolve()
    if not safe_path.is_file() or EXPORTS_DIR not in safe_path.parents:
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        safe_path,
        media_type="application/octet-stream",
        filename=safe_path.name,
        headers=artifact_download_headers(safe_path.name),
    )


# ── F9: Long-Term Semantic Memory Endpoints ─────────────────────────────────────

@app.get("/api/memory")
async def get_all_memories(_auth: None = Depends(require_app_auth)):
    memories = await asyncio.to_thread(semantic_memory.get_all_memories)
    return {
        "count": len(memories),
        "memories": [m.model_dump(mode="json") for m in memories]
    }

@app.post("/api/memory/distill")
async def trigger_distillation(_auth: None = Depends(require_app_auth)):
    result = await distiller.run()
    return result

@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    _auth: None = Depends(require_app_auth),
):
    """F8/F14: Receive a file from the UI and save it to the backend/uploads/ folder."""
    try:
        stored = await store_upload(file, UPLOADS_DIR)
        return {
            "upload_id": stored.upload_id,
            "filename": stored.stored_filename,
            "original_filename": stored.original_filename,
            "content_type": stored.content_type,
            "size_bytes": stored.size_bytes,
            "message": "File uploaded successfully",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/memory/{memory_id}")
async def delete_memory(memory_id: str, _auth: None = Depends(require_app_auth)):
    existing = await asyncio.to_thread(semantic_memory.get_memory_by_id, memory_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Memory not found")
        
    await asyncio.to_thread(semantic_memory.delete_memory, memory_id)
    return {"status": "deleted", "id": memory_id}

@app.patch("/api/memory/{memory_id}")
async def update_memory(
    memory_id: str,
    payload: MemoryUpdateRequest,
    _auth: None = Depends(require_app_auth),
):
    existing = await asyncio.to_thread(semantic_memory.get_memory_by_id, memory_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Memory not found")
        
    if payload.content is not None:
        existing.content = payload.content
    if payload.pinned is not None:
        existing.pinned = payload.pinned
    if payload.type is not None:
        existing.type = MemoryType(payload.type)
    if payload.sensitivity is not None:
        existing.sensitivity = SensitivityLevel(payload.sensitivity)
        
    existing.updated_at = datetime.now(UTC)
    
    await asyncio.to_thread(semantic_memory.update_memory, existing)
    return existing.model_dump(mode="json")


# ── F13: Google OAuth Endpoints ─────────────────────────────────────────────────

@app.get("/api/auth/google")
async def auth_google():
    auth_url = google_oauth.get_auth_url()
    return RedirectResponse(url=auth_url)

@app.get("/api/auth/google/callback")
async def auth_google_callback(code: str):
    try:
        token_data = google_oauth.exchange_code_for_token(code)
        token_store.save_token("abhi", token_data)
        return {"status": "success", "message": "Google account connected successfully! You can close this tab."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to authenticate with Google: {str(e)}")


# ── WebSocket Helpers ───────────────────────────────────────────────────────────

def _extract_interrupt_value(result: dict) -> dict | None:
    interrupts = result.get("__interrupt__") or []
    if not interrupts:
        return None

    value = getattr(interrupts[0], "value", interrupts[0])
    return value if isinstance(value, dict) else None


async def _send_interrupt_to_ui(
    websocket: WebSocket,
    result: dict,
    *,
    voice_response: bool = False,
) -> tuple[bool, str | None]:
    """Send a graph interrupt to the UI and optionally continue the voice turn."""
    interrupt_data = _extract_interrupt_value(result)
    if not interrupt_data:
        return False, None

    voice_prompt = (
        "This action requires your approval. "
        "Say approve, reject, or cancel."
    )

    if interrupt_data.get("kind") == "ENGINEERING_HIL_CARD":
        card = interrupt_data.get("card")
        if not isinstance(card, dict):
            raise ValueError("Engineering HIL interrupt is missing its card payload.")

        card_id = str(card.get("card_id", "")).strip()
        if not card_id:
            raise ValueError("Engineering HIL card is missing card_id.")

        await websocket.send_json({
            "type": "hil_card",
            "id": str(uuid4()),
            "created_at": datetime.now(UTC).isoformat(),
            "card": card,
            "voice_response": voice_response,
            "voice_prompt": voice_prompt,
        })
        return True, card_id

    if interrupt_data.get("action_type"):
        await websocket.send_json({
            "type": "approval_request",
            "id": str(uuid4()),
            "created_at": datetime.now(UTC).isoformat(),
            "action_type": interrupt_data.get("action_type"),
            "draft": interrupt_data.get("draft"),
            "tool_name": interrupt_data.get("tool_name"),
            "voice_response": voice_response,
            "voice_prompt": voice_prompt,
        })
        return True, None

    raise ValueError("Unsupported graph interrupt payload.")

def _tool_result_field(tool_result, field: str, default=None):
    if isinstance(tool_result, dict):
        return tool_result.get(field, default)
    return getattr(tool_result, field, default)


async def _send_completed_graph_result(
    websocket: WebSocket,
    result: dict,
    *,
    fallback_message: str = "",
    voice_response: bool = False,
) -> None:
    tool_result = result.get("tool_result")

    if tool_result:
        tool_name = _tool_result_field(tool_result, "tool", "tool")
        tool_content = str(_tool_result_field(tool_result, "content", ""))
        tool_success = bool(
            _tool_result_field(
                tool_result,
                "success",
                _tool_result_field(tool_result, "ok", False),
            )
        )
        tool_data = _tool_result_field(tool_result, "data", {}) or {}

        await session_store.append_message(
            role="tool",
            content=tool_content,
            tool_results=[{
                "tool": tool_name,
                "success": tool_success,
                "data": tool_data,
            }],
        )

    assistant_message = result.get("final_reply") or fallback_message

    ws_payload = {
        "type": "message",
        "id": str(uuid4()),
        "role": "assistant",
        "content": assistant_message,
        "created_at": datetime.now(UTC).isoformat(),
        "provider": "gemini",
        "voice_response": voice_response,
    }

    tool_data = result.get("tool_data") or {}
    if isinstance(tool_data, dict) and tool_data.get("code_result"):
        ws_payload["code_result"] = tool_data["code_result"]


    if (
        isinstance(tool_data, dict)
        and isinstance(tool_data.get("music_command"), dict)
        and not tool_data.get("music_command_emitted")
    ):
        # Backward-compatible fallback. The parallel orchestrator now emits music
        # commands immediately while sibling tools are still running. If no live
        # tool-event emitter was available, send the command here as before.
        await websocket.send_json({
            "type": "music_command",
            **tool_data["music_command"],
        })

    await websocket.send_json(ws_payload)
    await session_store.append_message(
        role="assistant",
        content=assistant_message,
    )


# ── WebSocket Endpoint ──────────────────────────────────────────────────────────

@app.websocket("/ws/chat")
async def chat_socket(websocket: WebSocket) -> None:
    if not await require_websocket_auth(websocket):
        await websocket.close(code=4401)
        return

    await websocket.accept()

    tracking_bridge = await TrackingEventBridge.start(websocket)

    # The same thread ID must be reused while a graph is paused.
    active_thread_id: str | None = None
    active_hil_card_id: str | None = None
    active_thread_voice = False

    async def _invoke_with_ui_context(graph_input, thread_config: dict) -> dict:
        async def _heartbeat() -> None:
            while True:
                await asyncio.sleep(8)
                try:
                    await websocket.send_json({"type": "heartbeat"})
                except Exception:
                    break

        async def _emit_orb(state: str) -> None:
            await websocket.send_json({
                "type": "orb_state",
                "orb_state": state,
            })

        async def _emit_progress(message: str) -> None:
            await websocket.send_json({
                "type": "research_progress",
                "message": message,
            })

        async def _emit_tool_event(event: dict) -> None:
            # Tool events are already safe, frontend-facing payloads. This allows
            # fast actions such as music playback to reach React immediately while
            # slower parallel tools continue running.
            await websocket.send_json(event)

        heartbeat = asyncio.create_task(_heartbeat())
        orb_token = orb_emitter.set(_emit_orb)
        progress_token = progress_emitter.set(_emit_progress)
        tool_event_token = tool_event_emitter.set(_emit_tool_event)

        try:
            return await jarvis_graph.ainvoke(
                graph_input,
                config=thread_config,
            )
        finally:
            orb_emitter.reset(orb_token)
            progress_emitter.reset(progress_token)
            tool_event_emitter.reset(tool_event_token)
            heartbeat.cancel()
            await tracking_bridge.close()

    try:
        await websocket.send_json({
            "type": "status",
            "state": "idle",
            "message": "Jarvis socket connected.",
        })

        while True:
            payload = await websocket.receive_json()
            payload_type = payload.get("type")
            is_voice_input = payload.get("source") == "voice"

            # When the graph is paused, a spoken approval command resumes the
            # existing thread instead of starting a second Jarvis request.
            if active_thread_id and not payload_type:
                pending_message = str(payload.get("message", "")).strip()
                if not is_voice_input:
                    await websocket.send_json({
                        "type": "error",
                        "message": "An approval is pending. Use the approval controls first.",
                    })
                    continue

                if pending_message:
                    await session_store.append_message(
                        role="user",
                        content=pending_message,
                    )

                decision = parse_voice_approval_intent(pending_message)
                if decision is None:
                    guidance = "Please say approve, reject, or cancel."
                    await websocket.send_json({
                        "type": "message",
                        "id": str(uuid4()),
                        "role": "assistant",
                        "content": guidance,
                        "created_at": datetime.now(UTC).isoformat(),
                        "provider": "voice-control",
                        "voice_response": True,
                    })
                    await session_store.append_message(
                        role="assistant",
                        content=guidance,
                    )
                    await websocket.send_json({"type": "status", "state": "idle"})
                    continue

                if active_hil_card_id:
                    payload_type = "hil_response"
                    payload = {
                        "type": "hil_response",
                        "card_id": active_hil_card_id,
                        "decision": decision.value,
                        "response": {},
                    }
                else:
                    payload_type = "approval_response"
                    payload = {
                        "type": "approval_response",
                        "approved": decision is VoiceApprovalDecision.APPROVE,
                    }

            if payload_type == "music_state":
                try:
                    await music_store.set_client_state(
                        payload.get("state") or {}
                    )
                except Exception as exc:
                    await websocket.send_json({
                        "type": "error",
                        "message": f"Invalid music state: {exc}",
                    })
                continue

            if payload_type == "tracking_stop":
                session_id = str(payload.get("session_id", "")).strip()
                if not session_id:
                    await websocket.send_json({
                        "type": "error",
                        "message": "tracking_stop requires session_id.",
                    })
                    continue

                stopped = await tracking_service.stop_session(session_id)
                if stopped is None:
                    await websocket.send_json({
                        "type": "error",
                        "message": "Tracking session was not found or expired.",
                    })
                    continue

                # TrackingService publishes REQUESTER_STOPPED through tracking_hub.
                # TrackingEventBridge forwards that tracking_status event to this UI.
                continue
            # Existing action approvals and Engineering Team HIL responses
            # both resume the paused Jarvis graph.
            if payload_type in {"approval_response", "hil_response"}:
                if not active_thread_id:
                    await websocket.send_json({
                        "type": "error",
                        "message": "No active approval request.",
                    })
                    continue

                if payload_type == "hil_response":
                    card_id = str(payload.get("card_id", "")).strip()
                    if not card_id:
                        await websocket.send_json({
                            "type": "error",
                            "message": "HIL response is missing card_id.",
                        })
                        continue

                    if active_hil_card_id and card_id != active_hil_card_id:
                        await websocket.send_json({
                            "type": "error",
                            "message": "This HIL card is no longer active.",
                        })
                        continue

                    decision = str(payload.get("decision", "")).upper()
                    if decision not in {"APPROVE", "REJECT", "CANCEL"}:
                        await websocket.send_json({
                            "type": "error",
                            "message": "Invalid HIL decision.",
                        })
                        continue

                    response = payload.get("response", {})
                    if not isinstance(response, dict):
                        await websocket.send_json({
                            "type": "error",
                            "message": "HIL response fields must be an object.",
                        })
                        continue

                    resume_payload = {
                        "card_id": card_id,
                        "decision": decision,
                        "response": response,
                    }

                else:
                    resume_payload = {
                        "approved": bool(payload.get("approved", False)),
                    }
                    if payload.get("edited_code"):
                        resume_payload["edited_code"] = payload["edited_code"]

                thread_config = {
                    "configurable": {
                        "thread_id": active_thread_id,
                    }
                }

                await websocket.send_json({
                    "type": "status",
                    "state": "processing",
                })

                try:
                    result = await _invoke_with_ui_context(
                        Command(resume=resume_payload),
                        thread_config,
                    )

                    interrupted, card_id = await _send_interrupt_to_ui(
                        websocket,
                        result,
                        voice_response=active_thread_voice,
                    )

                    if interrupted:
                        active_hil_card_id = card_id
                        await websocket.send_json({
                            "type": "status",
                            "state": "idle",
                        })
                        continue

                    active_thread_id = None
                    active_hil_card_id = None

                    await _send_completed_graph_result(
                        websocket,
                        result,
                        fallback_message="Action completed.",
                        voice_response=active_thread_voice,
                    )
                    active_thread_voice = False

                except Exception as exc:
                    error_msg = f"Jarvis encountered an error resuming: {exc}"
                    print(f"ERROR: {error_msg}")
                    await websocket.send_json({
                        "type": "message",
                        "id": str(uuid4()),
                        "role": "assistant",
                        "content": error_msg,
                        "created_at": datetime.now(UTC).isoformat(),
                        "provider": "fallback",
                        "voice_response": active_thread_voice,
                    })
                    await session_store.append_message(
                        role="assistant",
                        content=error_msg,
                    )
                    active_thread_id = None
                    active_hil_card_id = None
                    active_thread_voice = False

                await websocket.send_json({
                    "type": "status",
                    "state": "idle",
                })
                continue

            # Normal Chat Flow
            message = str(payload.get("message", "")).strip()
            image_data = payload.get("image")
            if image_data == "":
                image_data = None

            if not message and not image_data:
                await websocket.send_json({
                    "type": "error",
                    "message": "Message cannot be empty.",
                })
                continue

            attachment_metadata = (
                {"image": image_data}
                if image_data
                else None
            )
            await session_store.append_message(
                role="user",
                content=message,
                attachment_metadata=attachment_metadata,
            )

            if not settings.gemini_api_key:
                fallback = (
                    "Gemini API key is not configured. "
                    "Please set GEMINI_API_KEY in backend/.env and restart."
                )
                await websocket.send_json({
                    "type": "status",
                    "state": "error",
                })
                await websocket.send_json({
                    "type": "message",
                    "id": str(uuid4()),
                    "role": "assistant",
                    "content": fallback,
                    "created_at": datetime.now(UTC).isoformat(),
                    "provider": "fallback",
                    "voice_response": is_voice_input,
                })
                await session_store.append_message(
                    role="assistant",
                    content=fallback,
                )
                await websocket.send_json({
                    "type": "status",
                    "state": "idle",
                })
                continue

            session_messages = await session_store.load_session()
            history: list[dict[str, str]] = []

            for stored_message in session_messages:
                role = stored_message["role"]
                content = stored_message.get("content", "").strip()

                if not content:
                    continue

                if role == "assistant":
                    role = "model"

                if role in ("user", "model"):
                    history.append({
                        "role": role,
                        "content": content,
                    })

            await websocket.send_json({
                "type": "status",
                "state": "processing",
            })

            try:
                active_thread_id = str(uuid4())
                active_hil_card_id = None
                active_thread_voice = is_voice_input

                thread_config = {
                    "configurable": {
                        "thread_id": active_thread_id,
                    }
                }

                result = await _invoke_with_ui_context(
                    {
                        "user_message": message,
                        "history": history,
                        "memory_facts": [],
                        "image_data": image_data,
                    },
                    thread_config,
                )

                interrupted, card_id = await _send_interrupt_to_ui(
                    websocket,
                    result,
                    voice_response=active_thread_voice,
                )

                if interrupted:
                    active_hil_card_id = card_id
                    await websocket.send_json({
                        "type": "status",
                        "state": "idle",
                    })
                    continue

                active_thread_id = None
                active_hil_card_id = None

                assistant_message = result.get("final_reply") or ""

                print("=" * 80)
                print("GRAPH RESULT:")
                print(f"  tool_calls  : {result.get('tool_calls')}")
                print(f"  final_reply : {assistant_message[:120]}")
                print("=" * 80)

                await _send_completed_graph_result(
                    websocket,
                    result,
                    voice_response=active_thread_voice,
                )
                active_thread_voice = False

            except Exception as exc:
                active_thread_id = None
                active_hil_card_id = None

                error_msg = f"Jarvis encountered an error: {exc}"
                print(f"ERROR: {error_msg}")

                await websocket.send_json({
                    "type": "message",
                    "id": str(uuid4()),
                    "role": "assistant",
                    "content": error_msg,
                    "created_at": datetime.now(UTC).isoformat(),
                    "provider": "fallback",
                    "voice_response": active_thread_voice,
                })
                await session_store.append_message(
                    role="assistant",
                    content=error_msg,
                )
                active_thread_voice = False

            await websocket.send_json({
                "type": "status",
                "state": "idle",
            })

    except WebSocketDisconnect:
        print("[WebSocket] Client disconnected abruptly.")
        return
    except Exception as exc:
        print(f"[WebSocket] Unexpected error: {exc}")
        return
