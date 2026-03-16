from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import Callable
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types

from app.core.config import settings
from app.services.auth import auth_manager
from app.services.workspace_tools import build_workspace_tools


router = APIRouter(tags=["voice"])
logger = logging.getLogger(__name__)


def _mask_session(session_id: str) -> str:
    if len(session_id) < 10:
        return "***"
    return f"{session_id[:4]}...{session_id[-4:]}"


def _truncate(value: object, limit: int = 600) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...<truncated>"


def _build_genai_client() -> genai.Client:
    if settings.gemini_use_vertex_ai:
        kwargs: dict[str, object] = {
            "vertexai": True,
            "location": settings.vertex_ai_location,
        }
        if settings.vertex_ai_project:
            kwargs["project"] = settings.vertex_ai_project
        logger.info(
            "Voice backend configured for Vertex AI | project=%s location=%s model=%s",
            settings.vertex_ai_project or "auto",
            settings.vertex_ai_location,
            settings.gemini_model,
        )
        return genai.Client(**kwargs)

    kwargs = {}
    if settings.gemini_api_key:
        kwargs["api_key"] = settings.gemini_api_key
    logger.info("Voice backend configured for Gemini Developer API | model=%s", settings.gemini_model)
    return genai.Client(**kwargs)


def _normalize_tool_args(raw_args: object) -> dict:
    if raw_args is None:
        return {}
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except json.JSONDecodeError:
            return {"value": raw_args}
    try:
        return dict(raw_args)  # type: ignore[arg-type]
    except Exception:
        return {"value": str(raw_args)}


def _safe_usage_field(usage: object, *field_names: str) -> object:
    """Safely read a usage metadata field, trying multiple candidate names.

    The google-genai SDK exposes different field names depending on the backend:
      - Developer API  -> candidates_token_count
      - Vertex AI      -> response_token_count

    NEVER access usage.candidates_token_count directly.  Python evaluates ALL
    function arguments before the call, so even a logger.debug() line will raise
    AttributeError on Vertex AI and silently kill the receive_from_gemini task,
    breaking all audio and tool-call responses.
    """
    for name in field_names:
        value = getattr(usage, name, None)
        if value is not None:
            return value
    return None


