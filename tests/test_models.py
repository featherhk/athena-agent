import asyncio
from dataclasses import replace
import json
import httpx
import pytest
from athena.api import create_app
from athena.errors import ServiceError
from athena.profiles import Profile
from athena.store import now


async def register(store, identifier='alternative', with_key=True):
    profile = Profile(id=identifier, provider='Example', model='example-text', base_model='Example Text',
                      api_base='https://example.invalid/v1', instruction_role='developer',
                      output_parameter='max_completion_tokens')
    await store.execute('INSERT INTO model_profiles VALUES (?,?,1,?)', (identifier, json.dumps(profile.snapshot()), now()))
    if with_key:
        await store.execute('INSERT INTO settings VALUES (?,?)', (f'provider_key:{identifier}', 'test-other-provider'))
    return profile


async def test_missing_platform_key_has_diagnostic_without_consuming_message(config):
    app = create_app(replace(config, api_key=''))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317') as client:
            await client.post('/api/auth/register', json={'username': 'newuser', 'password': 'local-test-password'})
            sid = (await client.post('/api/sessions', json={})).json()['id']
            response = await client.post(f'/api/sessions/{sid}/messages', json={'text': 'Keep my draft'})
            assert response.status_code == 503
            assert response.json()['code'] == 'provider_not_configured'
            assert await app.state.store.rows('SELECT * FROM runs') == []
            assert (await client.get('/api/service')).json()['configured'] is False
            await client.post('/api/settings/developer', json={'enabled': True})
            diagnostic = (await client.get(f'/api/developer/metrics?session_id={sid}')).json()['service']
            assert diagnostic['code'] == 'provider_not_configured'
            await app.state.store.execute("INSERT INTO settings VALUES ('deepseek_api_key','test-configured-later')")
            assert (await client.get('/api/service')).json()['configured'] is True
            assert 'test-configured-later' not in (await client.get('/api/developer/metrics')).text


async def test_switch_applies_at_next_request_keeps_identity_history_and_audit(engine, store, monkeypatch):
    await register(store)
    sid = await store.session()
    payloads = []
    async def provider(client, base, key, payload, on_text, on_reasoning=None):
        payloads.append((base, key, payload))
        if len(payloads) == 1:
            message = {'role': 'assistant', 'content': '', 'reasoning_content': 'private reasoning', 'tool_calls': [
                {'id': 'switch-1', 'type': 'function', 'function': {'name': 'runtime', 'arguments': json.dumps({'op': 'switch_model', 'profile_id': 'alternative', 'reason': 'Task-specific capability'})}}]}
        else:
            await on_text('Ready')
            message = {'role': 'assistant', 'content': 'Ready', 'reasoning_content': ''}
        return {'message': message, 'usage': {'prompt_tokens': 30, 'completion_tokens': 10}, 'model': 'returned-model-version'}
    monkeypatch.setattr('athena.engine.complete', provider)
    run = await engine.submit(sid, 'Switch for this task')
    await engine.actors[sid]
    assert (await store.rows('SELECT status FROM runs WHERE id=?', (run,)))[0]['status'] == 'complete'
    assert len(payloads) == 2
    first, second = payloads[0][2], payloads[1][2]
    assert 'DeepSeek V4.1 Flash' in first['messages'][0]['content']
    assert 'Athena' in second['messages'][0]['content'] and 'Example Text' in second['messages'][0]['content']
    assert second['messages'][0]['role'] == 'developer'
    assert second['messages'][0]['content'].endswith('基模：Example Text。')
    assert '上下文版本' not in second['messages'][0]['content']
    assert payloads[1][0] == 'https://example.invalid/v1' and payloads[1][1] == 'test-other-provider'
    assert 'thinking' not in second and 'max_tokens' not in second
    assert second['max_completion_tokens'] == 16384
    assert all('reasoning_content' not in message for message in second['messages'])
    assert sum(message.get('tool_call_id') == 'switch-1' for message in second['messages']) == 1
    assert (await store.rows("SELECT reasoning FROM messages WHERE tool_calls IS NOT NULL"))[0]['reasoning'] == 'private reasoning'
    requests = await store.rows('SELECT * FROM requests ORDER BY rowid')
    assert [r['context_epoch'] for r in requests] == [0, 1]
    assert all(r['response_model'] == 'returned-model-version' for r in requests)
    assert 'test-other-provider' not in json.dumps(requests)
    assert len(await store.rows('SELECT * FROM model_switches')) == 1
    before = await engine.context(sid)
    assert (await engine.models.switch(sid, 'alternative', 'No-op', None))['changed'] is False
    assert await engine.context(sid) == before
    fork = await store.fork(sid)
    assert (await engine.context(fork))[0] == before[0]
    await engine.models.switch(fork, 'default', 'Independent branch', None)
    assert (await engine.context(sid))[0] == before[0]
    assert 'DeepSeek' in (await engine.context(fork))[0]['content']


async def test_unconfigured_switch_leaves_original_epoch(engine, store):
    await register(store, with_key=False)
    sid = await store.session()
    with pytest.raises(ServiceError):
        await engine.models.switch(sid, 'alternative', 'Try another', None)
    assert (await engine.models.selected(sid))[1] == 0
    assert await store.rows('SELECT * FROM model_switches') == []


async def test_provider_failure_is_redacted_but_diagnosable(engine, store):
    sid = await store.session()
    await engine.client.aclose()
    engine.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(401, json={'error': 'sensitive provider body'})))
    run = await engine.submit(sid, 'Hi')
    await engine.actors[sid]
    requests = await store.rows('SELECT * FROM requests WHERE run_id=?', (run,))
    assert requests[0]['error_code'] == 'provider_auth_failed'
    assert 'sensitive provider body' not in json.dumps(requests)
    assert (await store.rows('SELECT status FROM runs WHERE id=?', (run,)))[0]['status'] == 'failed'


def test_profiles_reject_endpoint_credentials_and_payload_overrides():
    with pytest.raises(ValueError):
        Profile(id='bad', provider='A', model='a', base_model='A', api_base='https://user:secret@example.invalid')
    with pytest.raises(ValueError):
        Profile(id='bad', provider='A', model='a', base_model='A', api_base='https://example.invalid', options={'messages': []})


async def test_multilingual_accounts_and_validation_do_not_echo_passwords(config):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317') as client:
            for username in ['Élodie', 'アテナ', '사용자', 'مستخدم']:
                response = await client.post('/api/auth/register', json={'username': username, 'password': 'local-test-password'})
                assert response.status_code == 200
                await client.post('/api/auth/logout', json={})
                assert (await client.post('/api/auth/login', json={'username': username, 'password': 'local-test-password'})).status_code == 200
                await client.post('/api/auth/logout', json={})
            invalid = await client.post('/api/auth/register', json={'username': 'spaces disallowed', 'password': 'private-input'})
            assert invalid.status_code == 422 and invalid.json()['code'] == 'invalid_input'
            assert 'private-input' not in invalid.text
