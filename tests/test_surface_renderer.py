"""Whole-session renderers remain ordinary recoverable component versions.

These tests verify the durable selection/observation contract. They do not claim
that registering HTML proves browser rendering or interaction succeeded.
"""
import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from athena.api import create_app
from athena.app_controls import app_operation
from athena.auth import Auth
from athena.component_views import surface_registration
from athena.frontend_state import FrontendObservation
from athena.registry import Registry
from frontend_fixtures import observation, observe_client


async def register(registry, session, name='canvas', **changes):
    return await registry.operate({'kind': 'component', 'action': 'register',
                                   'id': name, 'title': 'Conversation canvas',
                                   'source': '<main><canvas></canvas><textarea></textarea></main>',
                                   'placement': 'surface', **changes}, session)


async def fixture(config, store):
    user, token = await Auth(store).register('renderer-owner', 'test-password')
    session = await store.session(user_id=user['id'])
    return user, token, session, Registry(config, store)


async def test_one_selected_renderer_is_versioned_audited_and_not_inferred_mounted(config, store):
    _, _, session, registry = await fixture(config, store)
    first = await register(registry, session)
    assert first['surface_selected'] and first['render']['state'] == 'unobserved'
    second = await register(registry, session, 'alternate')
    assert second['surface_selected']
    components = await store.components(session)
    assert [item['id'] for item in components if item['surface_selected']] == ['alternate']
    assert all((config.data_dir / item['file_path']).is_file() for item in components)
    assert not any(config.repo_dir.rglob('*.html'))
    listed = await registry.operate({'kind': 'component', 'action': 'list'}, session)
    assert [item['id'] for item in listed['items'] if item['surface_selected']] == ['alternate']

    # Layout activation and HTML updates use the same durable selection rule.
    activated = await registry.operate({'kind': 'component', 'action': 'layout', 'id': 'canvas',
                                        'version': 1, 'view_revision': 0, 'view': {'hidden': False}}, session)
    assert activated['surface_selected']
    await register(registry, session, 'alternate', source='<main>Replacement</main>')
    assert (await store.call(lambda db: surface_registration(db, session)))['id'] == 'alternate'
    historical = await registry.operate({'kind': 'component', 'action': 'read', 'id': 'alternate', 'version': 1}, session)
    assert not historical['surface_selected'] and not historical['enabled']
    restored = await registry.operate({'kind': 'component', 'action': 'restore', 'id': 'alternate', 'version': 1}, session)
    assert restored['surface_selected'] and restored['render']['state'] == 'unobserved'
    changes = await store.rows('SELECT type,payload FROM events WHERE session_id=? ORDER BY seq', (session,))
    assert [item['type'] for item in changes] == ['registry_changed', 'registry_changed', 'component_view', 'registry_changed', 'registry_changed']
    assert json.loads(changes[-1]['payload'])['placement'] == 'surface'
    await store.initialize()
    assert [item['id'] for item in await store.components(session) if item['surface_selected']] == ['alternate']


async def test_effective_layout_hiding_and_concurrent_registration_select_only_one(config, store):
    _, _, session, registry = await fixture(config, store)
    await asyncio.gather(*(register(registry, session, name) for name in ('left', 'right')))
    components = await store.components(session)
    assert sum(item['surface_selected'] for item in components) == 1
    selected = next(item for item in components if item['surface_selected'])
    hidden = await registry.operate({'kind': 'component', 'action': 'layout', 'id': selected['id'],
                                     'version': 1, 'view_revision': 0, 'view': {'hidden': True}}, session)
    assert not hidden['surface_selected']
    # Updating source must not reopen a deliberately hidden renderer.
    assert not (await register(registry, session, selected['id']))['surface_selected']
    other = next(item for item in await store.components(session) if item['surface_selected'])
    override = await registry.operate({'kind': 'component', 'action': 'layout', 'id': other['id'],
                                       'version': 1, 'view_revision': 0, 'view': {'placement': 'inline'}}, session)
    assert not override['surface_selected']
    assert await store.call(lambda db: surface_registration(db, session)) == {'provider': 'default'}
    await registry.operate({'kind': 'component', 'action': 'layout', 'id': other['id'],
                            'version': 1, 'view_revision': 1, 'view': {'placement': None}}, session)
    assert (await store.call(lambda db: surface_registration(db, session)))['id'] == other['id']
    assert not (await registry.operate({'kind': 'component', 'action': 'remove', 'id': other['id']}, session))['surface_selected']
    assert await store.call(lambda db: surface_registration(db, session)) == {'provider': 'default'}


