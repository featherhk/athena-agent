"""Fresh source onboarding is local, owned and write-only; hosted auth is unchanged."""
import asyncio
from dataclasses import replace
import json

import httpx
import pytest

from athena.api import create_app
from athena.auth import Auth
from athena.config import Config
from athena.local_setup import PROFILE, PRINCIPAL
from athena.protection import command_environment
from athena.store import Store


@pytest.fixture
def local_config(config):
    return replace(config, api_key='', local_setup_enabled=True)


def transport(app, peer='127.0.0.1'):
    return httpx.ASGITransport(app, client=(peer, 43210))


def provider(**extra):
    return {'revision': 0, 'provider': 'Test provider', 'model': 'test-model',
            'api_base': 'https://provider.example/v1', 'api_key': 'fixture-secret-never-echo', **extra}


async def test_fresh_bootstrap_is_atomic_and_reuses_only_marked_identity(local_config):
    app = create_app(local_config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport(app), base_url='http://127.0.0.1:4317') as client:
            assert (await client.get('/api/local/setup')).json() == {'mode': 'local', 'initialized': False}
            assert (await client.get('/api/local/provider')).status_code == 401
            first, second = await asyncio.gather(client.post('/api/local/bootstrap', json={}), client.post('/api/local/bootstrap', json={}))
            assert first.status_code == second.status_code == 200
            assert first.json()['user']['id'] == second.json()['user']['id']
            assert 'HttpOnly' in first.headers['set-cookie'] and 'SameSite=strict' in first.headers['set-cookie']
            user = first.json()['user']
            assert len(await app.state.store.rows('SELECT id FROM users')) == 1
            assert (await client.get('/api/auth/me')).json()['user']['id'] == user['id']
            assert (await client.get('/api/local/setup')).json() == {'mode': 'local', 'initialized': True}
            assert (await client.post('/api/auth/register', json={'username': 'accidental-second', 'password': 'fixture-password'})).status_code == 409
            # Clearing the browser cookie never creates another account.
            client.cookies.clear()
            assert (await client.post('/api/local/bootstrap', json={})).json()['user']['id'] == user['id']


async def test_ipv6_loopback_browser_can_bootstrap_and_save_local_provider(local_config, monkeypatch):
    monkeypatch.setenv('ATHENA_PORT', '4497')
    app = create_app(replace(local_config, bind_host='::1'))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport(app, '::1'), base_url='http://[::1]:4497',
                                     headers={'Origin': 'http://[::1]:4497', 'Sec-Fetch-Site': 'same-origin'}) as client:
            response = await client.post('/api/local/bootstrap', json={})
            assert response.status_code == 200
            user = response.json()['user']
            assert (await client.get('/api/auth/me')).json()['user']['id'] == user['id']
            saved = await client.post('/api/local/provider', json=provider(
                provider='Local', api_base='http://[::1]:11434/v1', api_key=None))
            assert saved.status_code == 200 and saved.json()['configured']
            session = (await client.post('/api/sessions', json={})).json()['id']
            selected, _ = await app.state.engine.models.selected(session)
            assert selected.id == saved.json()['profile']['id']


@pytest.mark.parametrize('origin', [
    'http://[::1]:4498', 'https://[::1]:4497', 'http://[::ffff:127.0.0.1]:4497',
    'http://[::2]:4497', 'http://localhost.attacker.example:4497', 'null',
])
async def test_ipv6_browser_origin_does_not_expand_host_scheme_or_port_access(local_config, monkeypatch, origin):
    monkeypatch.setenv('ATHENA_PORT', '4497')
    app = create_app(replace(local_config, bind_host='::1'))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport(app, '::1'), base_url='http://[::1]:4497',
                                     headers={'Origin': origin}) as client:
            assert (await client.post('/api/local/bootstrap', json={})).status_code == 403
            assert not await app.state.store.rows('SELECT id FROM users')


