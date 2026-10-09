"""Desktop presentation has durable intent and separate, owned native receipts."""
import json

import httpx
import pytest
from pydantic import ValidationError

from athena.api import create_app
from athena.app_controls import app_operation
from athena.auth import Auth
from athena.component_views import update_view
from athena.errors import UserError
from athena.frontend_state import DesktopObservation, FrontendObservation
from athena.registry import Registry
from frontend_fixtures import observation, observe_client


async def setup(config, store):
    user, token = await Auth(store).register('desktop-owner', 'test-password')
    session = await store.session(user_id=user['id'])
    registry = Registry(config, store)
    result = await registry.operate({'kind': 'component', 'action': 'register', 'id': 'collector',
                                    'title': 'Collector', 'source': '<button>Collect</button>',
                                    'placement': 'desktop'}, session)
    return user['id'], session, token, registry, result


def native(*, components=None, presented=True):
    return {'platform': 'macos', 'supported': True, 'presented': presented,
            'screens': [{'id': '1', 'frame': {'x': 0, 'y': 0, 'width': 1728, 'height': 1117},
                         'visible_frame': {'x': 0, 'y': 37, 'width': 1728, 'height': 1015.5}, 'scale': 2}],
            'components': components or []}


def receipt(**change):
    return {'id': 'collector', 'version': 1, 'view_revision': 0, 'state': 'mounted',
            'visible': True, 'rect': {'x': 120.5, 'y': 32, 'width': 80, 'height': 64},
            'screen_id': '1', **change}


async def test_desktop_uses_external_versioned_files_and_durable_fork_isolated_layout(config, store):
    _, session, _, registry, registered = await setup(config, store)
    assert registered['placement'] == 'desktop' and registered['render']['state'] == 'unobserved'
    assert registered['render']['scope'] == 'document_load_only'
    row = (await store.rows('SELECT * FROM components WHERE session_id=?', (session,)))[0]
    source = config.data_dir / row['file_path']
    assert source.is_relative_to(config.data_dir / 'extensions')
    assert source.read_text() == '<button>Collect</button>'
    view = {'x': 1, 'y': 0.5, 'width': 64, 'height': 48, 'screen_id': '1', 'hidden': True}
    updated = await update_view(store, session, 'collector', 1, 0, view)
    assert updated['view'] == view and updated['view_revision'] == 1
    await store.initialize()
    fork = await store.fork(session)
    await update_view(store, fork, 'collector', 1, 1, {'hidden': False})
    assert (await store.components(session))[0]['view'] == view
    assert (await store.components(fork))[0]['view']['hidden'] is False
    assert (await registry.operate({'kind': 'component', 'action': 'read', 'id': 'collector'}, fork))['render']['state'] == 'unobserved'
    refreshed = await registry.operate({'kind': 'component', 'action': 'register', 'id': 'collector',
                                       'source': '<button>Updated</button>'}, session)
    assert refreshed['version'] == 2 and refreshed['view'] == view
    assert source.read_text() == '<button>Collect</button>'
    event = (await store.rows("SELECT * FROM events WHERE session_id=? AND type='component_view'", (session,)))[0]
    assert json.loads(event['payload'])['view'] == view


@pytest.mark.parametrize('change', [{'width': 31}, {'height': 16385}, {'screen_id': ''},
                                   {'screen_id': '../escape'}, {'screen_id': True}, {'x': 1.1},
                                   {'height': float('inf')}, {'width': True}])
async def test_desktop_rejects_invalid_layout_without_persisting(config, store, change):
    _, session, _, _, _ = await setup(config, store)
    with pytest.raises(UserError):
        await update_view(store, session, 'collector', 1, 0, change)
    assert not await store.rows('SELECT * FROM component_views')