async def test_equal_timestamps_have_stable_id_tiebreak_without_touching_history(config, store):
    _, _, session, registry = await fixture(config, store)
    await register(registry, session, 'a')
    await register(registry, session, 'z')
    await store.execute('UPDATE components SET created_at=? WHERE session_id=?', ('2026-01-01T00:00:00+00:00', session))
    assert (await store.call(lambda db: surface_registration(db, session)))['id'] == 'z'
    assert len(await store.rows('SELECT * FROM components WHERE session_id=?', (session,))) == 2


async def test_layout_can_promote_an_existing_card_without_forking_its_source(config, store):
    _, _, session, registry = await fixture(config, store)
    await register(registry, session, placement='inline')
    original = (await store.components(session))[0]
    promoted = await registry.operate({'kind': 'component', 'action': 'layout', 'id': 'canvas', 'version': 1,
                                       'view_revision': 0, 'view': {'placement': 'surface'}}, session)
    assert promoted['surface_selected'] and promoted['version'] == 1
    assert (await store.components(session))[0]['html'] == original['html']
    await registry.operate({'kind': 'component', 'action': 'register', 'id': 'canvas', 'source': '<main>Revised</main>'}, session)
    selected = (await store.components(session))[0]
    assert selected['surface_selected'] and selected['placement'] == 'inline'
    assert selected['view']['placement'] == 'surface'


async def test_fork_copies_renderer_but_never_its_browser_receipts_and_reset_retains_sources(config, engine, store):
    user, token, session, registry = await fixture(config, store)
    message = await store.message(session, None, 'user', 'Keep this conversation after resetting the canvas.')
    await register(registry, session)
    original = (await store.components(session))[0]
    await registry.report_component(session, 'canvas', 1, 'original-frame', 'mounted', True, True)
    await observe_client(store, user['id'], session, token,
                         surface={'provider': 'component', 'id': 'canvas', 'version': 1, 'view_revision': 0, 'state': 'mounted'})
    fork = await store.fork(session)
    child = await registry.operate({'kind': 'component', 'action': 'read', 'id': 'canvas'}, fork)
    assert child['surface_selected'] and child['render']['state'] == 'unobserved'
    assert not await store.rows('SELECT * FROM frontend_clients WHERE session_id=?', (fork,))
    await registry.operate({'kind': 'component', 'action': 'layout', 'id': 'canvas', 'version': 1,
                            'view_revision': 0, 'view': {'hidden': True}}, fork)
    assert (await store.call(lambda db: surface_registration(db, session)))['id'] == 'canvas'
    assert await store.call(lambda db: surface_registration(db, fork)) == {'provider': 'default'}
    await engine.reset_user(user['id'])
    assert not await store.components(session) and not await store.components(fork)
    assert await store.call(lambda db: surface_registration(db, session)) == {'provider': 'default'}
    assert (config.data_dir / original['file_path']).is_file()
    assert (await store.rows('SELECT content FROM messages WHERE id=?', (message,)))[0]['content'].startswith('Keep this conversation')
    # Reset adds disabling versions, retaining the original and fork snapshots.
    assert len(await store.rows('SELECT * FROM components WHERE id=?', ('canvas',))) == 4


