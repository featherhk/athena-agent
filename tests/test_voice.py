import asyncio
import io
import json
import wave

import httpx
import pytest

from athena.auth import Auth
from athena.errors import ServiceError
from athena.voice import VoiceConfig, VoiceService, initialize_voice, audio_duration


def wav():
    file = io.BytesIO()
    with wave.open(file, 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
        audio.writeframes(b'\0\0' * 8000)
    return file.getvalue()


async def identity(store):
    await initialize_voice(store)
    user, _ = await Auth(store).register('voice-tester', 'voice-test-password')
    return user['id'], await store.session(user_id=user['id'])


class Billing:
    def __init__(self):
        self.reservations = []; self.settlements = []

    async def reserve(self, *args):
        self.reservations.append(args)
        return args[1]

    async def settle(self, reservation, **kwargs):
        self.settlements.append((reservation, kwargs))


async def test_voice_uses_owned_request_ledger_without_recordings_or_transcripts(store):
    owner, session = await identity(store)
    billing = Billing(); received = []
    def provider(request):
        received.append(request)
        if request.url.path.endswith('transcriptions'):
            assert b'utterance.wav' in request.content
            return httpx.Response(200, json={'text': 'private voice transcript', 'usage': {'input_tokens': 3, 'total_tokens': 8, 'secret': 'never-store'}})
        return httpx.Response(200, content=b'fake-mp3', headers={'content-type': 'audio/mpeg'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        voice = VoiceService(VoiceConfig(api_key='voice-test-key', hosted=True), store, billing=billing, client=client)
        result = await voice.perform(owner, session, 'transcribe', wav())
        assert result['text'] == 'private voice transcript'
        assert await voice.perform(owner, session, 'speech', 'private voice response') == b'fake-mp3'
    assert len(billing.reservations) == len(billing.settlements) == 2
    assert billing.reservations[0][-1]['seconds'] == .5
    assert all(item[1]['status'] == 'complete' for item in billing.settlements)
    rows = await store.rows('SELECT * FROM voice_requests')
    assert len(rows) == 2 and all(row['status'] == 'complete' for row in rows)
    serialized = json.dumps(rows)
    assert 'private voice' not in serialized and 'voice-test-key' not in serialized and 'never-store' not in serialized
    assert all(request.headers['authorization'] == 'Bearer voice-test-key' for request in received)


async def test_hosted_audio_without_billing_fails_closed(store):
    voice = VoiceService(VoiceConfig(api_key='test', hosted=True), store)
    try:
        assert voice.diagnostic()['available'] is False
        with pytest.raises(ServiceError, match='not configured'):
            await voice.perform('no-user', 'no-session', 'speech', 'test')
    finally:
        await voice.close()


async def test_voice_cancellation_records_uncertain_outcome_and_releases_slot(store):
    owner, session = await identity(store)
    started = asyncio.Event(); billing = Billing()
    async def provider(request):
        started.set(); await asyncio.Event().wait()
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        voice = VoiceService(VoiceConfig(api_key='test'), store, client=client, billing=billing)
        task = asyncio.create_task(voice.perform(owner, session, 'speech', 'Test'))
        await started.wait()
        with pytest.raises(ServiceError) as error:
            await voice.perform(owner, session, 'speech', 'Duplicate')
        assert error.value.code == 'voice_busy'
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert not voice.active
    assert billing.settlements[0][1]['status'] == 'interrupted'
    assert (await store.rows('SELECT status FROM voice_requests')) == [{'status': 'interrupted'}]


async def test_provider_error_body_never_leaks(store):
    owner, session = await identity(store)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(401, text='secret-key private-server-debug'))) as client:
        voice = VoiceService(VoiceConfig(api_key='test'), store, client=client)
        with pytest.raises(ServiceError) as error:
            await voice.perform(owner, session, 'speech', 'Test')
    assert 'secret' not in str(error.value)
    assert 'private-server' not in json.dumps(await store.rows('SELECT * FROM voice_requests'))


async def test_dashscope_native_adapter_upgrades_oss_and_never_forwards_key(store):
    owner, session = await identity(store); requests = []
    def provider(request):
        requests.append(request)
        if request.method == 'GET':
            assert request.url.scheme == 'https'
            assert 'authorization' not in request.headers
            return httpx.Response(200, content=wav())
        payload = json.loads(request.content)
        if request.url.path.endswith('chat/completions'):
            assert payload['messages'][0]['content'][0]['input_audio']['data'].startswith('data:audio/wav;base64,')
            return httpx.Response(200, json={'choices': [{'message': {'content': 'hello'}}], 'usage': {'total_tokens': 4}})
        assert payload['input'] == {'text': 'hello', 'voice': 'Cherry', 'language_type': 'Auto'}
        return httpx.Response(200, json={'output': {'audio': {'url': 'http://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/voice.wav?signature=opaque'}}, 'usage': {'characters': 5}})
    config = VoiceConfig(provider='dashscope', api_key='test', base_url='https://dashscope.aliyuncs.com/api/v1', transcription_model='qwen3-asr-flash', speech_model='qwen3-tts-flash', voice='Cherry')
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        voice = VoiceService(config, store, client=client)
        assert (await voice.perform(owner, session, 'transcribe', wav()))['text'] == 'hello'
        assert await voice.perform(owner, session, 'speech', 'hello') == wav()
    assert len(requests) == 3


async def test_dashscope_audio_url_cannot_become_arbitrary_server_fetch(store):
    owner, session = await identity(store)
    calls = []
    def provider(request):
        calls.append(request)
        return httpx.Response(200, json={'output': {'audio': {'url': 'http://169.254.169.254/latest/meta-data'}}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        voice = VoiceService(VoiceConfig(provider='dashscope', api_key='test'), store, client=client)
        with pytest.raises(ServiceError):
            await voice.perform(owner, session, 'speech', 'hello')
    assert len(calls) == 1


def test_wav_validation_rejects_truncation_and_config_hides_key():
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        audio_duration(wav()[:-20])
    assert audio_duration(wav()) == .5
    assert 'my-private-key' not in repr(VoiceConfig(api_key='my-private-key'))
    assert not VoiceConfig(api_key='test', base_url='https://key@host.invalid/v1').valid()


async def test_voice_restart_marks_inflight_metadata_without_replaying(store):
    owner, session = await identity(store)
    await store.execute("INSERT INTO voice_requests (id,user_id,session_id,operation,model,status,input_units,created_at) VALUES ('pending',?,?,'speech','test','running',5,'2026-01-01')", (owner, session))
    await initialize_voice(store)
    assert (await store.rows("SELECT status,error_code FROM voice_requests WHERE id='pending'")) == [{'status': 'interrupted', 'error_code': 'restarted'}]


async def test_voice_http_routes_enforce_login_session_ownership_and_audio_bounds(config):
    from athena.api import create_app
    from athena.auth import COOKIE
    app = create_app(config)
    provider_calls = []
    def provider(request):
        provider_calls.append(request)
        return httpx.Response(200, json={'text': 'hello', 'usage': {'seconds': 1}})
    async with app.router.lifespan_context(app):
        owner, token = await app.state.auth.register('voice-owner', 'test-password')
        other, other_token = await app.state.auth.register('voice-other', 'test-password')
        session = await app.state.store.session(user_id=owner['id'])
        await app.state.voice.close()
        app.state.voice = VoiceService(VoiceConfig(api_key='test'), app.state.store, client=httpx.AsyncClient(transport=httpx.MockTransport(provider)))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317') as client:
            assert (await client.get('/api/voice')).status_code == 401
            client.cookies.set(COOKIE, other_token)
            assert (await client.post(f'/api/sessions/{session}/voice/transcribe', content=wav(), headers={'content-type': 'audio/wav'})).status_code == 404
            client.cookies.set(COOKIE, token)
            assert (await client.get('/api/voice')).json()['available'] is True
            assert (await client.post(f'/api/sessions/{session}/voice/transcribe', content=b'x' * (4 * 1024 * 1024 + 1), headers={'content-type': 'audio/wav'})).status_code == 413
            assert (await client.post(f'/api/sessions/{session}/voice/transcribe', content=b'x' * 44, headers={'content-type': 'audio/wav'})).status_code == 400
            assert not provider_calls
            result = await client.post(f'/api/sessions/{session}/voice/transcribe', content=wav(), headers={'content-type': 'audio/wav'})
            assert result.status_code == 200 and result.json()['text'] == 'hello'
            assert len(provider_calls) == 1
