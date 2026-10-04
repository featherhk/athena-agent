"""Synthetic PCM fixtures only; provider recordings do not belong in the repo."""
import io
import json
import struct
import wave

import httpx
import pytest

from athena.auth import Auth
from athena.errors import ServiceError
from athena.voice import VoiceConfig, VoiceService, audio_duration, initialize_voice, normalize_speech_wav


def wav():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000)
        audio.writeframes(b"\x01\0\xff\xff" * 6000)
    return buffer.getvalue()


def placeholders(value=None):
    result = bytearray(wav() if value is None else value)
    struct.pack_into("<I", result, 4, 0x7FFFFFBF)
    struct.pack_into("<I", result, 40, 0x7FFFFF9B)
    return bytes(result)


def riff(chunks):
    data = b"WAVE" + b"".join(kind + struct.pack("<I", len(body)) + body + (b"\0" if len(body) % 2 else b"") for kind, body in chunks)
    return b"RIFF" + struct.pack("<I", len(data)) + data


def test_observed_stream_placeholder_becomes_exact_finite_wav_without_changing_pcm():
    original = placeholders()
    output = normalize_speech_wav(original)
    assert output == wav() and original == placeholders()
    assert output[44:] == original[44:]
    assert struct.unpack_from("<I", output, 4)[0] == len(output) - 8
    assert struct.unpack_from("<I", output, 40)[0] == len(output) - 44
    assert audio_duration(output) == .5
    with wave.open(io.BytesIO(output)) as audio:
        assert audio.getnframes() == 12000 and audio.getframerate() == 24000


def test_finite_pcm_and_bounded_metadata_chunks_are_preserved():
    assert normalize_speech_wav(wav()) == wav()
    original = riff([(b"JUNK", b"abc"), (b"fmt ", wav()[20:36]), (b"data", wav()[44:]), (b"LIST", b"demo")])
    assert normalize_speech_wav(original) == original and audio_duration(original) == .5


@pytest.mark.parametrize("offset,format,value", [
    (4, "I", 0xFFFFFFFF), (40, "I", 0xFFFFFFFF),
    (4, "I", 1000000), (40, "I", 1000000),
    (20, "H", 3), (22, "H", 0), (22, "H", 3), (24, "I", 0), (24, "I", 192000),
    (28, "I", 12345), (32, "H", 4), (34, "H", 8),
])
@pytest.mark.parametrize("streaming", [False, True])
def test_unknown_sentinels_and_invalid_pcm_are_rejected(offset, format, value, streaming):
    data = bytearray(placeholders() if streaming else wav())
    struct.pack_into("<" + format, data, offset, value)
    with pytest.raises(ValueError):
        normalize_speech_wav(data)


@pytest.mark.parametrize("removed", [1, 2, 20, 24000])
def test_actual_finite_truncation_is_never_repaired(removed):
    original = wav()
    with pytest.raises(ValueError):
        normalize_speech_wav(original[:-removed])
    # A rewritten outer length must not disguise the inner data truncation.
    modified = bytearray(original[:-removed])
    struct.pack_into("<I", modified, 4, len(modified) - 8)
    with pytest.raises(ValueError):
        normalize_speech_wav(modified)


@pytest.mark.parametrize("content", [placeholders()[:-1], placeholders()[:44], b"RIFF" + b"\0" * 40])
def test_partial_or_empty_stream_pcm_and_non_wave_are_rejected(content):
    with pytest.raises(ValueError):
        normalize_speech_wav(content)


@pytest.mark.parametrize("chunks", [
    [(b"data", wav()[44:]), (b"fmt ", wav()[20:36])],
    [(b"fmt ", wav()[20:36]), (b"fmt ", wav()[20:36]), (b"data", wav()[44:])],
    [(b"fmt ", wav()[20:36]), (b"data", wav()[44:]), (b"data", b"\0\0")],
    [(b"fmt ", wav()[20:36]), (b"data", b"\0\0\0")],
    [(b"fmt ", wav()[20:36]), (b"data", wav()[44:])] + [(b"JUNK", b"")] * 63,
])
def test_invalid_duplicate_unaligned_and_excessive_chunks_are_rejected(chunks):
    with pytest.raises(ValueError):
        normalize_speech_wav(riff(chunks))


def test_wav_byte_limit_also_applies_to_normalization(monkeypatch):
    monkeypatch.setattr("athena.voice.MAX_SPEECH_BYTES", len(wav()) - 1)
    with pytest.raises(ValueError):
        normalize_speech_wav(placeholders())


@pytest.mark.parametrize("outcome", ["complete", "truncated-wav", "transport-truncated"])
async def test_download_normalization_integrates_with_audit_and_uncertain_billing(store, outcome):
    await initialize_voice(store)
    user, _ = await Auth(store).register("wav-receipt", "test-password")
    session = await store.session(user_id=user["id"])
    settlements, calls = [], []
    class Billing:
        async def reserve(self, *args):
            return "held"
        async def settle(self, reservation, **kwargs):
            assert reservation == "held"
            settlements.append(kwargs)
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield placeholders()[:128]
            raise httpx.RemoteProtocolError("fixture truncated HTTP response")
    def provider(request):
        calls.append(request)
        if request.method == "GET":
            assert request.url.scheme == "https" and "authorization" not in request.headers
            if outcome == "transport-truncated":
                return httpx.Response(200, stream=BrokenStream())
            return httpx.Response(200, content=placeholders() if outcome == "complete" else wav()[:-2])
        assert request.headers["authorization"] == "Bearer fixture-key"
        return httpx.Response(200, json={"output": {"audio": {"url": "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/fixture.wav"}}, "usage": {"characters": 5}})
    config = VoiceConfig(provider="dashscope", api_key="fixture-key", base_url="https://dashscope.aliyuncs.com/api/v1")
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        service = VoiceService(config, store, billing=Billing(), client=client)
        if outcome == "complete":
            result = await service.perform(user["id"], session, "speech", "hello")
            assert result == wav() and audio_duration(result) == .5
        else:
            with pytest.raises(ServiceError) as failure:
                await service.perform(user["id"], session, "speech", "hello")
            assert failure.value.code == "voice_provider_error"
        assert not service.active
    assert len(calls) == 2 and len(settlements) == 1
    expected = "complete" if outcome == "complete" else "failed"
    assert settlements[0]["status"] == expected
    row = (await store.rows("SELECT * FROM voice_requests"))[0]
    assert row["status"] == expected
    if outcome == "complete":
        assert row["output_bytes"] == len(wav()) and json.loads(row["usage"]) == {"characters": 5}
    else:
        assert row["error_code"] == "voice_provider_error"
    assert "fixture-key" not in json.dumps(row)
