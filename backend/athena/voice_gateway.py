"""Metered audio gateway and a credential-free harness-side adapter."""
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import hashlib
import json
import math
import os
import re

import httpx
from fastapi import Request
from fastapi.responses import Response

from .errors import ServiceError
from .store import now, uid
from .voice import VoiceConfig, VoiceService, SpeechInput, MAX_AUDIO_BYTES, MAX_SPEECH_BYTES, initialize_voice, _disconnectable


@dataclass(frozen=True)
class VoiceTariff:
    """Operator-published USD tariff; no implicit FX conversion or markup."""
    stt_per_second: Decimal
    tts_per_10k_characters: Decimal

    @classmethod
    def from_env(cls):
        try:
            rates = [Decimal(os.environ[key]) for key in ("ATHENA_AUDIO_STT_USD_PER_SECOND", "ATHENA_AUDIO_TTS_USD_PER_10K_CHARACTERS")]
            if not all(rate.is_finite() and 0 < rate <= 100 for rate in rates):
                return None
            return cls(*rates)
        except (KeyError, InvalidOperation):
            return None

    def charge(self, operation, units):
        rate = self.stt_per_second if operation == 'transcribe' else self.tts_per_10k_characters / 10000
        return int((Decimal(str(units)) * rate * 1_000_000).to_integral_value(rounding=ROUND_CEILING))


class MeteredVoice:
    def __init__(self, credits, tariff):
        self.credits, self.tariff = credits, tariff

    async def reserve(self, user_id, request_id, operation, model, units):
        maximum = math.ceil(units['seconds']) + 1 if operation == 'transcribe' else units['characters'] * 2
        fingerprint = hashlib.sha256(json.dumps({'operation': operation, 'model': model, 'units': units}, sort_keys=True).encode()).hexdigest()
        await self.credits.reserve(user_id, request_id, max(1, self.tariff.charge(operation, maximum)), fingerprint)
        return {'id': request_id, 'operation': operation, 'tariff': self.tariff}

    async def settle(self, reservation, *, status, usage):
        field = 'seconds' if reservation['operation'] == 'transcribe' else 'characters'
        actual = usage.get(field) if isinstance(usage, dict) else None
        if status != 'complete' or type(actual) not in (int, float) or not math.isfinite(actual) or actual < 0:
            await self.credits.uncertain(reservation['id'])
            return
        charge = reservation['tariff'].charge(reservation['operation'], actual)
        await self.credits.settle(reservation['id'], charge, json.dumps(usage))


async def initialize_gateway_voice(app, store, credits):
    await initialize_voice(store)
    config, tariff = replace(VoiceConfig.from_env(), hosted=True), VoiceTariff.from_env()
    # This tariff consumes provider-reported duration/characters. Token-priced
    # adapters need their own metering implementation before hosted enablement.
    billing = MeteredVoice(credits, tariff) if tariff and config.provider == 'dashscope' else None
    app.state.voice = VoiceService(config, store, billing=billing)


def register_gateway_voice_routes(app, authenticate):
    async def scope(request):
        account = await authenticate(request)
        source = request.headers.get('x-athena-session-id', '')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{16,100}', source):
            raise ServiceError('invalid_input', 'Invalid voice session.', 400)
        # Cloud audit sessions contain no conversation history or recordings.
        # Namespace by account, so a hostile client cannot claim another owner.
        session_id = hashlib.sha256((account['id'] + ':' + source).encode()).hexdigest()
        def ensure(db):
            project = 'personal:' + account['id']
            db.execute("INSERT OR IGNORE INTO projects VALUES (?, 'Voice audit')", (project,))
            db.execute("INSERT OR IGNORE INTO sessions(id,project_id,title,created_at,user_id) VALUES (?,?,'Voice audit',?,?)", (session_id, project, now(), account['id']))
        await app.state.store.call(ensure)
        return account['id'], session_id

    @app.get('/v1/voice')
    async def status(request: Request):
        account = await authenticate(request)
        result = await app.state.voice.status(account['id'])
        credit = await app.state.credits.balance(account['id'])
        if not credit['enabled'] or credit['available_micros'] <= 0:
            raise ServiceError('account_balance_insufficient', 'No available credit.', 402)
        return result

    @app.post('/v1/voice/transcribe')
    async def transcribe(request: Request, language: str = ''):
        owner, session = await scope(request)
        if request.headers.get('content-type', '').split(';')[0] != 'audio/wav' or (language and not re.fullmatch('[a-z]{2}', language)):
            raise ServiceError('voice_invalid_audio', 'Invalid audio.', 400)
        audio = bytearray()
        async for chunk in request.stream():
            audio.extend(chunk)
            if len(audio) > MAX_AUDIO_BYTES:
                raise ServiceError('voice_audio_too_large', 'Audio too large.', 413)
        return await _disconnectable(request, app.state.voice.perform(owner, session, 'transcribe', bytes(audio), language=language))

    @app.post('/v1/voice/speech')
    async def speech(request: Request, body: SpeechInput):
        owner, session = await scope(request)
        audio = await _disconnectable(request, app.state.voice.perform(owner, session, 'speech', body.text))
        return Response(audio, media_type='audio/wav' if audio.startswith(b'RIFF') else 'audio/mpeg')


class CloudVoiceService:
    """Desktop/worker bridge. Own account bearer is the only outbound secret."""
    def __init__(self, accounts):
        self.accounts = accounts
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10), follow_redirects=False, trust_env=False)

    async def close(self):
        await self.client.aclose()

    async def status(self, user_id):
        return await self.accounts.call('/voice', token=await self.accounts.token(user_id))

    async def perform(self, user_id, session_id, operation, payload, *, media_type='audio/wav', language=''):
        if operation not in {'transcribe', 'speech'}:
            raise ServiceError('invalid_input', 'Invalid voice operation.', 400)
        token = await self.accounts.token(user_id)
        headers = {'Authorization': 'Bearer ' + token, 'X-Athena-Session-ID': session_id}
        if operation == 'transcribe':
            headers['Content-Type'] = 'audio/wav'
            kwargs = {'content': payload, 'params': {'language': language}}
        else:
            kwargs = {'json': {'text': payload}}
        try:
            async with self.client.stream('POST', self.accounts.base + '/voice/' + operation, headers=headers, **kwargs) as response:
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > (MAX_SPEECH_BYTES if operation == 'speech' else 256 * 1024):
                        raise ServiceError('voice_provider_error', 'Voice response exceeded its limit.')
                if response.status_code != 200:
                    value = json.loads(content)
                    raise ServiceError(value.get('code', 'voice_provider_error'), 'Voice request could not complete.', response.status_code)
                return json.loads(content) if operation == 'transcribe' else bytes(content)
        except (httpx.HTTPError, ValueError):
            raise ServiceError('voice_provider_error', 'Voice service is temporarily unavailable.') from None