async def _execute_tool_call(
    *,
    function_call: types.FunctionCall,
    tool_registry: dict[str, Callable],
    connection_id: str,
) -> types.FunctionResponse:
    tool_name = function_call.name or ""
    call_id = function_call.id or ""
    args = _normalize_tool_args(function_call.args)
    tool_fn = tool_registry.get(tool_name)

    if settings.log_tool_payloads:
        logger.info(
            "Tool call requested | conn=%s id=%s tool=%s args=%s",
            connection_id,
            call_id,
            tool_name,
            _truncate(args),
        )
    else:
        logger.info(
            "Tool call requested | conn=%s id=%s tool=%s arg_keys=%s",
            connection_id,
            call_id,
            tool_name,
            sorted(args.keys()),
        )

    if tool_fn is None:
        error_message = f"Unknown tool '{tool_name}'."
        logger.error("Tool call failed | conn=%s id=%s tool=%s reason=%s", connection_id, call_id, tool_name, error_message)
        return types.FunctionResponse(
            id=call_id,
            name=tool_name,
            response={"error": error_message},
        )

    started = time.perf_counter()
    try:
        result = await asyncio.to_thread(tool_fn, **args)
        elapsed_ms = (time.perf_counter() - started) * 1000
        if settings.log_tool_payloads:
            logger.info(
                "Tool call succeeded | conn=%s id=%s tool=%s duration_ms=%.2f result=%s",
                connection_id,
                call_id,
                tool_name,
                elapsed_ms,
                _truncate(result),
            )
        else:
            logger.info(
                "Tool call succeeded | conn=%s id=%s tool=%s duration_ms=%.2f",
                connection_id,
                call_id,
                tool_name,
                elapsed_ms,
            )
        return types.FunctionResponse(
            id=call_id,
            name=tool_name,
            response={"result": result},
        )
    except TypeError as exc:
        logger.exception(
            "Tool call argument error | conn=%s id=%s tool=%s args=%s",
            connection_id,
            call_id,
            tool_name,
            _truncate(args),
        )
        return types.FunctionResponse(
            id=call_id,
            name=tool_name,
            response={"error": f"Invalid tool arguments: {str(exc)}", "args": args},
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Tool call runtime error | conn=%s id=%s tool=%s", connection_id, call_id, tool_name)
        return types.FunctionResponse(
            id=call_id,
            name=tool_name,
            response={"error": str(exc)},
        )


async def _send_status(websocket: WebSocket, payload: dict) -> None:
    """Send a JSON status message to the client, swallowing send errors."""
    try:
        await websocket.send_text(json.dumps(payload))
    except Exception:  # noqa: BLE001
        pass


@router.websocket("/ws")
async def voice_agent_endpoint(websocket: WebSocket):
    connection_id = uuid4().hex[:8]
    await websocket.accept()

    session_id = websocket.query_params.get("session_id")
    if not session_id:
        logger.warning("WebSocket denied | conn=%s reason=missing_session_id", connection_id)
        await websocket.send_text(json.dumps({"error": "Missing session_id query parameter."}))
        await websocket.close(code=1008, reason="Missing session_id query parameter.")
        return

    logger.info("WebSocket accepted | conn=%s session=%s", connection_id, _mask_session(session_id))

    user = auth_manager.get_authenticated_user(session_id)
    if not user:
        logger.warning("WebSocket denied | conn=%s reason=invalid_or_expired_session", connection_id)
        await websocket.send_text(json.dumps({"error": "Invalid or expired session."}))
        await websocket.close(code=1008, reason="Invalid or expired session.")
        return

    client = _build_genai_client()
    workspace_tools = build_workspace_tools(user.credentials)
    tool_registry = {fn.__name__: fn for fn in workspace_tools}
    logger.info(
        "Tool registry ready | conn=%s tool_count=%d tools=%s",
        connection_id,
        len(tool_registry),
        ",".join(sorted(tool_registry.keys())),
    )

    user_context = (
        f"Authenticated user: {user.full_name} <{user.email}>. "
        f"Given name: {user.given_name}. Family name: {user.family_name}. "
        "You are speaking through a web UI with live voice input and voice output. "
        "When sending email or booking meetings, act on this user's behalf. "
        "The interaction is happening through a browser-based voice web UI, so keep spoken answers concise and natural. "
        "For tasks and calendar actions, prefer natural language references over opaque IDs whenever possible. "
        "When the user mentions a person by name for email or meetings, prefer Google Contact resolution tools over asking for raw email addresses. "
        "When the user mentions a task or event by title or description, first use the natural-language tools. "
        "Before deleting a task, deleting a calendar event, removing a deadline, or sending a drafted email, you must use the confirmation-token flow and explicitly wait for the user's confirmation. "
        "For newly composed emails over voice, draft the email first, read back a short review summary, and send only after the user confirms. "
        "When listing tasks or events, summarize title, details, status, and due date or time in a concise voice-friendly way. "
        "When the user asks for their day, agenda, briefing, or a morning summary, use the daily executive briefing tool. "
        "If multiple tasks or events match, ask the user to clarify rather than guessing."
    )

    # Use snake_case fields — camelCase aliases work too, but snake_case is the
    # canonical form in the current google-genai SDK and avoids edge-case aliasing
    # bugs when switching between Developer API and Vertex AI backends.
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        tools=workspace_tools,
        system_instruction=types.Content(
            parts=[
                types.Part.from_text(
                    text=(
                        "You are a highly capable Google Workspace Executive Assistant. "
                        "You converse naturally over voice and actively use your tools to help the user. "
                        + user_context
                    )
                )
            ]
        ),
    )

    incoming_audio_chunks = 0
    outgoing_audio_chunks = 0

    try:
        async with client.aio.live.connect(model=settings.gemini_model, config=config) as session:
            logger.info("Gemini live session connected | conn=%s model=%s", connection_id, settings.gemini_model)

            async def receive_from_client():
                nonlocal incoming_audio_chunks
                try:
                    while True:
                        message = await websocket.receive()
                        message_type = message.get("type")
                        if message_type == "websocket.disconnect":
                            raise WebSocketDisconnect(code=message.get("code", 1000))

                        # Preferred path: binary websocket frames with PCM16 data.
                        if message.get("bytes") is not None:
                            audio_bytes = message["bytes"]
                            incoming_audio_chunks += 1
                            if settings.log_audio_chunks:
                                logger.debug(
                                    "Client audio chunk | conn=%s chunk=%d bytes=%d",
                                    connection_id,
                                    incoming_audio_chunks,
                                    len(audio_bytes),
                                )
                            await session.send_realtime_input(
                                audio=types.Blob(data=audio_bytes, mime_type="audio/pcm;rate=16000")
                            )
                            continue

                        # Backward-compatible path for legacy base64 JSON payloads.
                        text_payload = message.get("text")
                        if text_payload is None:
                            continue
                        decoded_message = json.loads(text_payload)
                        if "audio_chunk" in decoded_message:
                            audio_bytes = base64.b64decode(decoded_message["audio_chunk"])
                            incoming_audio_chunks += 1
                            if settings.log_audio_chunks:
                                logger.debug(
                                    "Client audio chunk (base64) | conn=%s chunk=%d bytes=%d",
                                    connection_id,
                                    incoming_audio_chunks,
                                    len(audio_bytes),
                                )
                            await session.send_realtime_input(
                                audio=types.Blob(data=audio_bytes, mime_type="audio/pcm;rate=16000")
                            )
                except WebSocketDisconnect:
                    logger.info("Voice client disconnected | conn=%s", connection_id)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Voice client stream error | conn=%s error=%s", connection_id, exc)
                    await _send_status(websocket, {"error": f"Error reading from client: {str(exc)}"})

            async def receive_from_gemini():
                nonlocal outgoing_audio_chunks
                try:
                    async for response in session.receive():

                        # ── Setup complete ──────────────────────────────────
                        if response.setup_complete:
                            logger.info("Live setup complete | conn=%s", connection_id)
                            await _send_status(websocket, {"type": "ready"})

                        # ── Usage metadata ──────────────────────────────────
                        # Use _safe_usage_field() — NEVER access .candidates_token_count
                        # directly (crashes on Vertex AI and kills this coroutine).
                        if response.usage_metadata:
                            usage = response.usage_metadata
                            logger.debug(
                                "Usage update | conn=%s prompt_tokens=%s response_tokens=%s total_tokens=%s",
                                connection_id,
                                _safe_usage_field(usage, "prompt_token_count"),
                                _safe_usage_field(usage, "candidates_token_count", "response_token_count"),
                                _safe_usage_field(usage, "total_token_count"),
                            )

                        # ── Tool calls ──────────────────────────────────────
                        if response.tool_call and response.tool_call.function_calls:
                            tool_names = [fc.name or "unknown" for fc in response.tool_call.function_calls]
                            logger.info(
                                "Tool calls received | conn=%s count=%d tools=%s",
                                connection_id,
                                len(response.tool_call.function_calls),
                                tool_names,
                            )
                            # Notify the UI so it can show "Calling tool…" feedback.
                            await _send_status(websocket, {"type": "tool_calling", "tools": tool_names})

                            function_responses: list[types.FunctionResponse] = []
                            for function_call in response.tool_call.function_calls:
                                function_response = await _execute_tool_call(
                                    function_call=function_call,
                                    tool_registry=tool_registry,
                                    connection_id=connection_id,
                                )
                                function_responses.append(function_response)

                            await session.send_tool_response(function_responses=function_responses)
                            logger.info(
                                "Tool responses sent | conn=%s count=%d",
                                connection_id,
                                len(function_responses),
                            )
                            await _send_status(websocket, {"type": "tool_done", "tools": tool_names})

                        # ── Server content ──────────────────────────────────
                        server_content = response.server_content
                        if server_content:
                            # Barge-in: user spoke while model was talking.
                            # Tell the client to flush its audio playback buffer.
                            if getattr(server_content, "interrupted", False):
                                logger.info("Model interrupted | conn=%s", connection_id)
                                await _send_status(websocket, {"type": "interrupted"})

                            # Model finished its spoken turn.
                            if getattr(server_content, "turn_complete", False):
                                logger.info("Model turn complete | conn=%s", connection_id)
                                await _send_status(websocket, {"type": "turn_complete"})

                            if server_content.model_turn:
                                for part in server_content.model_turn.parts:
                                    if part.text:
                                        logger.debug(
                                            "Model text part | conn=%s text=%s",
                                            connection_id,
                                            _truncate(part.text, 240),
                                        )
                                        # Forward transcript to the UI for display.
                                        await _send_status(websocket, {"type": "text", "text": part.text})

                                    if part.inline_data and part.inline_data.data:
                                        outgoing_audio_chunks += 1
                                        if settings.log_audio_chunks:
                                            logger.debug(
                                                "Model audio chunk | conn=%s chunk=%d bytes=%d",
                                                connection_id,
                                                outgoing_audio_chunks,
                                                len(part.inline_data.data),
                                            )
                                        await websocket.send_bytes(part.inline_data.data)

                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Gemini stream error | conn=%s error=%s", connection_id, exc)
                    await _send_status(websocket, {"error": f"Error reading from Gemini: {str(exc)}"})

            client_task = asyncio.create_task(receive_from_client())
            gemini_task = asyncio.create_task(receive_from_gemini())
            try:
                await client_task
            finally:
                gemini_task.cancel()
                await asyncio.gather(gemini_task, return_exceptions=True)
                logger.info(
                    "Voice session summary | conn=%s in_chunks=%d out_chunks=%d",
                    connection_id,
                    incoming_audio_chunks,
                    outgoing_audio_chunks,
                )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Gemini session setup error | conn=%s error=%s", connection_id, exc)
        try:
            await websocket.send_text(json.dumps({"error": f"Gemini session error: {str(exc)}"}))
            await websocket.close(code=1011, reason="Gemini session setup failed.")
        except Exception:  # noqa: BLE001
            pass
