"""Replaceable audio transport. Audio stays in memory; only request metadata is stored.

The ordinary session actor owns conversation, tool execution and interruption.
Voice does not create a second model history or replay actions.
"""
import asyncio
import base64
from dataclasses import dataclass, field
import json
import io
import os
from pathlib import Path
import re
import wave
from typing import Protocol
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .errors import ServiceError
from .store import now, uid

MAX_AUDIO_BYTES = 4 * 1024 * 1024
MAX_SPEECH_BYTES = 12 * 1024 * 1024
AUDIO_TYPES = {"audio/wav": "wav"}


@dataclass(frozen=True)
class VoiceConfig:
    base_url: str = "https://api.openai.com/v1"
    api_key: str = field(default="", repr=False)
    transcription_model: str = "gpt-4o-mini-transcribe"
    speech_model: str = "gpt-4o-mini-tts"
    voice: str = "coral"
    hosted: bool = False
    provider: str = "openai"
    stt_base_url: str = ""

    @classmethod
    def from_env(cls):
        provider = os.getenv("ATHENA_AUDIO_PROVIDER", "openai")
        dashscope = provider == "dashscope"
        key = os.getenv("ATHENA_AUDIO_API_KEY", "")
        key_file = os.getenv("ATHENA_AUDIO_API_KEY_FILE", "")
        if not key and key_file:
            try:
                key = Path(key_file).expanduser().read_text().strip()
            except OSError:
                key = ""
        return cls(
            base_url=os.getenv("ATHENA_AUDIO_BASE_URL", "https://dashscope.aliyuncs.com/api/v1" if dashscope else "https://api.openai.com/v1").rstrip("/"),
            api_key=key,
            transcription_model=os.getenv("ATHENA_AUDIO_STT_MODEL", "qwen3-asr-flash" if dashscope else "gpt-4o-mini-transcribe"),
            speech_model=os.getenv("ATHENA_AUDIO_TTS_MODEL", "qwen3-tts-flash" if dashscope else "gpt-4o-mini-tts"),
            voice=os.getenv("ATHENA_AUDIO_VOICE", "Cherry" if dashscope else "coral"),
            hosted=os.getenv("ATHENA_DEPLOYMENT", "local") == "hosted",
            provider=provider, stt_base_url=os.getenv("ATHENA_AUDIO_STT_BASE_URL", "").rstrip("/"),
        )

    def valid(self):
        return bool(self.api_key and self.transcription_model and self.speech_model) and self.provider in {"openai", "dashscope"} and all(self.valid_url(url) for url in (self.base_url, self.stt_base_url or self.base_url))

    @staticmethod
    def valid_url(value):
        url = urlparse(value)
        return (bool(url.hostname)
                and not url.username and not url.password and not url.query and not url.fragment
                and (url.scheme == "https" or (url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"})))


class VoiceBilling(Protocol):
    """Central ledger adapter, with an atomic reservation before the provider call.

    Reserve units use validated WAV duration; speech uses input characters. A failed
    or cancelled upstream request may still have incurred cost; settle receives
    that uncertainty and must not blindly refund its reservation.
    """
    async def reserve(self, user_id: str, request_id: str, operation: str, model: str, units: dict) -> object: ...
    async def settle(self, reservation: object, *, status: str, usage: dict | None) -> None: ...


def audio_duration(content):
    try:
        with wave.open(io.BytesIO(content), "rb") as audio:
            frames, rate = audio.getnframes(), audio.getframerate()
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or not 16000 <= rate <= 96000 or not 0 < frames <= rate * 31:
                raise ValueError("unsupported WAV")
            if len(audio.readframes(frames)) != frames * 2:
                raise ValueError("truncated WAV")
            return frames / rate
    except (wave.Error, EOFError, ValueError):
        raise HTTPException(400, "voice_invalid_audio") from None


async def initialize_voice(store):
    await store.execute("""CREATE TABLE IF NOT EXISTS voice_requests (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
        session_id TEXT NOT NULL REFERENCES sessions(id), operation TEXT NOT NULL,
        model TEXT NOT NULL, status TEXT NOT NULL, input_units INTEGER NOT NULL,
        output_bytes INTEGER NOT NULL DEFAULT 0, usage TEXT, error_code TEXT,
        created_at TEXT NOT NULL, finished_at TEXT)""")
    # Interrupted requests are audited, never replayed after a restart.
    await store.execute("UPDATE voice_requests SET status='interrupted',error_code='restarted',finished_at=? WHERE status='running'", (now(),))


class VoiceService:
    def __init__(self, config, store, *, billing: VoiceBilling | None = None, client=None):
        self.config, self.store, self.billing = config, store, billing
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10), follow_redirects=False, trust_env=False)
        self.active = set()

    async def close(self):
        await self.client.aclose()

    def diagnostic(self):
        configured = self.config.valid() and (not self.config.hosted or self.billing is not None)
        return {"available": configured, "code": None if configured else "voice_unavailable", "transport": "transcription-speech", "raw_audio_retained": False, "unsupported_languages": ["ar"] if self.config.provider == "dashscope" else []}

    async def status(self, user_id):
        return self.diagnostic()

    async def perform(self, user_id, session_id, operation, payload, *, media_type="audio/wav", language=""):
        if not self.diagnostic()["available"]:
            raise ServiceError("voice_unavailable", "Voice is not configured.")
        if operation not in {"transcribe", "speech"}:
            raise HTTPException(400, "invalid_input")
        if operation == "speech" and (not isinstance(payload, str) or not 0 < len(payload) <= 600):
            raise HTTPException(400, "invalid_input")
        units = {"audio_bytes": len(payload), "seconds": audio_duration(payload)} if operation == "transcribe" else {"characters": len(payload)}
        # One transcription and one synthesis per account can overlap. A queue
        # is owned by the caller, not by an unbounded server semaphore backlog.
        key = (user_id, operation)
        if key in self.active:
            raise ServiceError("voice_busy", "A voice request is already in progress.", 429)
        self.active.add(key)
        request_id, reservation, usage = uid(), None, None
        model = self.config.transcription_model if operation == "transcribe" else self.config.speech_model
        status, code, output_bytes = "failed", "voice_provider_error", 0
        recorded = False
        try:
            if self.billing:
                reservation = await self.billing.reserve(user_id, request_id, operation, model, units)
            await self.store.execute("INSERT INTO voice_requests (id,user_id,session_id,operation,model,status,input_units,created_at) VALUES (?,?,?,?,?,'running',?,?)", (request_id, user_id, session_id, operation, model, len(payload), now()))
            recorded = True
            headers = {"Authorization": "Bearer " + self.config.api_key}
            if operation == "transcribe":
                data = {"model": model, "response_format": "json"}
                if language:
                    data["language"] = language
                kwargs = {"data": data, "files": {"file": ("utterance." + AUDIO_TYPES[media_type], payload, media_type)}}
                endpoint, limit = "transcriptions", 256 * 1024
            else:
                kwargs = {"json": {"model": model, "input": payload, "voice": self.config.voice, "response_format": "mp3"}}
                endpoint, limit = "speech", MAX_SPEECH_BYTES
            url = self.config.base_url + "/audio/" + endpoint
            if self.config.provider == "dashscope":
                limit = 256 * 1024
                if operation == "transcribe":
                    root = self.config.stt_base_url or self.config.base_url.removesuffix("/api/v1") + "/compatible-mode/v1"
                    url = root + "/chat/completions"
                    kwargs = {"json": {"model": model, "messages": [{"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": "data:audio/wav;base64," + base64.b64encode(payload).decode("ascii")}}]}], "stream": False}}
                else:
                    url = self.config.base_url + "/services/aigc/multimodal-generation/generation"
                    kwargs = {"json": {"model": model, "input": {"text": payload, "voice": self.config.voice, "language_type": "Auto"}}}
            async with self.client.stream("POST", url, headers=headers, **kwargs) as response:
                if response.status_code != 200:
                    # Never forward provider bodies or request headers to users.
                    code = "voice_rate_limited" if response.status_code == 429 else "voice_provider_error"
                    raise ServiceError(code, "The voice service could not complete this request.")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > limit:
                        raise ServiceError("voice_provider_error", "Voice response exceeded its limit.")
                output_bytes = len(content)
                if operation == "transcribe":
                    result = json.loads(content)
                    text = result["choices"][0]["message"]["content"] if self.config.provider == "dashscope" else result.get("text")
                    if not isinstance(text, str) or len(text) > 30000:
                        raise ValueError("invalid transcription")
                    raw_usage = result.get("usage")
                    # Whitelist numeric usage fields; providers may return more
                    # fields than the documented usage contract.
                    usage = {k: raw_usage[k] for k in ("input_tokens", "output_tokens", "total_tokens", "seconds") if isinstance(raw_usage, dict) and isinstance(raw_usage.get(k), (int, float)) and not isinstance(raw_usage.get(k), bool)}
                    value = {"text": text.strip(), "request_id": request_id}
                elif self.config.provider == "dashscope":
                    result = json.loads(content)
                    # A provider-returned URL is never a generic server-side fetch.
                    audio_url = result["output"]["audio"]["url"]
                    target = urlparse(audio_url)
                    if target.scheme not in {"http", "https"} or target.username or target.password or target.port not in {None, 443} or not re.fullmatch(r"dashscope-result-[a-z0-9-]+\.oss-[a-z0-9-]+\.aliyuncs\.com", target.hostname or ""):
                        raise ValueError("invalid audio download")
                    # Alibaba examples use HTTP; upgrade the same signed OSS URL.
                    audio_url = target._replace(scheme="https").geturl()
                    async with self.client.stream("GET", audio_url) as download:
                        if download.status_code != 200:
                            raise ValueError("audio download failed")
                        audio = bytearray()
                        async for chunk in download.aiter_bytes():
                            audio.extend(chunk)
                            if len(audio) > MAX_SPEECH_BYTES:
                                raise ValueError("audio too large")
                    if not audio.startswith(b"RIFF") or audio[8:12] != b"WAVE":
                        raise ValueError("invalid WAV")
                    raw_characters = result.get("usage", {}).get("characters")
                    usage = {"characters": raw_characters} if type(raw_characters) is int and raw_characters >= 0 else None
                    value = bytes(audio)
                    output_bytes = len(value)
                else:
                    if not content or not response.headers.get("content-type", "").split(";", 1)[0].strip() in {"audio/mpeg", "audio/mp3", "application/octet-stream"}:
                        raise ValueError("invalid speech")
                    value = bytes(content)
                status, code = "complete", None
                return value
        except asyncio.CancelledError:
            status, code = "interrupted", "voice_interrupted"
            raise
        except ServiceError:
            raise
        except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError):
            raise ServiceError("voice_provider_error", "The voice service could not complete this request.") from None
        finally:
            try:
                if recorded:
                    await asyncio.shield(self.store.execute("UPDATE voice_requests SET status=?,usage=?,error_code=?,output_bytes=?,finished_at=? WHERE id=?", (status, json.dumps(usage) if usage else None, code, output_bytes, now(), request_id)))
                if self.billing and reservation is not None:
                    await asyncio.shield(self.billing.settle(reservation, status=status, usage=usage))
            finally:
                self.active.discard(key)