@pytest.mark.parametrize('extra', [
    {'bind_host': '0.0.0.0'}, {'bind_host': '::'}, {'public_origin': 'https://local.example'},
    {'local_setup_enabled': False},
])
async def test_exposed_or_disabled_server_cannot_bootstrap(local_config, extra):
    app = create_app(replace(local_config, **extra))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport(app), base_url='http://127.0.0.1:4317') as client:
            assert (await client.post('/api/local/bootstrap', json={})).status_code in (404, 503)
            assert not await app.state.store.rows('SELECT id FROM users')


@pytest.mark.parametrize('peer,headers,base', [
    ('192.0.2.44', {}, 'http://127.0.0.1:4317'),
    ('127.0.0.1', {'Origin': 'https://untrusted.example'}, 'http://127.0.0.1:4317'),
    ('127.0.0.1', {'Origin': 'null'}, 'http://127.0.0.1:4317'),
    ('127.0.0.1', {'Sec-Fetch-Site': 'cross-site'}, 'http://127.0.0.1:4317'),
    ('127.0.0.1', {'X-Forwarded-For': '127.0.0.1'}, 'http://127.0.0.1:4317'),
    ('127.0.0.1', {'Forwarded': 'for=127.0.0.1'}, 'http://127.0.0.1:4317'),
    ('127.0.0.1', {}, 'http://attacker.example:4317'),
])
async def test_bootstrap_rejects_nonlocal_cross_site_and_proxy_identity(local_config, peer, headers, base):
    app = create_app(local_config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport(app, peer), base_url=base, headers=headers) as client:
            assert (await client.post('/api/local/bootstrap', json={})).status_code == 403
            assert not await app.state.store.rows('SELECT id FROM users')


@pytest.mark.parametrize('existing', ['one', 'many', 'orphan'])
async def test_existing_accounts_and_history_never_silently_adopted(local_config, existing):
    app = create_app(local_config)
    async with app.router.lifespan_context(app):
        if existing == 'orphan':
            await app.state.store.session('Legacy history')
        else:
            await Auth(app.state.store).register('existing-one', 'fixture-password')
            if existing == 'many':
                await Auth(app.state.store).register('existing-two', 'fixture-password')
        before = await app.state.store.rows('SELECT id,username FROM users')
        async with httpx.AsyncClient(transport=transport(app), base_url='http://127.0.0.1:4317') as client:
            assert (await client.get('/api/local/setup')).json()['mode'] == 'account'
            assert (await client.post('/api/local/bootstrap', json={})).status_code == 409
            assert await app.state.store.rows('SELECT id,username FROM users') == before
            assert not await app.state.store.rows('SELECT value FROM settings WHERE key=?', (PRINCIPAL,))
            if existing != 'orphan':
                assert (await client.post('/api/auth/login', json={'username': 'existing-one', 'password': 'fixture-password'})).status_code == 200
                assert (await client.post('/api/local/provider', json=provider())).status_code == 403


async def test_provider_write_only_and_initial_session_reset_use_saved_profile(local_config, monkeypatch):
    app = create_app(local_config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport(app), base_url='http://127.0.0.1:4317') as client:
            user = (await client.post('/api/local/bootstrap', json={})).json()['user']
            response = await client.post('/api/local/provider', json=provider())
            assert response.status_code == 200
            identifier = response.json()['profile']['id']
            assert identifier.startswith('local-') and response.json()['configured']
            assert 'fixture-secret' not in response.text
            for path in ['/api/local/provider', '/api/auth/me', '/api/health']:
                result = await client.get(path)
                assert 'fixture-secret' not in result.text
            selected, _ = await app.state.engine.models.selected()
            assert selected.id == identifier
            assert await app.state.engine.models.key(selected) == 'fixture-secret-never-echo'
            session = (await client.post('/api/sessions', json={})).json()['id']
            assert (await app.state.engine.models.selected(session))[0].id == identifier
            await client.post('/api/reset', json={})
            assert (await app.state.engine.models.selected(session))[0].id == identifier
            assert 'fixture-secret' not in json.dumps(await app.state.engine.models.catalog())
            assert 'fixture-secret' not in json.dumps(await app.state.engine.models.diagnostic(session))
            monkeypatch.setenv('DEEPSEEK_API_KEY', 'fixture-secret-never-echo')
            assert 'fixture-secret' not in json.dumps(command_environment(local_config.data_dir / 'files' / user['id']))
            assert not await app.state.store.rows('SELECT id FROM requests')  # Save is not a paid connectivity check.