async def test_desktop_geometry_cannot_leak_into_web_placement_or_source_registration(config, store):
    _, session, _, registry, _ = await setup(config, store)
    await update_view(store, session, 'collector', 1, 0, {'width': 64, 'height': 32, 'screen_id': '1'})
    with pytest.raises(UserError):
        await update_view(store, session, 'collector', 1, 1, {'placement': 'inline'})
    before = list((config.data_dir / 'extensions').rglob('*.html'))
    with pytest.raises(UserError):
        await registry.operate({'kind': 'component', 'action': 'register', 'id': 'collector',
                                'placement': 'inline', 'source': '<p>Other source</p>'}, session)
    assert list((config.data_dir / 'extensions').rglob('*.html')) == before
    assert len(await store.rows('SELECT * FROM components')) == 1
    changed = await update_view(store, session, 'collector', 1, 1,
                                {'placement': 'inline', 'width': None, 'height': None, 'screen_id': None})
    assert changed['view'] == {'placement': 'inline'}
    for change in ({'width': 64}, {'height': 32}, {'width': 2000}, {'screen_id': '1'}):
        with pytest.raises(UserError):
            await update_view(store, session, 'collector', 1, 2, change)
    changed = await update_view(store, session, 'collector', 1, 2,
                                {'placement': None, 'width': 16384, 'height': 32, 'screen_id': '1'})
    assert changed['view']['width'] == 16384 and 'placement' not in changed['view']


async def test_snapshot_distinguishes_registered_html_from_unsupported_or_native_display(config, store):
    user, session, token, registry, _ = await setup(config, store)
    await registry.report_component(session, 'collector', 1, 'document-instance', 'mounted', True, True)
    unsupported = {'platform': 'web', 'supported': False, 'presented': False, 'screens': [], 'components': []}
    await observe_client(store, user, session, token, desktop=unsupported)
    snapshot = await app_operation(store, session, None, {'action': 'snapshot', 'client_id': 'browser-one'})
    assert snapshot['desktop_registration'] == [{'id': 'collector', 'version': 1, 'view_revision': 0, 'view': {}}]
    assert snapshot['state']['desktop'] == unsupported
    assert 'pixel verification' in snapshot['scope']
    html = await registry.operate({'kind': 'component', 'action': 'read', 'id': 'collector'}, session)
    assert html['render']['state'] == 'mounted' and html['render']['scope'] == 'document_load_only'
    await observe_client(store, user, session, token, revision=2, desktop=native(components=[receipt()]))
    clients = (await app_operation(store, session, None, {'action': 'list'}))['clients']
    assert clients[0]['desktop'] == {'platform': 'macos', 'supported': True, 'presented': True}
    assert 'components' not in clients[0]['desktop']  # Cheap host discovery; detail is progressive.
    snapshot = await app_operation(store, session, None, {'action': 'snapshot', 'client_id': 'browser-one'})
    assert snapshot['state']['desktop']['components'][0]['rect']['x'] == 120.5
    # Desired layout advances independently; a truthful old visible version is
    # accepted and stays explicitly old rather than receiving the new revision.
    await update_view(store, session, 'collector', 1, 0, {'hidden': True})
    await registry.operate({'kind': 'component', 'action': 'register', 'id': 'collector',
                            'source': '<p>New</p>'}, session)
    await observe_client(store, user, session, token, revision=3, desktop=native(components=[receipt()]))
    snapshot = await app_operation(store, session, None, {'action': 'snapshot', 'client_id': 'browser-one'})
    assert snapshot['desktop_registration'][0]['version'] == 2
    assert snapshot['desktop_registration'][0]['view'] == {'hidden': True}
    assert snapshot['state']['desktop']['components'][0]['version'] == 1
    await observe_client(store, user, session, token, revision=4, foreground=False,
                         desktop=native(presented=False, components=[receipt(version=2, view_revision=1, state='hidden', visible=False)]))
    with pytest.raises(UserError):
        await app_operation(store, session, None, {'action': 'open', 'target': 'history'})


@pytest.mark.parametrize('change,code', [({'id': 'unregistered'}, 'not_found'), ({'version': 2}, 'not_found'),
                                       ({'view_revision': 1}, 'frontend_conflict')])
async def test_native_receipt_cannot_invent_a_component_or_future_version(config, store, change, code):
    user, session, token, _, _ = await setup(config, store)
    with pytest.raises(UserError) as error:
        await observe_client(store, user, session, token, desktop=native(components=[receipt(**change)]))
    assert error.value.code == code
    assert not await store.rows('SELECT * FROM frontend_clients')


