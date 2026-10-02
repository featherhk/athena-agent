from decimal import Decimal
import io
import json
import wave

import httpx

from athena.gateway import GatewayConfig, create_gateway
from athena.voice_gateway import VoiceTariff, MeteredVoice, CloudVoiceService
from athena.credits import Credits, initialize_credits
from athena.auth import Auth


def wav():
    output = io.BytesIO()
    with wave.open(output, 'wb') as file:
        file.setnchannels(1); file.setsampwidth(2); file.setframerate(16000); file.writeframes(b'\0\0' * 8000)
    return output.getvalue()


async def test_audio_gateway_blocks_unfunded_accounts_and_settles_reported_units(tmp_path, monkeypatch):
    for key, value in {
        'ATHENA_AUDIO_PROVIDER': 'dashscope', 'ATHENA_AUDIO_API_KEY': 'operator-audio-key',
        'ATHENA_AUDIO_STT_USD_PER_SECOND': '0.00001', 'ATHENA_AUDIO_TTS_USD_PER_10K_CHARACTERS': '0.1',
    }.items():
        monkeypatch.setenv(key, value)
    app = create_gateway(GatewayConfig(data_dir=tmp_path / 'cloud'))
    provider_calls = []
    def provider(request):
        provider_calls.append(request)
        if request.method == 'GET':
            assert 'authorization' not in request.headers
            return httpx.Response(200, content=wav())
        assert request.headers['authorization'] == 'Bearer operator-audio-key'
        return httpx.Response(200, json={'output': {'audio': {'url': 'https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/test.wav'}}, 'usage': {'characters': 5}})
    async with app.router.lifespan_context(app):
        await app.state.voice.client.aclose()
        app.state.voice.client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='https://accounts.invalid') as client:
            anonymous = await client.get('/v1/voice')
            assert anonymous.status_code == 401
            registered = (await client.post('/v1/auth/register', json={'username': 'voiceuser', 'password': 'a-long-test-password'})).json()
            headers = {'Authorization': 'Bearer ' + registered['token'], 'X-Athena-Session-ID': 'a' * 32}
            unfunded = await client.post('/v1/voice/speech', json={'text': 'hello'}, headers=headers)
            assert unfunded.status_code == 402
            assert not provider_calls
            await app.state.credits.grant(registered['user']['id'], 1000, 'test-credit', 'test')
            response = await client.post('/v1/voice/speech', json={'text': 'hello'}, headers=headers)
            assert response.status_code == 200 and response.content == wav()
            balance = await app.state.credits.balance(registered['user']['id'])
            assert balance['balance_micros'] == 950 and balance['held_micros'] == 0
            audits = await app.state.store.rows('SELECT * FROM voice_requests')
            assert len(audits) == 1 and audits[0]['user_id'] == registered['user']['id']
            assert 'operator-audio-key' not in response.text


async def test_missing_usage_and_unknown_outcomes_retain_audio_reservation(store):
    user, _ = await Auth(store).register('voice-ledger', 'test-password')
    await initialize_credits(store)
    credits = Credits(store)
    await credits.grant(user['id'], 1000, 'voice-credit', 'test')
    billing = MeteredVoice(credits, VoiceTariff(Decimal('.00001'), Decimal('.1')))
    reservation = await billing.reserve(user['id'], 'x' * 32, 'transcribe', 'qwen3-asr-flash', {'seconds': .5})
    await billing.settle(reservation, status='complete', usage={'total_tokens': 2})
    balance = await credits.balance(user['id'])
    assert balance['balance_micros'] == 1000 and balance['held_micros'] == 20
    reservation = await billing.reserve(user['id'], 'y' * 32, 'speech', 'qwen3-tts-flash', {'characters': 5})
    await billing.settle(reservation, status='interrupted', usage=None)
    assert (await credits.balance(user['id']))['held_micros'] == 120


async def test_worker_voice_proxy_uses_only_account_token_and_same_cloud_origin():
    class Accounts:
        base = 'https://accounts.invalid/v1'
        async def token(self, owner):
            assert owner == 'owner'
            return 'account-token'
    seen = []
    def gateway(request):
        seen.append(request)
        assert request.headers['authorization'] == 'Bearer account-token'
        assert request.headers['x-athena-session-id'] == 's' * 32
        assert request.url.host == 'accounts.invalid'
        assert json.loads(request.content) == {'text': 'hello'}
        return httpx.Response(200, content=wav(), headers={'content-type': 'audio/wav'})
    voice = CloudVoiceService(Accounts())
    await voice.client.aclose()
    voice.client = httpx.AsyncClient(transport=httpx.MockTransport(gateway))
    try:
        assert await voice.perform('owner', 's' * 32, 'speech', 'hello') == wav()
    finally:
        await voice.close()
    assert len(seen) == 1


def test_unconfigured_or_invalid_tariffs_never_enable_billing(monkeypatch):
    monkeypatch.delenv('ATHENA_AUDIO_STT_USD_PER_SECOND', raising=False)
    monkeypatch.delenv('ATHENA_AUDIO_TTS_USD_PER_10K_CHARACTERS', raising=False)
    assert VoiceTariff.from_env() is None
    monkeypatch.setenv('ATHENA_AUDIO_STT_USD_PER_SECOND', 'NaN')
    monkeypatch.setenv('ATHENA_AUDIO_TTS_USD_PER_10K_CHARACTERS', '0.1')
    assert VoiceTariff.from_env() is None
