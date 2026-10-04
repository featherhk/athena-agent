"""Operator-only audio routing; all credentials and endpoints here are fixtures."""
import asyncio
import io
import json
import os
import wave

import httpx
import pytest

from athena.auth import Auth, COOKIE
from athena.api import create_app
from athena.gateway import GatewayConfig, create_gateway
from athena.voice import VoiceConfig, VoiceService, initialize_voice


@pytest.fixture(autouse=True)
def isolated_audio_env(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("ATHENA_AUDIO_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("ATHENA_DEPLOYMENT", "local")


def configure(monkeypatch, **values):
    for key, value in values.items():
        monkeypatch.setenv("ATHENA_AUDIO_" + key, value)
    return VoiceConfig.from_env()


def wav():
    file = io.BytesIO()
    with wave.open(file, "wb") as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 8000)
    return file.getvalue()


@pytest.mark.parametrize("provider", ["openai", "dashscope"])
def test_shared_configuration_remains_compatible(monkeypatch, provider):
    config = configure(monkeypatch, PROVIDER=provider, API_KEY="shared-fixture")
    assert config.valid()
    assert all(config.endpoint(kind).api_key == "shared-fixture" for kind in ("transcribe", "speech"))
    assert all(config.endpoint(kind).provider == provider for kind in ("transcribe", "speech"))
    if provider == "dashscope":
        assert config.endpoint("transcribe").base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        assert config.endpoint("speech").voice == "Cherry"
    else:
        assert config.endpoint("speech").base_url == "https://api.openai.com/v1"
        assert config.endpoint("speech").voice == "coral"


def test_operation_credentials_files_and_models_override_legacy_without_leaking_repr(tmp_path, monkeypatch):
    keyfile = tmp_path / "voice-key"
    keyfile.write_text("  separate-stt-fixture\n")
    config = configure(monkeypatch, API_KEY="legacy-fixture", STT_API_KEY_FILE=str(keyfile),
                       TTS_PROVIDER="dashscope", TTS_API_KEY=" separate-tts-fixture ",
                       STT_MODEL="custom-asr", TTS_MODEL="custom-tts", TTS_VOICE="Cherry")
    assert config.valid()
    assert config.endpoint("transcribe").api_key == "separate-stt-fixture"
    assert config.endpoint("speech").api_key == "separate-tts-fixture"
    assert config.transcription_model == "custom-asr" and config.speech_model == "custom-tts"
    assert config.endpoint("speech").base_url == "https://dashscope.aliyuncs.com/api/v1"
    assert all(secret not in repr(config) for secret in ("legacy-fixture", "separate-stt-fixture", "separate-tts-fixture"))


@pytest.mark.parametrize("kind", ["STT", "TTS"])
@pytest.mark.parametrize("invalid", ["empty", "missing-file", "binary-file"])
def test_explicit_invalid_operation_key_does_not_fall_back(tmp_path, monkeypatch, kind, invalid):
    values = {"API_KEY": "shared-fixture"}
    if invalid == "empty":
        values[kind + "_API_KEY"] = "  "
    else:
        path = tmp_path / "invalid-key"
        if invalid == "binary-file":
            path.write_bytes(b"\xff\xfe")
        values[kind + "_API_KEY_FILE"] = str(path)
    config = configure(monkeypatch, **values)
    assert not config.valid()
    assert config.endpoint("transcribe" if kind == "STT" else "speech").api_key == ""


@pytest.mark.parametrize("kind", ["STT", "TTS"])
@pytest.mark.parametrize("values", [
    {"BASE_URL": "https://other.example/v1"},
    {"BASE_URL": "https://api.openai.com:8443/v1"},
    {"BASE_URL": "https://api.openai.com:0/v1"},
    {"PROVIDER": "dashscope"},
    {"PROVIDER": "dashscope", "BASE_URL": "https://api.openai.com/v1"},
])
def test_shared_key_never_follows_changed_host_port_or_protocol(monkeypatch, kind, values):
    config = configure(monkeypatch, API_KEY="shared-fixture", **{kind + "_" + key: value for key, value in values.items()})
    assert not config.valid()
    assert config.endpoint("transcribe" if kind == "STT" else "speech").api_key == ""


def test_same_origin_path_override_can_reuse_shared_key(monkeypatch):
    config = configure(monkeypatch, API_KEY="shared-fixture", STT_BASE_URL="https://API.OPENAI.COM:443/asr/v1")
    assert config.valid()
    assert config.endpoint("transcribe").api_key == "shared-fixture"


def test_legacy_official_dashscope_workspace_only_inherits_when_provider_is_implicit(monkeypatch):
    config = configure(monkeypatch, PROVIDER="dashscope", API_KEY="shared-fixture",
                       STT_BASE_URL="https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
    assert config.valid() and config.endpoint("transcribe").api_key == "shared-fixture"
    monkeypatch.setenv("ATHENA_AUDIO_STT_PROVIDER", "dashscope")
    assert not VoiceConfig.from_env().valid()
    monkeypatch.setenv("ATHENA_AUDIO_STT_API_KEY", "separate-stt-fixture")
    assert VoiceConfig.from_env().valid()


@pytest.mark.parametrize("target", [
    "https://workspace.cn-beijing.maas.aliyuncs.com.evil.example/v1",
    "https://notmaas.aliyuncs.com/v1",
    "https://maas.aliyuncs.com/v1",
    "http://workspace.cn-beijing.maas.aliyuncs.com/v1",
    "https://workspace.cn-beijing.maas.aliyuncs.com:443/v1",
    "https://user@workspace.cn-beijing.maas.aliyuncs.com/v1",
    "https://workspace.cn-beijing.maas.aliyuncs.com/v1?key=bad",
    "https://[malformed",
    "https://workspace.cn-beijing.maas.aliyuncs.com:wrong/v1",
])
def test_legacy_workspace_exception_has_strict_origin_bounds(monkeypatch, target):
    config = configure(monkeypatch, PROVIDER="dashscope", API_KEY="shared-fixture", STT_BASE_URL=target)
    assert not config.valid() and config.endpoint("transcribe").api_key == ""


async def test_local_mixed_adapters_route_separate_keys_models_and_protocols(store, monkeypatch):
    config = configure(monkeypatch, STT_PROVIDER="openai", STT_BASE_URL="https://asr.example/v1", STT_API_KEY="asr-fixture",
                       STT_MODEL="fixture-asr", TTS_PROVIDER="dashscope", TTS_API_KEY="tts-fixture",
                       TTS_MODEL="fixture-tts", TTS_VOICE="Cherry")
    await initialize_voice(store)
    owner, _ = await Auth(store).register("split-voice", "test-password")
    session = await store.session(user_id=owner["id"])
    requests = []
    def provider(request):
        requests.append(request)
        if request.url.host == "asr.example":
            assert request.url.path == "/v1/audio/transcriptions"
            assert request.headers["authorization"] == "Bearer asr-fixture"
            assert b"fixture-asr" in request.content and b"utterance.wav" in request.content
            return httpx.Response(200, json={"text": "hello", "usage": {"seconds": .5}})
        if request.method == "GET":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=wav())
        assert request.url.host == "dashscope.aliyuncs.com"
        assert request.headers["authorization"] == "Bearer tts-fixture"
        assert json.loads(request.content) == {"model": "fixture-tts", "input": {"text": "hello", "voice": "Cherry", "language_type": "Auto"}}
        return httpx.Response(200, json={"output": {"audio": {"url": "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/test.wav"}}, "usage": {"characters": 5}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        voice = VoiceService(config, store, client=client)
        assert voice.diagnostic()["unsupported_languages"] == ["ar"]
        assert (await voice.perform(owner["id"], session, "transcribe", wav()))["text"] == "hello"
        assert await voice.perform(owner["id"], session, "speech", "hello") == wav()
        diagnostic = json.dumps(voice.diagnostic())
    rows = await store.rows("SELECT * FROM voice_requests")
    assert {row["model"] for row in rows} == {"fixture-asr", "fixture-tts"}
    assert len(requests) == 3
    assert all(secret not in diagnostic + json.dumps(rows) for secret in ("asr-fixture", "tts-fixture", "asr.example"))


async def test_local_native_recognition_and_http_speech_use_resolved_defaults(store, monkeypatch):
    config = configure(monkeypatch, PROVIDER="dashscope", API_KEY="native-fixture", TTS_PROVIDER="openai",
                       TTS_BASE_URL="https://speech.example/v1", TTS_API_KEY="speech-fixture")
    await initialize_voice(store)
    owner, _ = await Auth(store).register("reverse-voice", "test-password")
    session = await store.session(user_id=owner["id"])
    calls = []
    def provider(request):
        calls.append(request)
        data = json.loads(request.content)
        if request.url.host == "dashscope.aliyuncs.com":
            assert request.headers["authorization"] == "Bearer native-fixture"
            assert request.url.path == "/compatible-mode/v1/chat/completions"
            assert data["model"] == "qwen3-asr-flash"
            return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}], "usage": {"seconds": .5}})
        assert request.url.host == "speech.example" and request.url.path == "/v1/audio/speech"
        assert request.headers["authorization"] == "Bearer speech-fixture"
        assert data == {"model": "gpt-4o-mini-tts", "voice": "coral", "input": "hello", "response_format": "mp3"}
        return httpx.Response(200, content=b"fixture-mp3", headers={"content-type": "audio/mpeg"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        voice = VoiceService(config, store, client=client)
        assert voice.diagnostic()["unsupported_languages"] == []
        assert (await voice.perform(owner["id"], session, "transcribe", wav()))["text"] == "hello"
        assert await voice.perform(owner["id"], session, "speech", "hello") == b"fixture-mp3"
    assert len(calls) == 2


@pytest.mark.parametrize("missing", ["STT", "TTS"])
async def test_incomplete_pair_disables_http_availability_before_audio_calls(config, monkeypatch, missing):
    configure(monkeypatch, STT_API_KEY="stt-fixture", TTS_API_KEY="tts-fixture")
    monkeypatch.delenv("ATHENA_AUDIO_" + missing + "_API_KEY")
    app = create_app(config)
    async with app.router.lifespan_context(app):
        user, token = await app.state.auth.register("voice-incomplete", "test-password")
        session = await app.state.store.session(user_id=user["id"])
        calls = []
        await app.state.voice.client.aclose()
        app.state.voice.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(500)))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:4317") as client:
            client.cookies.set(COOKIE, token)
            status = (await client.get("/api/voice")).json()
            assert status["available"] is False and status["code"] == "voice_unavailable"
            response = await client.post(f"/api/sessions/{session}/voice/speech", json={"text": "hello"})
            assert response.status_code != 200 and response.json()["code"] == "voice_unavailable"
        assert not calls
        assert not await app.state.store.rows("SELECT * FROM voice_requests")


