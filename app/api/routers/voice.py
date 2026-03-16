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
    return f"{text[:limit]}...<truncated>" if len(text) > limit else text


def _build_genai_client() -> genai.Client:
    if settings.gemini_use_vertex_ai:
        kwargs: dict[str, object] = {"vertexai": True, "location": settings.vertex_ai_location}
        if settings.vertex_ai_project:
            kwargs["project"] = settings.vertex_ai_project
        logger.info("Voice backend | Vertex AI | project=%s location=%s model=%s",
                    settings.vertex_ai_project or "auto", settings.vertex_ai_location, settings.gemini_model)
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
    """Try multiple attribute names — Developer API vs Vertex AI differ."""
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
        logger.error("Tool not found | conn=%s id=%s tool=%s", connection_id, call_id, tool_name)
        return types.FunctionResponse(id=call_id, name=tool_name, response={"error": f"Unknown tool '{tool_name}'."})

    started = time.perf_counter()
    try:
        result = await asyncio.to_thread(tool_fn, **args)
        logger.info("Tool ok | conn=%s id=%s tool=%s ms=%.0f", connection_id, call_id, tool_name,
                    (time.perf_counter() - started) * 1000)
        return types.FunctionResponse(id=call_id, name=tool_name, response={"result": result})
    except TypeError as exc:
        logger.exception("Tool arg error | conn=%s id=%s tool=%s", connection_id, call_id, tool_name)
        return types.FunctionResponse(id=call_id, name=tool_name, response={"error": f"Invalid args: {exc}", "args": args})
    except Exception as exc:  # noqa: BLE001
        logger.exception("Tool runtime error | conn=%s id=%s tool=%s", connection_id, call_id, tool_name)
        return types.FunctionResponse(id=call_id, name=tool_name, response={"error": str(exc)})