async def test_immutable_model_switch_is_audited_and_busy_session_rejected(local_config):
    app = create_app(local_config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport(app), base_url='http://127.0.0.1:4317') as client:
            await client.post('/api/local/bootstrap', json={})
            first = (await client.post('/api/local/provider', json=provider())).json()
            sid = (await client.post('/api/sessions', json={})).json()['id']
            await app.state.store.execute("INSERT INTO runs(id,session_id,status,created_at) VALUES ('busy',?,'waiting_user','2026-10-01')", (sid,))
            payload = provider(revision=1, model='next-model', session_id=sid)
            assert (await client.post('/api/local/provider', json=payload)).json()['code'] == 'local_model_busy'
            assert (await client.get('/api/local/provider')).json()['revision'] == 1
            await app.state.store.execute("UPDATE runs SET status='complete' WHERE id='busy'")
            second = (await client.post('/api/local/provider', json=payload)).json()
            assert second['profile']['id'] != first['profile']['id']
            assert (await app.state.engine.models.get(first['profile']['id'])).model == 'test-model'
            current, epoch = await app.state.engine.models.selected(sid)
            assert current.model == 'next-model' and epoch == 1
            switches = await app.state.store.rows('SELECT * FROM model_switches WHERE session_id=?', (sid,))
            assert len(switches) == 1 and switches[0]['reason'] == 'Local model settings'
            assert 'fixture-secret' not in json.dumps(switches)
            assert (await client.post('/api/local/provider', json=payload)).json()['code'] == 'local_setup_conflict'
            assert len(await app.state.store.rows('SELECT id FROM model_profiles')) == 3


async def test_key_never_transfers_to_changed_endpoint_and_local_models_need_no_key(local_config):
    app = create_app(local_config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport(app), base_url='http://127.0.0.1:4317') as client:
            await client.post('/api/local/bootstrap', json={})
            await client.post('/api/local/provider', json=provider())
            change = provider(revision=1, api_base='https://different.example/v1', api_key=None)
            assert (await client.post('/api/local/provider', json=change)).json()['code'] == 'local_provider_key_required'
            same = (await client.post('/api/local/provider', json=provider(revision=1, model='next-model', api_key=None))).json()
            assert same['configured']
            local = provider(revision=2, provider='Local', api_base='http://127.0.0.1:11434/v1', api_key=None)
            assert (await client.post('/api/local/provider', json=local)).status_code == 200
            selected, _ = await app.state.engine.models.selected()
            assert await app.state.engine.models.key(selected) == 'local-no-key'
            invalid = await client.post('/api/local/provider', json=provider(revision=3, api_key={'oops': 'fixture-secret-never-echo'}))
            assert invalid.status_code == 422 and 'fixture-secret' not in invalid.text


async def test_bootstrap_does_not_adopt_marked_account_once_multiuser(local_config):
    app = create_app(local_config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport(app), base_url='http://127.0.0.1:4317') as client:
            await client.post('/api/local/bootstrap', json={})
            await Auth(app.state.store).register('later-account', 'fixture-password')
            assert (await client.get('/api/local/setup')).json()['mode'] == 'account'
            assert (await client.post('/api/local/bootstrap', json={})).status_code == 409
            assert (await client.post('/api/local/provider', json=provider())).status_code == 403


async def test_cloud_configuration_never_bootstraps_even_on_loopback(local_config):
    cloud_config = replace(local_config, cloud_url='https://cloud.example')
    app = create_app(cloud_config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport(app), base_url='http://127.0.0.1:4317') as client:
            assert (await client.get('/api/local/setup')).json() == {'mode': 'hosted'}
            assert (await client.post('/api/local/bootstrap', json={})).status_code == 404
            assert not await app.state.store.rows('SELECT id FROM users')


