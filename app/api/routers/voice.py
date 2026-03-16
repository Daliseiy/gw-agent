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

# 20 ms of silence at 16 kHz mono PCM-16 (640 bytes).
# Sent as a keepalive when the user is in "always-on" mode and the mic
# is quiet so that Gemini's VAD does not time out the listening window.
_SILENCE_20MS = bytes(640)


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
            "Voice backend | Vertex AI | project=%s location=%s model=%s",
            settings.vertex_ai_project or "auto",
            settings.vertex_ai_location,
            settings.gemini_model,
        )
        return genai.Client(**kwargs)

    kwargs = {}
    if settings.gemini_api_key:
        kwargs["api_key"] = settings.gemini_api_key
    logger.info("Voice backend | Developer API | model=%s", settings.gemini_model)
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
    """Try multiple attribute names — Developer API vs Vertex AI use different names."""
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
        logger.info("Tool call | conn=%s id=%s tool=%s args=%s", connection_id, call_id, tool_name, _truncate(args))
    else:
        logger.info("Tool call | conn=%s id=%s tool=%s arg_keys=%s", connection_id, call_id, tool_name, sorted(args.keys()))

    if tool_fn is None:
        msg = f"Unknown tool '{tool_name}'."
        logger.error("Tool not found | conn=%s id=%s tool=%s", connection_id, call_id, tool_name)
        return types.FunctionResponse(id=call_id, name=tool_name, response={"error": msg})

    started = time.perf_counter()
    try:
        result = await asyncio.to_thread(tool_fn, **args)
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info("Tool ok | conn=%s id=%s tool=%s ms=%.0f", connection_id, call_id, tool_name, elapsed_ms)
        return types.FunctionResponse(id=call_id, name=tool_name, response={"result": result})
    except TypeError as exc:
        logger.exception("Tool arg error | conn=%s id=%s tool=%s", connection_id, call_id, tool_name)
        return types.FunctionResponse(id=call_id, name=tool_name, response={"error": f"Invalid args: {exc}", "args": args})
    except Exception as exc:  # noqa: BLE001
        logger.exception("Tool runtime error | conn=%s id=%s tool=%s", connection_id, call_id, tool_name)
        return types.FunctionResponse(id=call_id, name=tool_name, response={"error": str(exc)})


