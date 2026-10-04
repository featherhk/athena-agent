"""Replaceable audio transport. Audio stays in memory; only request metadata is stored.

The ordinary session actor owns conversation, tool execution and interruption.
Voice does not create a second model history or replay actions.
"""
import asyncio
import base64
from dataclasses import dataclass, field, replace
import json
import io
import os
from pathlib import Path
import re
import struct
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
class VoiceEndpointConfig:
    provider: str
    base_url: str
    api_key: str = field(repr=False)
    model: str
    voice: str = ""

    def valid(self):
        return bool(self.api_key and self.model) and self.provider in {"openai", "dashscope"} and VoiceConfig.valid_url(self.base_url)


def _audio_key(prefix):
    """None means unset; an explicitly empty/unreadable key must stay disabled."""
    key, key_file = os.getenv(prefix + "_API_KEY", ""), os.getenv(prefix + "_API_KEY_FILE", "")
    if key.strip():
        return key.strip()
    if key_file:
        try:
            return Path(key_file).expanduser().read_text().strip()
        except (OSError, UnicodeError):
            return ""
    return "" if any(prefix + suffix in os.environ for suffix in ("_API_KEY", "_API_KEY_FILE")) else None


def _same_audio_origin(left, right):
    if not VoiceConfig.valid_url(left) or not VoiceConfig.valid_url(right):
        return False
    def origin(value):
        url = urlparse(value)
        return url.scheme, url.hostname, url.port if url.port is not None else (443 if url.scheme == "https" else 80)
    return origin(left) == origin(right)


def _legacy_dashscope_workspace(base, target):
    """Preserve the documented pre-split official DashScope workspace setup."""
    if not VoiceConfig.valid_url(base) or not VoiceConfig.valid_url(target):
        return False
    source, destination = urlparse(base), urlparse(target)
    return (source.scheme == destination.scheme == "https" and source.port is destination.port is None
            and source.hostname in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"}
            and destination.hostname.endswith(".maas.aliyuncs.com"))


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
    stt: VoiceEndpointConfig | None = None
    tts: VoiceEndpointConfig | None = None

    @classmethod
    def from_env(cls):
        provider = os.getenv("ATHENA_AUDIO_PROVIDER", "openai")
        dashscope = provider == "dashscope"
        config = cls(
            base_url=os.getenv("ATHENA_AUDIO_BASE_URL", "https://dashscope.aliyuncs.com/api/v1" if dashscope else "https://api.openai.com/v1").rstrip("/"),
            api_key=_audio_key("ATHENA_AUDIO") or "",
            transcription_model=os.getenv("ATHENA_AUDIO_STT_MODEL", "qwen3-asr-flash" if dashscope else "gpt-4o-mini-transcribe"),
            speech_model=os.getenv("ATHENA_AUDIO_TTS_MODEL", "qwen3-tts-flash" if dashscope else "gpt-4o-mini-tts"),
            voice=os.getenv("ATHENA_AUDIO_VOICE", "Cherry" if dashscope else "coral"),
            hosted=os.getenv("ATHENA_DEPLOYMENT", "local") == "hosted",
            provider=provider, stt_base_url=os.getenv("ATHENA_AUDIO_STT_BASE_URL", "").rstrip("/"),
        )

        def operation(kind, prefix):
            selected = os.getenv(prefix + "_PROVIDER", provider)
            legacy = config.endpoint(kind)
            same_provider = selected == provider
            native = selected == "dashscope"
            default_url = ("https://dashscope.aliyuncs.com/compatible-mode/v1" if kind == "transcribe"
                           else "https://dashscope.aliyuncs.com/api/v1") if native else "https://api.openai.com/v1"
            default_model = ("qwen3-asr-flash" if kind == "transcribe" else "qwen3-tts-flash") if native else (
                "gpt-4o-mini-transcribe" if kind == "transcribe" else "gpt-4o-mini-tts")
            base_url = os.getenv(prefix + "_BASE_URL", legacy.base_url if same_provider else default_url).rstrip("/")
            key = _audio_key(prefix)
            if key is None:
                inherit = same_provider and (_same_audio_origin(config.base_url, base_url) or (
                    kind == "transcribe" and native and prefix + "_PROVIDER" not in os.environ
                    and _legacy_dashscope_workspace(config.base_url, base_url)))
                key = config.api_key if inherit else ""
            return VoiceEndpointConfig(
                provider=selected,
                base_url=base_url,
                api_key=key,
                model=os.getenv(prefix + "_MODEL", legacy.model if same_provider else default_model),
                voice=os.getenv("ATHENA_AUDIO_TTS_VOICE", os.getenv("ATHENA_AUDIO_VOICE", legacy.voice if same_provider else "Cherry" if native else "coral")) if kind == "speech" else "",
            )
        stt, tts = operation("transcribe", "ATHENA_AUDIO_STT"), operation("speech", "ATHENA_AUDIO_TTS")
        return replace(config, stt=stt, tts=tts, transcription_model=stt.model, speech_model=tts.model, voice=tts.voice)

    def endpoint(self, operation):
        if operation == "transcribe":
            root = self.stt_base_url or (self.base_url.removesuffix("/api/v1") + "/compatible-mode/v1" if self.provider == "dashscope" else self.base_url)
            return self.stt or VoiceEndpointConfig(self.provider, root, self.api_key, self.transcription_model)
        if operation == "speech":
            return self.tts or VoiceEndpointConfig(self.provider, self.base_url, self.api_key, self.speech_model, self.voice)
        raise ValueError("Unknown voice operation")

    def valid(self):
        return all(self.endpoint(kind).valid() for kind in ("transcribe", "speech"))

    @staticmethod
    def valid_url(value):
        try:
            url = urlparse(value)
            # Accessing port validates its syntax and range before origin checks.
            url.port
        except ValueError:
            return False
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