class SpeechInput(BaseModel):
    text: str = Field(min_length=1, max_length=600)


async def _disconnectable(request, operation):
    task = asyncio.create_task(operation)
    async def watch():
        # All routes have consumed their body before entering here. A direct
        # receive avoids is_disconnected's cancelled polling scope swallowing
        # cancellation when a provider fails before the watcher starts.
        while True:
            if (await request.receive())["type"] == "http.disconnect":
                return
    watcher = asyncio.create_task(watch())
    try:
        done, _ = await asyncio.wait((task, watcher), return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            return await task
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise HTTPException(499, "voice_interrupted")
    finally:
        task.cancel()
        watcher.cancel()
        await asyncio.gather(task, watcher, return_exceptions=True)


def register_voice_routes(app, exists):
    @app.get("/api/voice")
    async def voice_status(request: Request):
        return await app.state.voice.status(request.state.user["id"])

    @app.post("/api/sessions/{session_id}/voice/transcribe")
    async def transcribe(session_id: str, request: Request, language: str = ""):
        await exists(session_id, request)
        media_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
        if media_type not in AUDIO_TYPES or (language and not re.fullmatch("[a-z]{2}", language)):
            raise HTTPException(400, "voice_invalid_audio")
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > MAX_AUDIO_BYTES:
                raise HTTPException(413, "voice_audio_too_large")
        if len(content) < 44:
            raise HTTPException(400, "voice_invalid_audio")
        return await _disconnectable(request, app.state.voice.perform(request.state.user["id"], session_id, "transcribe", bytes(content), media_type=media_type, language=language))

    @app.post("/api/sessions/{session_id}/voice/speech")
    async def speech(session_id: str, request: Request, body: SpeechInput):
        await exists(session_id, request)
        result = await _disconnectable(request, app.state.voice.perform(request.state.user["id"], session_id, "speech", body.text))
        return Response(result, media_type="audio/wav" if result.startswith(b"RIFF") else "audio/mpeg", headers={"Cache-Control": "no-store"})
