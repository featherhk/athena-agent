"""Recharge uses the signed-in central account, never an agent-supplied owner."""
from contextlib import asynccontextmanager

import httpx
import pytest

from athena.api import create_app
from athena.config import Config
from athena.errors import ServiceError


class Accounts:
    def __init__(self):
        self.calls = []
        self.http = httpx.AsyncClient()

    async def token(self, user_id):
        return 'synthetic-token-' + user_id

    async def call(self, path, *, token, body=None):
        self.calls.append((path, token, body))
        if path.endswith('/foreign-order'):
            raise ServiceError('recharge_not_found', 'Order not found.', 404)
        if body:
            return {'id': 'order-1', 'amount_micros': body['amount_micros'], 'currency': 'USD',
                    'status': 'pending', 'checkout_url': 'https://payments.example/i/test'}
        return {'enabled': True, 'amounts_micros': [10_000_000], 'currency': 'USD', 'orders': []}


@asynccontextmanager
async def client(tmp_path, managed=True):
    app = create_app(Config(tmp_path / 'data', tmp_path / 'core'))
    async with app.router.lifespan_context(app):
        # Only the transport is synthetic. These requests still traverse the
        # application's cookie, origin, Pydantic and route boundaries.
        remote = Accounts()
        if managed:
            app.state.cloud = remote
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317') as browser:
                yield browser, remote
        finally:
            await remote.http.aclose()


async def register(browser, username):
    response = await browser.post('/api/auth/register', json={'username': username, 'password': 'synthetic-password'})
    return response


async def test_local_only_recharge_is_disabled_and_cannot_create(tmp_path):
    async with client(tmp_path, managed=False) as (browser, remote):
        assert (await browser.get('/api/account/recharge')).status_code == 401
        assert (await register(browser, 'local-user')).status_code == 200
        assert (await browser.get('/api/account/recharge')).json() == {
            'enabled': False, 'amounts_micros': [], 'currency': 'USD', 'orders': []}
        response = await browser.post('/api/account/recharge', json={
            'amount_micros': 10_000_000, 'idempotency_key': 'synthetic-order-key'})
        assert response.status_code == 503
        assert response.json()['code'] == 'recharge_unavailable'
        assert not remote.calls


async def test_proxy_uses_current_user_and_preserves_idempotent_intent(tmp_path):
    async with client(tmp_path, managed=False) as (browser, remote):
        # Register locally, then connect only the recharge transport.
        response = await register(browser, 'alice')
        alice = response.json()['user']['id']
        app = browser._transport.app
        app.state.cloud = remote
        assert (await browser.get('/api/account/recharge')).json()['enabled'] is True
        body = {'amount_micros': 10_000_000, 'idempotency_key': 'synthetic-order-key'}
        for _ in range(2):
            created = await browser.post('/api/account/recharge', json=body)
            assert created.status_code == 200
        assert remote.calls == [('/account/recharge', 'synthetic-token-' + alice, None),
                                ('/account/recharge', 'synthetic-token-' + alice, body),
                                ('/account/recharge', 'synthetic-token-' + alice, body)]
        assert 'synthetic-token' not in created.text
        assert (await browser.get('/api/account/recharge/foreign-order')).status_code == 404
        assert (await browser.get('/api/account/recharge/pending-order')).status_code == 200
        assert remote.calls[-1][:2] == ('/account/recharge/pending-order', 'synthetic-token-' + alice)
        app.state.cloud = None
        browser.cookies.clear()
        bob = (await register(browser, 'bob')).json()['user']['id']
        app.state.cloud = remote
        assert (await browser.get('/api/account/recharge')).status_code == 200
        assert remote.calls[-1][1] == 'synthetic-token-' + bob
        browser.cookies.clear()
        before = len(remote.calls)
        assert (await browser.get('/api/account/recharge')).status_code == 401
        assert len(remote.calls) == before


@pytest.mark.parametrize('change', [
    {'user_id': 'another-account'}, {'checkout_url': 'https://foreign.example'},
    {'amount_micros': True}, {'amount_micros': 1.2}, {'amount_micros': '10000000'},
    {'amount_micros': 0}, {'amount_micros': 1_000_000_001},
    {'idempotency_key': '../arbitrary-path'}, {'idempotency_key': 'short'},
])
async def test_proxy_rejects_invalid_order_before_contacting_cloud(tmp_path, change):
    async with client(tmp_path, managed=False) as (browser, remote):
        assert (await register(browser, 'alice')).status_code == 200
        browser._transport.app.state.cloud = remote
        body = {'amount_micros': 10_000_000, 'idempotency_key': 'synthetic-order-key', **change}
        response = await browser.post('/api/account/recharge', json=body)
        assert response.status_code == 422
        assert response.json() == {'code': 'invalid_input'}
        assert not remote.calls


async def test_cross_site_order_is_rejected_before_cloud(tmp_path):
    async with client(tmp_path, managed=False) as (browser, remote):
        await register(browser, 'alice')
        browser._transport.app.state.cloud = remote
        response = await browser.post('/api/account/recharge', json={
            'amount_micros': 10_000_000, 'idempotency_key': 'synthetic-order-key'},
            headers={'Origin': 'https://foreign.example'})
        assert response.status_code == 403
        assert not remote.calls


@pytest.mark.parametrize('enabled, expected', [(False, 200), (True, 404), (None, 404)])
async def test_old_gateway_fallback_requires_explicitly_disabled_credit_flag(tmp_path, enabled, expected):
    async with client(tmp_path, managed=False) as (browser, remote):
        await register(browser, 'alice')
        browser._transport.app.state.cloud = remote

        async def old_gateway(path, **kwargs):
            if path == '/auth/me':
                return {'credit': {'recharge_enabled': enabled}}
            raise ServiceError('cloud_unavailable', 'Route not found.', 404)

        remote.call = old_gateway
        response = await browser.get('/api/account/recharge')
        assert response.status_code == expected
        if expected == 200:
            assert response.json()['enabled'] is False
        # Missing creation/receipt routes are never converted into success.
        response = await browser.post('/api/account/recharge', json={
            'amount_micros': 10_000_000, 'idempotency_key': 'synthetic-order-key'})
        assert response.status_code == 404
        assert (await browser.get('/api/account/recharge/any-order')).status_code == 404