def normalize_speech_wav(content):
    """Validate bounded PCM output; repair only the observed DashScope stream header.

    Call after a successful, complete HTTP download. Streaming headers cannot
    prove that a provider produced its entire intended utterance; finite lengths
    and partial PCM frames are still checked rather than silently truncated.
    """
    if not 44 <= len(content) <= MAX_SPEECH_BYTES or content[:4] != b"RIFF" or content[8:12] != b"WAVE":
        raise ValueError("invalid speech WAV")

    def pcm(format_bytes, data_size):
        if len(format_bytes) not in (16, 18) or (len(format_bytes) == 18 and format_bytes[16:] != b"\0\0"):
            raise ValueError("unsupported speech PCM")
        encoding, channels, rate, byte_rate, alignment, bits = struct.unpack_from("<HHIIHH", format_bytes)
        if (encoding != 1 or channels not in (1, 2) or bits != 16 or not 16000 <= rate <= 96000
                or alignment != channels * 2 or byte_rate != rate * alignment
                or not data_size or data_size % alignment):
            raise ValueError("invalid speech PCM")

    declared = struct.unpack_from("<I", content, 4)[0]
    # This exact paired sentinel was observed on complete Qwen TTS downloads.
    # Do not repair arbitrary oversized lengths: those can be real truncation.
    if (declared == 0x7FFFFFBF and content[12:20] == b"fmt \x10\0\0\0"
            and content[36:40] == b"data" and struct.unpack_from("<I", content, 40)[0] == 0x7FFFFF9B):
        pcm(content[20:36], len(content) - 44)
        result = bytearray(content)
        struct.pack_into("<I", result, 4, len(result) - 8)
        struct.pack_into("<I", result, 40, len(result) - 44)
        return bytes(result)

    if declared != len(content) - 8:
        raise ValueError("truncated or invalid speech WAV")
    offset, format_bytes, data_size = 12, None, None
    # Bound parsing work as well as bytes, including files with empty chunks.
    for _ in range(64):
        if offset == len(content):
            break
        if offset + 8 > len(content):
            raise ValueError("invalid speech WAV chunks")
        kind, size = struct.unpack_from("<4sI", content, offset)
        start = offset + 8
        end = start + size
        offset = end + (size % 2)
        if offset > len(content):
            raise ValueError("truncated speech WAV chunk")
        if kind == b"fmt ":
            if format_bytes is not None or size not in (16, 18):
                raise ValueError("invalid speech WAV format")
            format_bytes = content[start:end]
        elif kind == b"data":
            if format_bytes is None or data_size is not None:
                raise ValueError("invalid speech WAV data")
            data_size = size
    if offset != len(content) or format_bytes is None or data_size is None:
        raise ValueError("invalid speech WAV chunks")
    pcm(format_bytes, data_size)
    return bytes(content)


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
        return {"available": configured, "code": None if configured else "voice_unavailable", "transport": "transcription-speech", "raw_audio_retained": False, "unsupported_languages": ["ar"] if self.config.endpoint("speech").provider == "dashscope" else []}

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
        provider = self.config.endpoint(operation)
        model = provider.model
        status, code, output_bytes = "failed", "voice_provider_error", 0
        recorded = False
        try:
            if self.billing:
                reservation = await self.billing.reserve(user_id, request_id, operation, model, units)
            await self.store.execute("INSERT INTO voice_requests (id,user_id,session_id,operation,model,status,input_units,created_at) VALUES (?,?,?,?,?,'running',?,?)", (request_id, user_id, session_id, operation, model, len(payload), now()))
            recorded = True
            headers = {"Authorization": "Bearer " + provider.api_key}
            if operation == "transcribe":
                data = {"model": model, "response_format": "json"}
                if language:
                    data["language"] = language
                kwargs = {"data": data, "files": {"file": ("utterance." + AUDIO_TYPES[media_type], payload, media_type)}}
                endpoint, limit = "transcriptions", 256 * 1024
            else:
                kwargs = {"json": {"model": model, "input": payload, "voice": provider.voice, "response_format": "mp3"}}
                endpoint, limit = "speech", MAX_SPEECH_BYTES
            url = provider.base_url + "/audio/" + endpoint
            if provider.provider == "dashscope":
                limit = 256 * 1024
                if operation == "transcribe":
                    url = provider.base_url + "/chat/completions"
                    kwargs = {"json": {"model": model, "messages": [{"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": "data:audio/wav;base64," + base64.b64encode(payload).decode("ascii")}}]}], "stream": False}}
                else:
                    url = provider.base_url + "/services/aigc/multimodal-generation/generation"
                    kwargs = {"json": {"model": model, "input": {"text": payload, "voice": provider.voice, "language_type": "Auto"}}}
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
                    text = result["choices"][0]["message"]["content"] if provider.provider == "dashscope" else result.get("text")
                    if not isinstance(text, str) or len(text) > 30000:
                        raise ValueError("invalid transcription")
                    raw_usage = result.get("usage")
                    # Whitelist numeric usage fields; providers may return more
                    # fields than the documented usage contract.
                    usage = {k: raw_usage[k] for k in ("input_tokens", "output_tokens", "total_tokens", "seconds") if isinstance(raw_usage, dict) and isinstance(raw_usage.get(k), (int, float)) and not isinstance(raw_usage.get(k), bool)}
                    value = {"text": text.strip(), "request_id": request_id}
                elif provider.provider == "dashscope":
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
                    raw_characters = result.get("usage", {}).get("characters")
                    usage = {"characters": raw_characters} if type(raw_characters) is int and raw_characters >= 0 else None
                    value = normalize_speech_wav(audio)
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