def test_source_config_respects_bind_and_hosted_flags(monkeypatch, tmp_path):
    monkeypatch.setenv('ATHENA_DATA_DIR', str(tmp_path / 'local-data'))
    monkeypatch.setenv('ATHENA_CLOUD_URL', '')
    monkeypatch.setenv('ATHENA_PUBLIC_ORIGIN', '')
    monkeypatch.setenv('ATHENA_BIND_HOST', '127.0.0.1')
    assert Config.from_env().local_setup
    monkeypatch.setenv('ATHENA_BIND_HOST', '0.0.0.0')
    assert not Config.from_env().local_setup
    monkeypatch.setenv('ATHENA_BIND_HOST', '::1')
    assert Config.from_env().local_setup
    monkeypatch.setenv('ATHENA_CLOUD_URL', 'https://cloud.example')
    assert not Config.from_env().local_setup


async def test_registration_bootstrap_race_has_one_owner(local_config):
    app = create_app(local_config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport(app), base_url='http://127.0.0.1:4317') as client:
            result = await asyncio.gather(client.post('/api/local/bootstrap', json={}), client.post('/api/auth/register', json={'username': 'explicit', 'password': 'fixture-password'}))
            assert sum(response.status_code == 200 for response in result) == 1
            assert len(await app.state.store.rows('SELECT id FROM users')) == 1


@pytest.mark.parametrize('api_base', [
    'http://remote.example/v1', 'https://user:fixture-secret-never-echo@provider.example/v1',
    'https://provider.example/v1?key=fixture-secret-never-echo', 'https://provider.example/#fixture-secret-never-echo',
])
async def test_provider_url_validation_never_echoes_credential_fields(local_config, api_base):
    app = create_app(local_config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport(app), base_url='http://127.0.0.1:4317') as client:
            await client.post('/api/local/bootstrap', json={})
            response = await client.post('/api/local/provider', json=provider(api_base=api_base))
            assert response.status_code == 400
            assert 'fixture-secret' not in response.text
            assert not await app.state.store.rows('SELECT value FROM settings WHERE key=?', (PROFILE,))


async def test_restart_keeps_identity_and_uses_byok_for_real_stream_transport(local_config):
    app = create_app(local_config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport(app), base_url='http://127.0.0.1:4317') as client:
            first = (await client.post('/api/local/bootstrap', json={})).json()['user']
            await client.post('/api/local/provider', json=provider())
    restarted = create_app(local_config)
    async with restarted.router.lifespan_context(restarted):
        requests = []
        def respond(request):
            requests.append(request)
            chunk = {'model': 'test-model', 'choices': [{'delta': {'content': 'Local ready'}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 2}}
            return httpx.Response(200, text='data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n', headers={'Content-Type': 'text/event-stream'})
        await restarted.state.engine.client.aclose()
        restarted.state.engine.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        async with httpx.AsyncClient(transport=transport(restarted), base_url='http://127.0.0.1:4317') as client:
            assert (await client.post('/api/local/bootstrap', json={})).json()['user']['id'] == first['id']
            sid = (await client.post('/api/sessions', json={})).json()['id']
            sent = await client.post(f'/api/sessions/{sid}/messages', json={'text': 'Hello'})
            assert sent.status_code == 200
            await restarted.state.engine.actors[sid]
            assert len(requests) == 1
            assert str(requests[0].url) == 'https://provider.example/v1/chat/completions'
            assert requests[0].headers['authorization'] == 'Bearer fixture-secret-never-echo'
            payload = json.loads(requests[0].content)
            assert payload['model'] == 'test-model' and 'Athena' in payload['messages'][0]['content']
            assert 'fixture-secret' not in json.dumps(payload)
            snapshot = (await client.get(f'/api/sessions/{sid}')).json()
            assert snapshot['messages'][-1]['content'] == 'Local ready'
            assert 'fixture-secret' not in json.dumps(snapshot)
            receipts = await restarted.state.store.rows('SELECT * FROM requests')
            assert receipts[0]['status'] == 'complete' and 'fixture-secret' not in json.dumps(receipts)