async def _send_status(websocket: WebSocket, payload: dict) -> None:
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
        await websocket.send_text(json.dumps({"error": "Missing session_id query parameter."}))
        await websocket.close(code=1008, reason="Missing session_id query parameter.")
        return

    logger.info("WS accepted | conn=%s session=%s", connection_id, _mask_session(session_id))

    user = auth_manager.get_authenticated_user(session_id)
    if not user:
        await websocket.send_text(json.dumps({"error": "Invalid or expired session."}))
        await websocket.close(code=1008, reason="Invalid or expired session.")
        return

    client = _build_genai_client()
    workspace_tools = build_workspace_tools(user.credentials)
    tool_registry = {fn.__name__: fn for fn in workspace_tools}
    logger.info("Tool registry | conn=%s count=%d tools=%s", connection_id, len(tool_registry), ",".join(sorted(tool_registry.keys())))

    user_context = (
        f"Authenticated user: {user.full_name} <{user.email}>. "
        f"Given name: {user.given_name}. Family name: {user.family_name}. "
        "You are speaking through a web UI with live voice input and voice output. "
        "When sending email or booking meetings, act on this user's behalf. "
        "Keep spoken answers concise and natural for voice. "
        "Prefer natural language references over opaque IDs. "
        "Resolve contacts by name using Google Contact tools instead of asking for raw email addresses. "
        "Before deleting tasks, events, deadlines, or sending drafted emails, use the confirmation-token flow. "
        "For new emails over voice: draft first, read back a short summary, send only after confirmation. "
        "When listing tasks or events, summarize title, status, and due date concisely. "
        "When the user asks for their day, agenda, briefing, or morning summary, use the daily executive briefing tool. "
        "If multiple items match, ask the user to clarify."
    )

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
    # Shared state: tracks whether the client currently has the mic open.
    # Used by the keepalive task to decide whether to drip silence.
    mic_active = False
    last_client_audio_ts: float = 0.0

    try:
        async with client.aio.live.connect(model=settings.gemini_model, config=config) as session:
            logger.info("Gemini session open | conn=%s model=%s", connection_id, settings.gemini_model)
            # Emit ready immediately — newer models (e.g. gemini-2.5-flash-native-audio-preview)
            # do not send a setup_complete event, so we cannot gate the UI on it.
            await _send_status(websocket, {"type": "ready"})

            # ── Task 1: client → Gemini ──────────────────────────────────
            async def receive_from_client():
                nonlocal incoming_audio_chunks, mic_active, last_client_audio_ts
                try:
                    while True:
                        message = await websocket.receive()
                        if message.get("type") == "websocket.disconnect":
                            raise WebSocketDisconnect(code=message.get("code", 1000))

                        # Binary frame = PCM16 audio
                        if message.get("bytes") is not None:
                            audio_bytes = message["bytes"]
                            incoming_audio_chunks += 1
                            last_client_audio_ts = time.monotonic()
                            mic_active = True
                            if settings.log_audio_chunks:
                                logger.debug("Client audio | conn=%s chunk=%d bytes=%d", connection_id, incoming_audio_chunks, len(audio_bytes))
                            await session.send_realtime_input(
                                audio=types.Blob(data=audio_bytes, mime_type="audio/pcm;rate=16000")
                            )
                            continue

                        # Text frame = JSON control messages
                        text_payload = message.get("text")
                        if text_payload is None:
                            continue
                        decoded = json.loads(text_payload)

                        # Legacy base64 audio path
                        if "audio_chunk" in decoded:
                            audio_bytes = base64.b64decode(decoded["audio_chunk"])
                            incoming_audio_chunks += 1
                            last_client_audio_ts = time.monotonic()
                            mic_active = True
                            await session.send_realtime_input(
                                audio=types.Blob(data=audio_bytes, mime_type="audio/pcm;rate=16000")
                            )
                            continue

                        # Mic state signals from frontend
                        if decoded.get("type") == "mic_start":
                            mic_active = True
                            last_client_audio_ts = time.monotonic()
                            logger.info("Mic started | conn=%s", connection_id)
                        elif decoded.get("type") == "mic_stop":
                            mic_active = False
                            logger.info("Mic stopped | conn=%s", connection_id)

                except WebSocketDisconnect:
                    logger.info("Client disconnected | conn=%s", connection_id)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Client stream error | conn=%s error=%s", connection_id, exc)
                    await _send_status(websocket, {"error": f"Client stream error: {exc}"})

            # ── Task 2: Gemini → client ──────────────────────────────────
            async def receive_from_gemini():
                nonlocal outgoing_audio_chunks
                try:
                    async for response in session.receive():

                        if response.setup_complete:
                            # Already emitted 'ready' when the session context opened.
                            # Just log — don't emit a second ready which would re-start the mic.
                            logger.info("Live setup_complete received | conn=%s", connection_id)

                        # Usage — safe field access only (crashes on Vertex AI otherwise)
                        if response.usage_metadata:
                            usage = response.usage_metadata
                            logger.debug(
                                "Usage | conn=%s prompt=%s response=%s total=%s",
                                connection_id,
                                _safe_usage_field(usage, "prompt_token_count"),
                                _safe_usage_field(usage, "candidates_token_count", "response_token_count"),
                                _safe_usage_field(usage, "total_token_count"),
                            )

                        # Tool calls
                        if response.tool_call and response.tool_call.function_calls:
                            tool_names = [fc.name or "unknown" for fc in response.tool_call.function_calls]
                            logger.info("Tool calls | conn=%s count=%d tools=%s", connection_id, len(tool_names), tool_names)
                            await _send_status(websocket, {"type": "tool_calling", "tools": tool_names})

                            function_responses: list[types.FunctionResponse] = []
                            for fc in response.tool_call.function_calls:
                                function_responses.append(
                                    await _execute_tool_call(function_call=fc, tool_registry=tool_registry, connection_id=connection_id)
                                )

                            await session.send_tool_response(function_responses=function_responses)
                            await _send_status(websocket, {"type": "tool_done", "tools": tool_names})

                        # Server content (audio + turn lifecycle events)
                        server_content = response.server_content
                        if server_content:
                            if getattr(server_content, "interrupted", False):
                                logger.info("Interrupted | conn=%s", connection_id)
                                await _send_status(websocket, {"type": "interrupted"})

                            if getattr(server_content, "turn_complete", False):
                                logger.info("Turn complete | conn=%s", connection_id)
                                await _send_status(websocket, {"type": "turn_complete"})

                            if server_content.model_turn:
                                for part in server_content.model_turn.parts:
                                    if part.text:
                                        logger.debug("Model text | conn=%s text=%s", connection_id, _truncate(part.text, 240))
                                        await _send_status(websocket, {"type": "text", "text": part.text})
                                    if part.inline_data and part.inline_data.data:
                                        outgoing_audio_chunks += 1
                                        if settings.log_audio_chunks:
                                            logger.debug("Model audio | conn=%s chunk=%d bytes=%d", connection_id, outgoing_audio_chunks, len(part.inline_data.data))
                                        await websocket.send_bytes(part.inline_data.data)

                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Gemini stream error | conn=%s error=%s", connection_id, exc)
                    await _send_status(websocket, {"error": f"Gemini stream error: {exc}"})

            # ── Task 3: silence keepalive ────────────────────────────────
            # When the mic is active but no audio has arrived for >1.5 s
            # (e.g. the user paused), drip silence into the session so that
            # Gemini's server-side VAD does not close the listening window.
            # Without this, the first turn works but subsequent turns are
            # silently dropped because the API thinks the stream ended.
            async def silence_keepalive():
                while True:
                    await asyncio.sleep(0.5)
                    if not mic_active:
                        continue
                    gap = time.monotonic() - last_client_audio_ts
                    if gap >= 1.5:
                        try:
                            await session.send_realtime_input(
                                audio=types.Blob(data=_SILENCE_20MS, mime_type="audio/pcm;rate=16000")
                            )
                        except Exception:  # noqa: BLE001
                            break  # session closed; let the other tasks clean up

            client_task   = asyncio.create_task(receive_from_client())
            gemini_task   = asyncio.create_task(receive_from_gemini())
            keepalive_task = asyncio.create_task(silence_keepalive())

            try:
                await client_task
            finally:
                gemini_task.cancel()
                keepalive_task.cancel()
                await asyncio.gather(gemini_task, keepalive_task, return_exceptions=True)
                logger.info(
                    "Session closed | conn=%s in_chunks=%d out_chunks=%d",
                    connection_id,
                    incoming_audio_chunks,
                    outgoing_audio_chunks,
                )

    except Exception as exc:  # noqa: BLE001
        logger.exception("Session setup error | conn=%s error=%s", connection_id, exc)
        try:
            await websocket.send_text(json.dumps({"error": f"Gemini session error: {exc}"}))
            await websocket.close(code=1011, reason="Gemini session setup failed.")
        except Exception:  # noqa: BLE001
            pass
