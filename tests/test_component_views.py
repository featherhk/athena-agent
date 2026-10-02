import asyncio
import json

import httpx
import pytest

from athena.api import create_app
from athena.auth import Auth
from athena.component_views import update_view
from athena.errors import UserError
from athena.registry import Registry


async def fixture(config, store):
    user, _ = await Auth(store).register('surface-owner', 'test-password')
    session = await store.session(user_id=user['id'])
    registry = Registry(config, store)
    await registry.operate({'kind': 'component', 'action': 'register', 'id': 'notes', 'title': 'Notes',
                            'source': '<textarea>Unsaved draft</textarea>', 'placement': 'top-end'}, session)
    return user, session, registry


async def test_layout_is_durable_audited_fork_isolated_and_never_rewrites_source(config, store):
    user, session, registry = await fixture(config, store)
    original = (await store.components(session))[0]
    view = {'x': 0.4, 'y': 0.3, 'width': 480, 'height': 360, 'hidden': True}
    result = await registry.operate({'kind': 'component', 'action': 'layout', 'id': 'notes', 'version': 1, 'view_revision': 0, 'view': view}, session)
    assert result['view'] == view and result['view_revision'] == 1
    event = (await store.rows("SELECT * FROM events WHERE session_id=? ORDER BY seq DESC LIMIT 1", (session,)))[0]
    assert event['type'] == 'component_view' and json.loads(event['payload'])['view'] == view
    await store.initialize()
    current = (await store.components(session))[0]
    assert current['view'] == view
    assert {key: current[key] for key in original if not key.startswith('view')} == {key: value for key, value in original.items() if not key.startswith('view')}
    fork = await store.fork(session)
    assert (await store.components(fork))[0]['view'] == view
    await update_view(store, fork, 'notes', 1, 1, {'hidden': False, 'x': None})
    assert (await store.components(session))[0]['view'] == view
    assert 'x' not in (await store.components(fork))[0]['view']
    await registry.operate({'kind': 'component', 'action': 'register', 'id': 'notes', 'source': '<p>Updated</p>'}, session)
    # Automatic content refresh must never reopen a surface the user folded.
    assert (await store.components(session))[0]['view'] == view
    historical = await registry.operate({'kind': 'component', 'action': 'read', 'id': 'notes', 'version': 1}, session)
    assert historical['source'] == original['html'] and historical['enabled'] is False


async def test_stale_layout_write_cannot_overwrite_another_window_or_registration(config, store):
    _, session, registry = await fixture(config, store)
    results = await asyncio.gather(*(update_view(store, session, 'notes', 1, 0, {'width': width}) for width in [400, 500]), return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 1
    conflict = next(result for result in results if isinstance(result, UserError))
    assert conflict.code == 'component_changed'
    before = (await store.components(session))[0]['view']
    await registry.operate({'kind': 'component', 'action': 'register', 'id': 'notes', 'source': '<p>Updated</p>'}, session)
    with pytest.raises(UserError, match='changed'):
        await update_view(store, session, 'notes', 1, 1, {'hidden': True})
    assert (await store.components(session))[0]['view'] == before


@pytest.mark.parametrize('change', [{'x': float('nan')}, {'width': True}, {'hidden': 1}, {'placement': 'outside'}, {'y': 3}, {'height': 2}, {'html': 'x'}, {}])
async def test_invalid_geometry_never_persists(config, store, change):
    _, session, _ = await fixture(config, store)
    with pytest.raises(UserError):
        await update_view(store, session, 'notes', 1, 0, change)
    assert not await store.rows('SELECT * FROM component_views')


async def test_owner_controls_and_agent_tools_use_the_same_layout_state(config):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        store, engine = app.state.store, app.state.engine
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317') as client:
            user = (await client.post('/api/auth/register', json={'username': 'surface-owner', 'password': 'test-password'})).json()['user']
            session = (await client.post('/api/sessions', json={})).json()['id']
            await engine.tools.registry.operate({'kind': 'component', 'action': 'register', 'id': 'notes', 'source': '<p>Notes</p>'}, session)
            endpoint = f'/api/sessions/{session}/components/notes/view'
            change = {'version': 1, 'view_revision': 0, 'view': {'hidden': True}}
            assert (await client.post(endpoint, json=change)).status_code == 200
            state = await engine.tools.execute('runtime', {'op': 'registry', 'args': {'kind': 'component', 'action': 'read', 'id': 'notes'}}, session, None)
            assert state['view'] == {'hidden': True} and state['view_revision'] == 1
            await engine.tools.execute('runtime', {'op': 'registry', 'args': {'kind': 'component', 'action': 'layout', 'id': 'notes', 'version': 1, 'view_revision': 1,
                                       'view': {'hidden': False, 'placement': 'workspace'}}}, session, None)
            component = (await client.get(f'/api/sessions/{session}')).json()['components'][0]
            assert component['view'] == {'hidden': False, 'placement': 'workspace'}
            assert (await client.post(endpoint, json=change)).json()['code'] == 'component_changed'
            await client.post('/api/auth/logout', json={})
            assert (await client.post(endpoint, json=change)).status_code == 401
            await client.post('/api/auth/register', json={'username': 'outsider', 'password': 'test-password'})
            assert (await client.post(endpoint, json=change)).status_code == 404
            await engine.reset_user(user['id'])
            assert not await store.rows('SELECT * FROM component_views')
            assert not await store.components(session)
            assert len(await store.rows('SELECT * FROM components')) == 2  # Versions remain recoverable.