async def test_native_receipt_cannot_reference_another_accounts_component(config, store):
    _, _, _, _, _ = await setup(config, store)
    user, token = await Auth(store).register('other-owner', 'test-password')
    session = await store.session(user_id=user['id'])
    with pytest.raises(UserError):
        await observe_client(store, user['id'], session, token, desktop=native(components=[receipt()]))
    assert not await store.rows('SELECT * FROM frontend_clients')


@pytest.mark.parametrize('desktop', [
    {'platform': 'web', 'supported': True, 'presented': False},
    {'platform': 'ios', 'supported': False, 'presented': True},
    {'platform': 'web', 'supported': False, 'presented': False, 'screens': native()['screens']},
    native(components=[receipt(screen_id='missing')]),
    native(components=[receipt(state='hidden')]),
    native(components=[receipt(state='failed')]),
    native(components=[receipt(error_code='private exception text')]),
    native(components=[receipt(), receipt()]),
    native(presented=False, components=[receipt()]),
    {**native(), 'screens': native()['screens'] * 2},
])
def test_impossible_or_unbounded_native_states_fail_validation(desktop):
    with pytest.raises(ValidationError):
        DesktopObservation.model_validate(desktop)


def test_native_failure_preserves_bounded_error_without_claiming_success():
    model = DesktopObservation.model_validate(native(components=[receipt(state='failed', visible=False, error_code='panel_failed')]))
    assert model.components[0].state == 'failed' and not model.components[0].visible
    assert DesktopObservation.model_validate({'platform': 'ios', 'supported': False, 'presented': False}).components == []


async def test_hidden_native_host_cannot_claim_foreground_even_if_webview_reports_visible(config, store):
    user, session, token, _, _ = await setup(config, store)
    # A hidden WKWebView can remain document-visible. Reject the contradictory
    # native observation instead of making it a source-affinity/action target.
    with pytest.raises(ValidationError):
        await observe_client(store, user, session, token, foreground=True, desktop=native(presented=False))
    assert not await store.rows('SELECT * FROM frontend_clients')
    await observe_client(store, user, session, token, foreground=False, desktop=native(presented=False))
    with pytest.raises(UserError) as error:
        await app_operation(store, session, None, {'action': 'open', 'target': 'history', 'client_id': 'browser-one'})
    assert error.value.code == 'frontend_unavailable'


async def test_old_client_heartbeats_keep_their_original_signature(config, store):
    user, session, token, _, _ = await setup(config, store)
    await observe_client(store, user, session, token)
    stored = json.loads((await store.rows('SELECT state FROM frontend_clients'))[0]['state'])
    assert 'desktop' not in stored
    assert (await observe_client(store, user, session, token))['accepted']
    assert 'desktop' not in (await app_operation(store, session, None, {'action': 'list'}))['clients'][0]
    assert FrontendObservation.model_validate(observation(session)).desktop is None


async def test_same_runtime_tool_and_authenticated_http_accept_desktop_without_new_tool(config):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317') as client:
            await client.post('/api/auth/register', json={'username': 'desktop-http', 'password': 'test-password'})
            session = (await client.post('/api/sessions', json={})).json()['id']
            tools = app.state.engine.tools
            registered = await tools.execute('runtime', {'op': 'registry', 'args': {
                'kind': 'component', 'action': 'register', 'id': 'collector', 'source': '<canvas></canvas>',
                'placement': 'desktop'}}, session, None)
            assert registered['version'] == 1
            changed = await tools.execute('runtime', {'op': 'registry', 'args': {
                'kind': 'component', 'action': 'layout', 'id': 'collector', 'version': 1, 'view_revision': 0,
                'view': {'screen_id': '1', 'width': 64, 'height': 64}}}, session, None)
            assert changed['view']['width'] == 64
            report = observation(session, desktop=native(components=[receipt(view_revision=1)]))
            response = await client.post('/api/frontend/observation', json=report)
            assert response.status_code == 200, response.text
            observed = await tools.execute('runtime', {'op': 'app', 'args': {'action': 'snapshot', 'client_id': 'browser-one'}}, session, None)
            assert observed['state']['desktop']['components'][0]['state'] == 'mounted'
            await client.post('/api/auth/logout', json={})
            assert (await client.post('/api/frontend/observation', json=report)).status_code == 401