async def test_authenticated_observations_distinguish_desired_actual_old_and_failed_renderers(config):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        store, registry = app.state.store, app.state.engine.tools.registry
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317') as browser:
            await browser.post('/api/auth/register', json={'username': 'renderer-http-owner', 'password': 'test-password'})
            session = (await browser.post('/api/sessions', json={})).json()['id']
            registered = await app.state.engine.tools.execute('update_frontend', {
                'op': 'upsert', 'id': 'canvas', 'title': 'Canvas', 'placement': 'surface',
                'html': '<main><canvas></canvas></main>'}, session, None)
            assert registered['surface_selected'] and registered['render']['state'] == 'unobserved'
            desired = {'provider': 'component', 'id': 'canvas', 'version': 1, 'view_revision': 0}
            report = observation(session, surface={**desired, 'state': 'mounted'})
            assert (await browser.post('/api/frontend/observation', json=report)).status_code == 200
            current = await app_operation(store, session, None, {'action': 'snapshot', 'client_id': 'browser-one'})
            assert current['surface_registration'] == desired
            assert current['state']['surface']['state'] == 'mounted'
            assert (await browser.get(f'/api/sessions/{session}')).json()['components'][0]['surface_selected']
            await register(registry, session, source='<main>New render generation</main>')
            # A browser showing v1 remains a truthful, identifiable old report.
            assert (await browser.post('/api/frontend/observation', json=report)).status_code == 200
            current = await app_operation(store, session, None, {'action': 'snapshot', 'client_id': 'browser-one'})
            assert current['surface_registration']['version'] == 2
            assert current['state']['surface']['version'] == 1
            assert (await registry.operate({'kind': 'component', 'action': 'read', 'id': 'canvas'}, session))['render']['state'] == 'unobserved'

            # Frame failure and default fallback are different facts, not success.
            failed = {'version': 2, 'instance': 'failed-frame', 'state': 'failed',
                      'has_content': False, 'in_view': False, 'error_code': 'script_error'}
            assert (await browser.post(f'/api/sessions/{session}/components/canvas/report', json=failed)).status_code == 200
            fallback = observation(session, revision=2, surface={'provider': 'default', 'state': 'mounted'})
            assert (await browser.post('/api/frontend/observation', json=fallback)).status_code == 200
            current = await app_operation(store, session, None, {'action': 'snapshot', 'client_id': 'browser-one'})
            assert current['surface_registration']['version'] == 2 and current['state']['surface']['provider'] == 'default'
            assert (await registry.operate({'kind': 'component', 'action': 'read', 'id': 'canvas'}, session))['render']['state'] == 'failed'

            missing = observation(session, revision=3, surface={**desired, 'version': 999, 'state': 'mounted'})
            rejected = await browser.post('/api/frontend/observation', json=missing)
            assert rejected.status_code == 400 and rejected.json()['code'] == 'not_found'
            await browser.post('/api/auth/logout', json={})
            assert (await browser.post('/api/frontend/observation', json=report)).status_code == 401
            await browser.post('/api/auth/register', json={'username': 'renderer-other', 'password': 'test-password'})
            other = (await browser.post('/api/sessions', json={})).json()['id']
            rejected = await browser.post('/api/frontend/observation', json=observation(other, client_id='other-browser', surface={**desired, 'state': 'mounted'}))
            assert rejected.status_code == 400 and rejected.json()['code'] == 'not_found'


@pytest.mark.parametrize('surface', [
    {'provider': 'component', 'state': 'mounted'},
    {'provider': 'default', 'state': 'failed'},
    {'provider': 'default', 'state': 'mounted', 'id': 'canvas'},
    {'provider': 'component', 'id': 'canvas', 'version': 1, 'view_revision': 0, 'state': 'failed'},
    {'provider': 'component', 'id': 'canvas', 'version': 1, 'view_revision': 0, 'state': 'mounted', 'error_code': 'script_error'},
    {'provider': 'component', 'id': 'canvas', 'version': 1, 'view_revision': 0, 'state': 'failed', 'error_code': 'private exception text'},
])
def test_renderer_observation_rejects_inconsistent_states_and_private_exception_payloads(surface):
    with pytest.raises(ValidationError):
        FrontendObservation.model_validate(observation('session', surface=surface))


async def test_older_clients_do_not_prove_default_renderer_is_visible(config, store):
    user, token, session, _ = await fixture(config, store)
    await observe_client(store, user['id'], session, token)
    current = await app_operation(store, session, None, {'action': 'snapshot', 'client_id': 'browser-one'})
    assert current['surface_registration'] == {'provider': 'default'}
    assert current['state']['surface'] is None
    assert 'surface' not in json.loads((await store.rows('SELECT state FROM frontend_clients'))[0]['state'])
    # A pre-upgrade browser heartbeat has the same payload and revision. The
    # optional field must not change its signature and strand it in conflicts.
    accepted = await observe_client(store, user['id'], session, token)
    assert accepted['accepted'] and accepted['revision'] == 1