async def _send(websocket: WebSocket, payload: dict) -> None:
    """Send a JSON status message, swallowing errors."""
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
    logger.info("Tool registry | conn=%s count=%d", connection_id, len(tool_registry))

    user_context = (
        f"Authenticated user: {user.full_name} <{user.email}>. "
        f"Given name: {user.given_name}. Family name: {user.family_name}. "
        "You are speaking through a web UI with live voice input and voice output. "
        "Keep spoken answers concise and natural for voice. "
        "Prefer natural language references over opaque IDs. "
        "Resolve contacts by name using Google Contact tools. "
        "Before deleting tasks, events, or sending drafted emails, use the confirmation-token flow. "
        "For new emails: draft first, read back a short summary, send only after confirmation. "
        "When listing tasks or events, summarize title, status, and due date concisely. "
        "When the user asks for their day, agenda, or morning summary, use the daily executive briefing tool. "
        "If multiple items match, ask the user to clarify."
    )

    # ── Session resumption prevents the 1011 keepalive timeout ──────────
    # Without SessionResumptionConfig the Gemini WebSocket closes with
    # code 1011 after ~60 s of no model activity (e.g. while the user is
    # just listening after turn_complete).  Resumption keeps the session
    # alive across those quiet windows.
    is_native_audio = "native-audio" in settings.gemini_model.lower()

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        tools=workspace_tools,
        # Enable transcription for native-audio models so the UI can show
        # what was said and heard even in audio-only mode.
        input_audio_transcription=types.AudioTranscriptionConfig() if is_native_audio else None,
        output_audio_transcription=types.AudioTranscriptionConfig() if is_native_audio else None,
        # Session resumption is the official fix for 1011 keepalive timeouts.
        session_resumption=types.SessionResumptionConfig(),
        system_instruction=types.Content(
            parts=[types.Part.from_text(
                text=(
                    "You are a highly capable Google Workspace Executive Assistant. "
                    "You converse naturally over voice and actively use your tools to help the user. "
                    + user_context
                )
            )]
        ),
    )

    in_chunks = 0
    out_chunks = 0

    try:
        async with client.aio.live.connect(model=settings.gemini_model, config=config) as session:
            logger.info("Gemini session open | conn=%s model=%s", connection_id, settings.gemini_model)
            # Emit ready immediately — newer models skip setup_complete.
            await _send(websocket, {"type": "ready"})

            # ── Upstream: client → Gemini ────────────────────────────────
            # Audio streams CONTINUOUSLY for the entire session.
            # We never stop/start the stream between turns — Gemini's
            # server-side VAD detects speech start/stop automatically.
            # Rebuilding the audio graph between turns (our old approach)
            # introduced a gap that confused the VAD and broke turn 2+.
            async def upstream():
                nonlocal in_chunks
                try:
                    while True:
                        msg = await websocket.receive()
                        if msg.get("type") == "websocket.disconnect":
                            raise WebSocketDisconnect(code=msg.get("code", 1000))

                        # Binary frame = raw PCM16 audio (preferred path)
                        if msg.get("bytes") is not None:
                            audio_bytes = msg["bytes"]
                            in_chunks += 1
                            if settings.log_audio_chunks:
                                logger.debug("Audio in | conn=%s chunk=%d bytes=%d", connection_id, in_chunks, len(audio_bytes))
                            try:
                                await session.send_realtime_input(
                                    audio=types.Blob(data=audio_bytes, mime_type="audio/pcm;rate=16000")
                                )
                            except Exception as exc:  # noqa: BLE001
                                # Gemini connection closed — stop forwarding.
                                logger.warning("Gemini send failed | conn=%s %s", connection_id, exc)
                                break
                            continue

                        # Text frame = JSON control / legacy base64 audio
                        text = msg.get("text")
                        if text is None:
                            continue
                        decoded = json.loads(text)

                        if "audio_chunk" in decoded:
                            audio_bytes = base64.b64decode(decoded["audio_chunk"])
                            in_chunks += 1
                            try:
                                await session.send_realtime_input(
                                    audio=types.Blob(data=audio_bytes, mime_type="audio/pcm;rate=16000")
                                )
                            except Exception as exc:  # noqa: BLE001
                                logger.warning("Gemini send failed (b64) | conn=%s %s", connection_id, exc)
                                break

                        # Mic state hints — for logging only, no server action needed
                        if decoded.get("type") == "mic_start":
                            logger.info("Mic started | conn=%s", connection_id)
                        elif decoded.get("type") == "mic_stop":
                            logger.info("Mic stopped | conn=%s", connection_id)

                except WebSocketDisconnect:
                    logger.info("Client disconnected | conn=%s", connection_id)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Upstream error | conn=%s %s", connection_id, exc)

            # ── Downstream: Gemini → client ──────────────────────────────
            async def downstream():
                nonlocal out_chunks
                try:
                    async for response in session.receive():

                        if response.setup_complete:
                            logger.info("setup_complete | conn=%s", connection_id)

                        # Usage — always use safe field access (Developer API vs Vertex AI differ)
                        if response.usage_metadata:
                            usage = response.usage_metadata
                            logger.debug("Usage | conn=%s prompt=%s response=%s total=%s",
                                         connection_id,
                                         _safe_usage_field(usage, "prompt_token_count"),
                                         _safe_usage_field(usage, "candidates_token_count", "response_token_count"),
                                         _safe_usage_field(usage, "total_token_count"))

                        # Tool calls
                        if response.tool_call and response.tool_call.function_calls:
                            tool_names = [fc.name or "unknown" for fc in response.tool_call.function_calls]
                            logger.info("Tool calls | conn=%s tools=%s", connection_id, tool_names)
                            await _send(websocket, {"type": "tool_calling", "tools": tool_names})
                            responses = []
                            for fc in response.tool_call.function_calls:
                                responses.append(await _execute_tool_call(
                                    function_call=fc, tool_registry=tool_registry, connection_id=connection_id
                                ))
                            await session.send_tool_response(function_responses=responses)
                            await _send(websocket, {"type": "tool_done", "tools": tool_names})

                        # Server content
                        sc = response.server_content
                        if sc:
                            if getattr(sc, "interrupted", False):
                                logger.info("Interrupted | conn=%s", connection_id)
                                await _send(websocket, {"type": "interrupted"})

                            if getattr(sc, "turn_complete", False):
                                logger.info("Turn complete | conn=%s", connection_id)
                                await _send(websocket, {"type": "turn_complete"})
                                # Do NOT inject silence here — that interferes with
                                # Gemini's VAD.  Audio streams continuously from the
                                # client so the VAD window stays open naturally.

                            if sc.model_turn:
                                for part in sc.model_turn.parts:
                                    if part.text:
                                        logger.debug("Model text | conn=%s text=%s", connection_id, _truncate(part.text, 200))
                                        await _send(websocket, {"type": "text", "text": part.text})
                                    if part.inline_data and part.inline_data.data:
                                        out_chunks += 1
                                        await websocket.send_bytes(part.inline_data.data)

                        # Transcription (native-audio models)
                        if hasattr(response, "input_transcription") and response.input_transcription:
                            t = response.input_transcription
                            if t.text and t.text.strip():
                                await _send(websocket, {"type": "input_transcript", "text": t.text, "finished": t.finished})

                        if hasattr(response, "output_transcription") and response.output_transcription:
                            t = response.output_transcription
                            if t.text and t.text.strip():
                                await _send(websocket, {"type": "output_transcript", "text": t.text, "finished": t.finished})

                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Downstream error | conn=%s %s", connection_id, exc)
                    await _send(websocket, {"error": f"Stream error: {exc}"})

            up_task   = asyncio.create_task(upstream())
            down_task = asyncio.create_task(downstream())
            try:
                await up_task
            finally:
                down_task.cancel()
                await asyncio.gather(down_task, return_exceptions=True)
                logger.info("Session closed | conn=%s in=%d out=%d", connection_id, in_chunks, out_chunks)

    except Exception as exc:  # noqa: BLE001
        logger.exception("Session setup error | conn=%s %s", connection_id, exc)
        try:
            await websocket.send_text(json.dumps({"error": f"Session error: {exc}"}))
            await websocket.close(code=1011, reason="Session setup failed.")
        except Exception:  # noqa: BLE001
            pass
