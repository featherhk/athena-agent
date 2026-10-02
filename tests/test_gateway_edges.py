"""Public gateway failures and stream completion must preserve billing invariants."""
import json

import httpx
import pytest
from starlette.requests import Request

from athena.gateway import GatewayConfig, create_gateway


@pytest.fixture
async def gateway(tmp_path):
    app = create_gateway(GatewayConfig(tmp_path, api_key='operator-test-secret'))
    async with app.router.lifespan_context(app):
        await app.state.http.aclose()
        account, token = await app.state.auth.register('gateway-edge-user', 'test-password')
        await app.state.credits.grant(account['id'], 1_000_000, 'edge-seed', 'Synthetic test credit')
        yield app, account, token


def model_payload(**extra):
    return {'model': 'deepseek-flash', 'messages': [{'role': 'user', 'content': 'hello'}], 'max_tokens': 16, **extra}


def provider_response(*, done=True):
    result = {'choices': [{'delta': {'content': 'hello'}, 'finish_reason': 'stop'}],
              'usage': {'prompt_tokens': 20, 'completion_tokens': 5, 'prompt_cache_hit_tokens': 0}}
    return 'data: ' + json.dumps(result) + '\n\n' + ('data: [DONE]\n\n' if done else '')


async def test_client_closing_at_done_does_not_leave_a_successful_request_on_hold(gateway):
    app, account, token = gateway
    app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=provider_response())))
    body = json.dumps(model_payload()).encode()
    request_id = 'client-closes-after-done'

    async def receive():
        return {'type': 'http.request', 'body': body, 'more_body': False}

    request = Request({'type': 'http', 'method': 'POST', 'scheme': 'https', 'path': '/v1/chat/completions',
                       'query_string': b'', 'headers': [(b'authorization', ('Bearer ' + token).encode()),
                                                       (b'idempotency-key', request_id.encode())],
                       'client': ('127.0.0.1', 1), 'server': ('cloud.test', 443)}, receive=receive)
    endpoint = next(route.endpoint for route in app.routes if getattr(route, 'path', None) == '/v1/chat/completions')
    response = await endpoint(request)
    # Athena's model parser closes its HTTP response immediately at [DONE].
    # This also covers other standard SSE consumers with the same behavior.
    try:
        async for part in response.body_iterator:
            if b'[DONE]' in part:
                break
    finally:
        await response.body_iterator.aclose()
    state = (await app.state.store.rows('SELECT status,charged_micros FROM credit_requests WHERE id=?', (request_id,)))[0]
    assert state['status'] == 'settled'
    assert state['charged_micros'] > 0
    assert (await app.state.credits.balance(account['id']))['held_micros'] == 0


async def test_missing_done_retains_reservation_without_replaying_provider(gateway):
    app, account, token = gateway
    requests = []

    def provider(request):
        requests.append(request)
        return httpx.Response(200, text=provider_response(done=False))

    app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    headers = {'Authorization': 'Bearer ' + token, 'Idempotency-Key': 'missing-stream-done-123'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='https://cloud.test') as client:
        response = await client.post('/v1/chat/completions', json=model_payload(), headers=headers)
        assert response.status_code == 200
        assert (await app.state.credits.balance(account['id']))['held_micros'] > 0
        assert (await app.state.store.rows('SELECT status FROM credit_requests')) == [{'status': 'uncertain'}]
        again = await client.post('/v1/chat/completions', json=model_payload(), headers=headers)
        assert again.status_code == 409
        assert len(requests) == 1


async def test_conflicting_output_limits_are_rejected_before_reservation(gateway):
    app, _, token = gateway
    requests = []
    app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(200, text=provider_response())))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='https://cloud.test') as client:
        response = await client.post('/v1/chat/completions', json=model_payload(max_completion_tokens=1_000_000), headers={'Authorization': 'Bearer ' + token})
        assert response.status_code == 400
        assert not requests
        assert not await app.state.store.rows('SELECT 1 FROM credit_requests')


async def test_provider_rejection_does_not_expose_upstream_details(gateway):
    app, account, token = gateway
    app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(401, json={'error': 'operator-test-secret private-provider-details'})))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='https://cloud.test') as client:
        response = await client.post('/v1/chat/completions', json=model_payload(), headers={'Authorization': 'Bearer ' + token})
        assert response.status_code == 503
        assert response.json() == {'code': 'provider_unavailable'}
        assert 'operator-test-secret' not in response.text
        assert (await app.state.credits.balance(account['id']))['available_micros'] == 1_000_000
