"""Browser API -> cloud accounts/ledger -> provider, without network or real keys."""
import asyncio
from contextlib import asynccontextmanager
import json

import httpx

from athena.api import create_app
from athena.config import Config
from athena.gateway import GatewayConfig, create_gateway, usage_charge


@asynccontextmanager
async def cloud_stack(tmp_path):
    gateway_config = GatewayConfig(tmp_path / 'gateway', api_key='synthetic-operator-key')
    gateway = create_gateway(gateway_config)
    config = Config(data_dir=tmp_path / 'client-state', repo_dir=tmp_path / 'application',
                    cloud_url='https://accounts.example/v1')
    app = create_app(config)
    provider_requests = []
    usage = {'prompt_tokens': 100, 'completion_tokens': 8, 'prompt_cache_hit_tokens': 25}

    def provider(request):
        provider_requests.append(request)
        events = [
            {'choices': [{'delta': {'reasoning_content': 'Check the request. '}, 'finish_reason': None}]},
            {'choices': [{'delta': {'content': 'Hello '}, 'finish_reason': None}]},
            {'choices': [{'delta': {'content': 'from Athena.'}, 'finish_reason': 'stop'}]},
            {'choices': [], 'usage': usage, 'model': 'deepseek-flash'},
        ]
        body = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events) + 'data: [DONE]\n\n'
        return httpx.Response(200, text=body, headers={'content-type': 'text/event-stream'})

    async with gateway.router.lifespan_context(gateway):
        await gateway.state.http.aclose()
        gateway.state.http = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        async with app.router.lifespan_context(app):
            await app.state.cloud.http.aclose()
            app.state.cloud.http = httpx.AsyncClient(transport=httpx.ASGITransport(gateway))
            await app.state.engine.client.aclose()
            app.state.engine.client = httpx.AsyncClient(transport=httpx.ASGITransport(gateway))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317') as browser:
                yield app, gateway, gateway_config, browser, provider_requests, usage


async def test_fresh_cloud_client_offers_login_without_provider_configuration(tmp_path):
    async with cloud_stack(tmp_path) as (_, gateway, _, browser, provider_requests, _):
        # A newly installed client has no local users even when the central
        # account service already exists. Login must still be the default flow.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(gateway), base_url='https://accounts.example') as remote:
            response = await remote.post('/v1/auth/register', json={'username': 'existing-cloud-user', 'password': 'test-password'})
            assert response.status_code == 200
        response = await browser.get('/api/auth/me')
        assert response.status_code == 200
        assert response.json()['configured'] is True
        assert response.json()['user'] is None
        assert response.json()['has_users'] is True
        assert not provider_requests
        assert 'synthetic-operator-key' not in response.text


async def test_zero_credit_blocks_send_then_grant_streams_persists_and_settles(tmp_path):
    async with cloud_stack(tmp_path) as (app, gateway, gateway_config, browser, provider_requests, usage):
        registration = await browser.post('/api/auth/register', json={'username': 'new-cloud-user', 'password': 'test-password'})
        assert registration.status_code == 200
        user = registration.json()['user']
        assert user['role'] == 'user'
        assert 'token' not in registration.json()
        central = await gateway.state.store.rows('SELECT id FROM users WHERE username=?', ('new-cloud-user',))
        assert central == [{'id': user['id']}]
        me = await browser.get('/api/auth/me')
        assert me.json()['configured'] is True
        assert me.json()['user']['id'] == user['id']

        credit = (await browser.get('/api/account/credit')).json()
        assert credit['managed'] is True and credit['available_micros'] == 0
        assert credit['enabled'] is False and credit['recharge_enabled'] is False
        session = (await browser.post('/api/sessions', json={})).json()['id']
        blocked = await browser.post(f'/api/sessions/{session}/messages', json={'text': 'Hello Athena'})
        assert blocked.status_code == 402
        assert blocked.json()['code'] == 'account_balance_insufficient'
        assert not provider_requests
        assert not await app.state.store.rows('SELECT 1 FROM runs')
        assert not await app.state.store.rows('SELECT 1 FROM requests')
        assert not await gateway.state.store.rows('SELECT 1 FROM credit_requests')

        # Credit grants are an operator operation, never a customer HTTP endpoint.
        await gateway.state.credits.grant(user['id'], 1_000_000, 'integration-preview-credit', 'Synthetic preview grant')
        sent = await browser.post(f'/api/sessions/{session}/messages', json={'text': 'Hello Athena'})
        assert sent.status_code == 200
        run_id = sent.json()['run_id']
        await asyncio.wait_for(asyncio.gather(*app.state.engine.actors.values()), 5)
        snapshot_response = await browser.get(f'/api/sessions/{session}')
        snapshot = snapshot_response.json()
        assert snapshot['runs'][0]['id'] == run_id
        assert snapshot['runs'][0]['status'] == 'complete'
        assert snapshot['messages'][-1]['content'] == 'Hello from Athena.'
        assert snapshot['messages'][-1]['reasoning'] == 'Check the request. '
        assert snapshot['messages'][-1]['status'] == 'complete'
        assert len(provider_requests) == 1
        assert provider_requests[0].headers['authorization'] == 'Bearer synthetic-operator-key'

        requests = await app.state.store.rows('SELECT id,status,input,response,usage FROM requests')
        assert len(requests) == 1 and requests[0]['status'] == 'complete'
        assert json.loads(requests[0]['usage']) == usage
        assert 'synthetic-operator-key' not in json.dumps(requests)
        ledger = await gateway.state.store.rows('SELECT id,user_id,status,charged_micros FROM credit_requests')
        assert ledger == [{'id': requests[0]['id'], 'user_id': user['id'], 'status': 'settled',
                           'charged_micros': usage_charge(gateway_config, usage)}]
        after = (await browser.get('/api/account/credit')).json()
        assert after['held_micros'] == 0
        assert after['balance_micros'] == 1_000_000 - ledger[0]['charged_micros']
        assert after['available_micros'] == after['balance_micros']

        # Persisted deltas show real progressive streaming rather than only a
        # final synthesized response, and a fresh snapshot restores the result.
        events = await app.state.store.events(session)
        contents = [row['payload'].get('content') for row in events if row['type'] == 'message']
        assert 'Hello ' in contents and 'Hello from Athena.' in contents
        assert (await browser.post('/api/auth/logout', json={})).status_code == 200
        assert (await browser.get('/api/auth/me')).json()['user'] is None
        login = await browser.post('/api/auth/login', json={'username': 'new-cloud-user', 'password': 'test-password'})
        assert login.status_code == 200 and login.json()['user']['id'] == user['id']
        restored = await browser.get(f'/api/sessions/{session}')
        assert restored.json()['messages'][-1]['content'] == 'Hello from Athena.'
        assert 'synthetic-operator-key' not in registration.text + me.text + sent.text + snapshot_response.text + login.text + restored.text