@pytest.mark.parametrize("unmetered", ["STT", "TTS"])
async def test_hosted_mixed_providers_fail_closed_despite_legacy_dashscope_tariff(tmp_path, monkeypatch, unmetered):
    configure(monkeypatch, PROVIDER="dashscope", API_KEY="shared-fixture", STT_USD_PER_SECOND=".00001", TTS_USD_PER_10K_CHARACTERS=".1",
              **{unmetered + "_PROVIDER": "openai", unmetered + "_API_KEY": "separate-fixture"})
    app = create_gateway(GatewayConfig(data_dir=tmp_path / "gateway"))
    async with app.router.lifespan_context(app):
        user, token = await app.state.auth.register("mixed-meter", "test-password")
        await app.state.credits.grant(user["id"], 1000, "test", "test")
        assert app.state.voice.config.valid() and app.state.voice.billing is None
        before = await app.state.credits.balance(user["id"])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://accounts.invalid") as client:
            headers = {"Authorization": "Bearer " + token, "X-Athena-Session-ID": "a" * 32}
            assert (await client.get("/v1/voice", headers=headers)).json()["available"] is False
            failed = await client.post("/v1/voice/speech", headers=headers, json={"text": "hello"})
            assert failed.json()["code"] == "voice_unavailable"
        assert await app.state.credits.balance(user["id"]) == before
        assert not await app.state.store.rows("SELECT * FROM voice_requests")


