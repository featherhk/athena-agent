import asyncio
import json
from pathlib import Path
import shlex
import httpx
import pytest
from athena.api import create_app
from athena.auth import Auth
from athena.metrics import summarize, usage_metrics


async def test_accounts_isolate_sessions_and_revoke_login(config):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:4317") as first, httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:4317") as second:
            registered = await first.post('/api/auth/register', json={"username": "first", "password": "local-test-password"})
            assert 'HttpOnly' in registered.headers['set-cookie']
            assert 'SameSite=strict' in registered.headers['set-cookie']
            sid = (await first.post('/api/sessions', json={})).json()['id']
            await second.post('/api/auth/register', json={"username": "second", "password": "different-test-password"})
            assert (await second.get('/api/sessions')).json() == []
            for suffix in ['', '/events']:
                assert (await second.get(f'/api/sessions/{sid}{suffix}')).status_code == 404
            for suffix in ['/stop', '/fork', '/messages']:
                assert (await second.post(f'/api/sessions/{sid}{suffix}', json={"text": "hi"})).status_code == 404
            assert (await first.post('/api/settings/key', json={"api_key": "test-only-value"})).status_code == 404
            assert (await first.get('/api/developer/metrics')).status_code == 403
            await first.post('/api/settings/developer', json={"enabled": True})
            assert (await first.get('/api/developer/metrics')).status_code == 200
            await first.post('/api/auth/logout', json={})
            assert (await first.get('/api/sessions')).status_code == 401
            assert (await first.post('/api/auth/login', json={"username": "first", "password": "incorrect-password"})).status_code == 400
            assert (await first.post('/api/auth/login', json={"username": "FIRST", "password": "local-test-password"})).status_code == 200
            assert (await first.get('/api/sessions')).json()[0]['id'] == sid
            hashes = await app.state.store.rows('SELECT password_hash FROM users')
            assert all('password' not in row['password_hash'] and row['password_hash'].startswith('pbkdf2_sha256$') for row in hashes)


async def test_registry_files_execution_and_reset_preserve_user_work(engine, store, config):
    user, _ = await Auth(store).register('creator', 'local-test-password')
    session = await store.session(user_id=user['id'])
    await store.message(session, None, 'user', 'Keep my messages')
    definition = {'op': 'register_tool', 'name': 'user_add', 'description': 'Adds two numbers',
        'parameters': {'type': 'object', 'properties': {'a': {'type': 'number'}, 'b': {'type': 'number'}}, 'required': ['a', 'b']},
        'source': 'import sys,json\na=json.load(sys.stdin)\nprint(json.dumps({"result": a["a"]+a["b"]}))\n'}
    result = await engine.tools.runtime(definition, session, None)
    assert result['version'] == 1
    assert (await engine.tools.definitions(session))[-1]['function']['name'] == 'user_add'
    output = await engine.tools.execute('user_add', {'a': 2, 'b': 3}, session, None)
    assert output['exit_code'] == 0, output
    assert json.loads(output['output']) == {'result': 5}
    await engine.tools.component({'op': 'upsert', 'id': 'test', 'html': '<button>hi</button>'}, session)
    component = (await store.components(session))[0]
    component_file = config.data_dir / component['file_path']
    assert component_file.read_text() == '<button>hi</button>'
    extension_file = config.data_dir / (await engine.tools.registered(session))[0]['file_path']
    await engine.reset_user(user['id'])
    assert await store.components(session) == []
    assert await engine.tools.registered(session) == []
    assert component_file.exists() and extension_file.exists()
    assert (await store.history(session))[0]['content'] == 'Keep my messages'
    assert len(await store.rows('SELECT * FROM resets')) == 1
    with pytest.raises(ValueError, match='disabled'):
        await engine.tools.execute('user_add', {'a': 1, 'b': 2}, session, None)


async def test_agent_cannot_write_source_or_read_core_database(engine, store, config):
    config.repo_dir.mkdir()
    source = config.repo_dir / 'source.py'
    source.write_text('original')
    session = await store.session()
    result = await engine.tools.shell({'command': 'printf changed > ' + shlex.quote(str(source))}, session, None)
    assert result['exit_code'] != 0
    assert source.read_text() == 'original'
    result = await engine.tools.shell({'command': 'cat ' + shlex.quote(str(store.path))}, session, None)
    assert result['exit_code'] != 0
    allowed = await engine.tools.shell({'command': 'printf okay > mine.txt; cat mine.txt'}, session, None)
    assert allowed['output'] == 'okay'


def row(usage, identifier='one'):
    return {'id': identifier, 'model': 'deepseek-flash', 'status': 'complete', 'usage': json.dumps(usage), 'created_at': '2026-10-02T00:00:00Z', 'finished_at': '2026-10-02T00:00:01Z'}


def test_metrics_weighted_cache_and_missing_usage_are_not_zero_cost():
    a = row({'prompt_tokens': 1000, 'completion_tokens': 100, 'prompt_cache_hit_tokens': 900, 'prompt_cache_miss_tokens': 100})
    b = row({'prompt_tokens': 9000, 'completion_tokens': 900, 'prompt_cache_hit_tokens': 0, 'prompt_cache_miss_tokens': 9000}, 'two')
    summary = summarize([a, b, row({}, 'missing')])['total']
    assert summary['cache_rate'] == .09
    assert summary['input_tokens'] == 10000
    assert summary['output_tokens'] == 1000
    assert summary['unreported_requests'] == 1
    assert summary['unpriced_requests'] == 1
    assert summary['cost_usd_low'] == pytest.approx((900*.003 + 9100*.15 + 1000*.6)/1e6)
    assert summary['cost_usd_high'] == summary['cost_usd_low']*2
    assert usage_metrics(row({}))['cost_usd_low'] is None
    assert usage_metrics(row({'prompt_tokens': 10, 'completion_tokens': 5, 'prompt_cache_hit_tokens': 100}))['cost_usd_low'] is None


async def test_first_account_adopts_legacy_data_without_deleting_it(store):
    legacy = await store.session()
    await store.message(legacy, None, 'user', 'before accounts')
    first, _ = await Auth(store).register('first', 'local-test-password')
    second, _ = await Auth(store).register('second', 'local-test-password')
    session = (await store.rows('SELECT * FROM sessions WHERE id=?', (legacy,)))[0]
    assert session['user_id'] == first['id'] and session['user_id'] != second['id']
    assert (await store.history(legacy))[0]['content'] == 'before accounts'