async def test_hosted_split_dashscope_endpoints_meter_actual_usage_with_separate_keys(tmp_path, monkeypatch):
    configure(monkeypatch, STT_PROVIDER="dashscope", STT_API_KEY="native-asr-fixture",
              STT_BASE_URL="https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
              TTS_PROVIDER="dashscope", TTS_API_KEY="native-tts-fixture",
              STT_USD_PER_SECOND=".00001", TTS_USD_PER_10K_CHARACTERS=".1")
    app = create_gateway(GatewayConfig(data_dir=tmp_path / "gateway"))
    calls = []
    def provider(request):
        calls.append(request)
        if request.url.host == "workspace.cn-beijing.maas.aliyuncs.com":
            assert request.headers["authorization"] == "Bearer native-asr-fixture"
            return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}], "usage": {"seconds": .5}})
        if request.method == "GET":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=wav())
        assert request.headers["authorization"] == "Bearer native-tts-fixture"
        return httpx.Response(200, json={"output": {"audio": {"url": "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/test.wav"}}, "usage": {"characters": 5}})
    async with app.router.lifespan_context(app):
        await app.state.voice.client.aclose()
        app.state.voice.client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        user, token = await app.state.auth.register("split-meter", "test-password")
        await app.state.credits.grant(user["id"], 1000, "test", "test")
        # Resolved endpoints determine billing, not the shared default "openai".
        assert app.state.voice.config.provider == "openai" and app.state.voice.billing is not None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://accounts.invalid") as client:
            headers = {"Authorization": "Bearer " + token, "X-Athena-Session-ID": "a" * 32}
            assert (await client.get("/v1/voice", headers=headers)).json()["available"] is True
            audio_headers = {**headers, "Content-Type": "audio/wav"}
            assert (await client.post("/v1/voice/transcribe", headers=audio_headers, content=wav())).json()["text"] == "hello"
            assert (await client.post("/v1/voice/speech", headers=headers, json={"text": "hello"})).content == wav()
        balance = await app.state.credits.balance(user["id"])
        assert balance["balance_micros"] == 945 and balance["held_micros"] == 0
        assert len(calls) == 3


async def test_agent_subprocess_does_not_inherit_split_voice_credentials(engine, store, monkeypatch):
    for prefix in ("ATHENA_AUDIO", "ATHENA_AUDIO_STT", "ATHENA_AUDIO_TTS"):
        monkeypatch.setenv(prefix + "_API_KEY", "fixture-operator-secret")
        monkeypatch.setenv(prefix + "_API_KEY_FILE", "/private/fixture-key-file")
    captured = []
    class Process:
        returncode = 0
        def __init__(self):
            self.stdout = asyncio.StreamReader()
            self.stdout.feed_eof()
        async def wait(self):
            return 0
    async def spawn(*args, **kwargs):
        captured.append(kwargs["env"])
        return Process()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    session = await store.session()
    await engine.tools.shell({"command": "true"}, session, None)
    assert captured and not any(key.startswith("ATHENA_AUDIO_") for key in captured[0])
    assert "fixture-operator-secret" not in json.dumps(captured)
