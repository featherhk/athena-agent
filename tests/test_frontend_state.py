"""Requirement tests exercise durable state, browser ownership and actual receipts."""
import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from athena.api import create_app
from athena.app_controls import (app_operation, acknowledge_app_action, claim_app_action,
                                 client_app_actions, cancel_app_actions)
from athena.auth import Auth, COOKIE, token_hash
from athena.errors import UserError
from athena.files import initialize_files
from athena.frontend_state import MAX_CLIENTS, FrontendObservation, observe
from athena.store import now
from frontend_fixtures import observation, observe_client


@pytest.fixture
async def surface(store):
    user, token = await Auth(store).register('surface-owner', 'test-password')
    session = await store.session(user_id=user['id'])
    await observe_client(store, user['id'], session, token)
    return user['id'], session, token


async def test_missing_or_old_client_is_unobservable_not_success(store):
    user, _ = await Auth(store).register('old-client', 'test-password')
    session = await store.session(user_id=user['id'])
    assert (await app_operation(store, session, None, {'action': 'list'}))['clients'] == []
    with pytest.raises(UserError, match='No fresh') as failure:
        await app_operation(store, session, None, {'action': 'open', 'target': 'history'})
    assert failure.value.code == 'frontend_unavailable'
    assert not await store.rows('SELECT * FROM app_actions')


async def test_only_one_fresh_foreground_same_session_is_automatic(store, surface):
    user, session, token = surface
    other_session = await store.session(user_id=user)
    await observe_client(store, user, session, token, 'background', foreground=False)
    await observe_client(store, user, other_session, token, 'other-conversation')
    action = await app_operation(store, session, None, {'action': 'open', 'target': 'history'})
    assert action['request']['target_client_id'] == 'browser-one'
    await observe_client(store, user, session, token, 'second-foreground')
    with pytest.raises(UserError) as error:
        await app_operation(store, session, None, {'action': 'open', 'target': 'history'})
    assert error.value.code == 'frontend_ambiguous'
    specific = await app_operation(store, session, None, {'action': 'open', 'target': 'history', 'client_id': 'second-foreground', 'expected_revision': 1})
    assert specific['request']['target_client_id'] == 'second-foreground'
    await store.execute("UPDATE frontend_clients SET expires_at='2000-01-01' WHERE client_id='browser-one'")
    stale = await app_operation(store, session, None, {'action': 'snapshot', 'client_id': 'browser-one'})
    assert stale['stale'] and stale['state']['session_id'] == session
    with pytest.raises(UserError) as error:
        await app_operation(store, session, None, {'action': 'close', 'target': 'history', 'client_id': 'browser-one'})
    assert error.value.code == 'frontend_unavailable'


async def test_observation_revisions_preserve_latest_state_and_allow_identical_heartbeat(store, surface):
    user, session, token = surface
    report = await observe_client(store, user, session, token, revision=3, open_views=['history'])
    heartbeat = await observe_client(store, user, session, token, revision=3, open_views=['history'])
    assert heartbeat['observed_at'] >= report['observed_at']
    for revision, views in ((2, []), (3, ['files'])):
        with pytest.raises(UserError) as error:
            await observe_client(store, user, session, token, revision=revision, open_views=views)
        assert error.value.code == 'frontend_conflict'
    assert (await app_operation(store, session, None, {'action': 'snapshot', 'client_id': 'browser-one'}))['state']['open_views'] == ['history']
    with pytest.raises(UserError) as error:
        await app_operation(store, session, None, {'action': 'open', 'target': 'files', 'client_id': 'browser-one', 'expected_revision': 2})
    assert error.value.code == 'frontend_conflict'


@pytest.mark.parametrize('change', [{'foreground': False}, {'open_views': ['files']}])
async def test_changed_or_background_client_cannot_claim_old_intent(store, surface, change):
    user, session, token = surface
    action = (await app_operation(store, session, None, {'action': 'open', 'target': 'history'}))['request']
    await observe_client(store, user, session, token, revision=2, **change)
    result = await claim_app_action(store, session, user, action['id'], 'browser-one', token_hash(token))
    assert not result['accepted'] and result['request']['status'] == 'failed'
    assert result['request']['error_code'] in ('frontend_conflict', 'frontend_unavailable')
    assert (await store.rows('SELECT type,payload FROM events WHERE session_id=? ORDER BY seq DESC LIMIT 1', (session,)))[0]['type'] == 'app_action'


async def test_account_and_auth_session_binding_cannot_be_claimed_or_reused(store, surface):
    user, session, token = surface
    other, other_token = await Auth(store).register('surface-other', 'test-password')
    other_session = await store.session(user_id=other['id'])
    await observe_client(store, other['id'], other_session, other_token, 'other-client')
    with pytest.raises(UserError):
        await observe_client(store, other['id'], other_session, other_token)
    with pytest.raises(UserError):
        await observe_client(store, user, other_session, token, 'foreign-session')
    with pytest.raises(UserError):
        await app_operation(store, session, None, {'action': 'snapshot', 'client_id': 'other-client'})
    replacement_token = await Auth(store).issue(user)
    action = (await app_operation(store, session, None, {'action': 'open', 'target': 'history'}))['request']
    with pytest.raises(UserError):
        await claim_app_action(store, session, user, action['id'], 'browser-one', token_hash(replacement_token))
    with pytest.raises(UserError):
        await claim_app_action(store, session, user, action['id'], 'other-client', token_hash(token))
    assert (await store.rows('SELECT status FROM app_actions WHERE id=?', (action['id'],)))[0]['status'] == 'pending'
    await store.execute('DELETE FROM auth_sessions WHERE token_hash=?', (token_hash(token),))
    assert (await app_operation(store, session, None, {'action': 'snapshot', 'client_id': 'browser-one'}))['stale']
    with pytest.raises(UserError):
        await observe_client(store, user, session, token, revision=2)


async def test_navigation_then_preview_and_open_stay_on_same_client_across_sessions(store, surface):
    user, origin, token = surface
    target_session = await store.session(user_id=user)
    navigation = (await app_operation(store, origin, None, {'action': 'navigate', 'session_id': target_session}))['request']
    await claim_app_action(store, origin, user, navigation['id'], 'browser-one', token_hash(token))
    with pytest.raises(UserError):
        await acknowledge_app_action(store, origin, user, navigation['id'], 'browser-one', 'applied', observed_revision=1)
    await observe_client(store, user, target_session, token, revision=2)
    receipt = await acknowledge_app_action(store, origin, user, navigation['id'], 'browser-one', 'applied', observed_revision=2)
    assert receipt['completed']
    await initialize_files(store)
    await store.execute('INSERT INTO file_entries VALUES (?,?,?,?,?,?,?,?,?)', ('report', user, origin, 'report.md', 'report.md', 3, 'text/markdown', 'output', now()))
    preview = (await app_operation(store, origin, None, {'action': 'preview', 'file_id': 'report', 'client_id': 'browser-one', 'expected_revision': 2}))['request']
    # The selected conversation's events no longer include origin actions. The
    # client-scoped feed must still deliver this new durable request.
    assert [row['id'] for row in await client_app_actions(store, user, 'browser-one', token_hash(token))] == [preview['id']]
    assert (await claim_app_action(store, origin, user, preview['id'], 'browser-one', token_hash(token)))['accepted']
    await observe_client(store, user, target_session, token, revision=3, open_views=['preview'], preview={'file_id': 'report', 'status': 'loading'})
    receipt = await acknowledge_app_action(store, origin, user, preview['id'], 'browser-one', 'applied', observed_revision=3)
    assert receipt['completed'] and receipt['request']['receipt']['file_ready'] is False
    assert receipt['request']['receipt']['file_status'] == 'loading'
    await observe_client(store, user, target_session, token, revision=4, open_views=['preview'], preview={'file_id': 'report', 'status': 'ready'})
    assert (await app_operation(store, origin, None, {'action': 'snapshot', 'client_id': 'browser-one'}))['state']['preview']['status'] == 'ready'
    opened = (await app_operation(store, origin, None, {'action': 'open', 'target': 'history', 'client_id': 'browser-one'}))['request']
    assert [row['id'] for row in await client_app_actions(store, user, 'browser-one', token_hash(token))] == [opened['id']]
    await claim_app_action(store, origin, user, opened['id'], 'browser-one', token_hash(token))
    await observe_client(store, user, target_session, token, revision=5, open_views=['history'])
    assert (await acknowledge_app_action(store, origin, user, opened['id'], 'browser-one', 'applied', observed_revision=5))['completed']


async def test_semantic_ack_is_not_just_transport_success_and_is_idempotent(store, surface):
    user, session, token = surface
    action = (await app_operation(store, session, None, {'action': 'open', 'target': 'history'}))['request']
    await claim_app_action(store, session, user, action['id'], 'browser-one', token_hash(token))
    for revision in (None, 1, 999):
        with pytest.raises(UserError):
            await acknowledge_app_action(store, session, user, action['id'], 'browser-one', 'applied', observed_revision=revision)
    await observe_client(store, user, session, token, revision=2, open_views=['history'])
    first = await acknowledge_app_action(store, session, user, action['id'], 'browser-one', 'applied', observed_revision=2)
    count = len(await store.rows('SELECT * FROM events'))
    second = await acknowledge_app_action(store, session, user, action['id'], 'browser-one', 'applied', observed_revision=2)
    assert first['changed'] and not second['changed']
    assert len(await store.rows('SELECT * FROM events')) == count
    second_token = await Auth(store).issue(user)
    with pytest.raises(UserError):
        await acknowledge_app_action(store, session, user, action['id'], 'browser-one', 'applied', observed_revision=2, auth_hash=token_hash(second_token))


async def test_download_receipt_does_not_claim_saved_file(store, surface):
    user, session, token = surface
    await initialize_files(store)
    await store.execute('INSERT INTO file_entries VALUES (?,?,?,?,?,?,?,?,?)', ('download-file', user, session, 'note.md', 'note.md', 1, 'text/markdown', 'output', now()))
    action = (await app_operation(store, session, None, {'action': 'download', 'file_id': 'download-file'}))['request']
    await claim_app_action(store, session, user, action['id'], 'browser-one')
    result = await acknowledge_app_action(store, session, user, action['id'], 'browser-one', 'applied', observed_revision=1)
    assert result['request']['receipt']['effect'] == 'download_requested'


async def test_reset_clears_old_state_revokes_late_heartbeat_and_preserves_other_accounts(store, surface):
    user, session, token = surface
    other, other_token = await Auth(store).register('reset-surface-other', 'test-password')
    other_session = await store.session(user_id=other['id'])
    await observe_client(store, other['id'], other_session, other_token, 'other')
    pending = await app_operation(store, session, None, {'action': 'open', 'target': 'history'})
    await store.call(lambda db: cancel_app_actions(db, user))
    assert (await app_operation(store, session, None, {'action': 'list'}))['clients'] == []
    with pytest.raises(UserError) as error:
        await observe_client(store, user, session, token, revision=2)
    assert error.value.code == 'frontend_identity_revoked'
    await observe_client(store, user, session, token, 'after-reset')
    assert (await app_operation(store, other_session, None, {'action': 'snapshot', 'client_id': 'other'}))['stale'] is False
    assert (await app_operation(store, session, None, {'action': 'read', 'id': pending['request']['id']}))['request']['error_code'] == 'reset'


async def test_cache_is_bounded_and_expired_clients_can_be_replaced(store, surface):
    user, session, token = surface
    for index in range(MAX_CLIENTS - 1):
        await observe_client(store, user, session, token, f'client-{index}')
    with pytest.raises(UserError):
        await observe_client(store, user, session, token, 'over-limit')
    await store.execute("UPDATE frontend_clients SET expires_at='2000-01-01' WHERE user_id=?", (user,))
    await observe_client(store, user, session, token, 'fresh-client')
    assert len((await app_operation(store, session, None, {'action': 'list'}))['clients']) == 1


@pytest.mark.parametrize('extra', [{'draft': 'secret'}, {'password': 'secret'}, {'dom': '<p>secret</p>'}, {'clipboard': 'secret'}, {'form_values': {'password': 'secret'}}, {'viewport': {'width': 20000, 'height': 1}}, {'open_views': ['unknown']}])
def test_observation_is_bounded_semantics_not_a_private_content_sink(extra):
    with pytest.raises(ValidationError):
        FrontendObservation.model_validate(observation('session', **extra))


async def test_api_observation_auth_validation_logout_and_targeted_receipt(config):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317') as browser:
            assert (await browser.post('/api/frontend/observation', json=observation(None))).status_code == 401
            user = (await browser.post('/api/auth/register', json={'username': 'observe-api', 'password': 'test-password'})).json()['user']['id']
            session = (await browser.post('/api/sessions', json={})).json()['id']
            assert (await browser.post('/api/frontend/observation', json=observation(session, password='never-store'))).status_code == 422
            first = await browser.post('/api/frontend/observation', json=observation(session))
            assert first.status_code == 200 and first.json()['revision'] == 1
            action = (await app_operation(app.state.store, session, None, {'action': 'open', 'target': 'history'}))['request']
            path = f'/api/sessions/{session}/app-actions/{action["id"]}'
            assert (await browser.post(path + '/claim', json={'client_id': 'browser-one'})).json()['accepted']
            invalid = await browser.post(path + '/ack', json={'client_id': 'browser-one', 'status': 'applied', 'observed_revision': 1})
            assert invalid.status_code == 400 and invalid.json()['code'] == 'frontend_conflict'
            await browser.post('/api/frontend/observation', json=observation(session, revision=2, open_views=['history']))
            assert (await browser.post(path + '/ack', json={'client_id': 'browser-one', 'status': 'applied', 'observed_revision': 2})).json()['completed']
            await browser.post('/api/auth/logout', json={})
            assert not await app.state.store.rows('SELECT * FROM frontend_clients WHERE user_id=?', (user,))
            assert (await browser.post('/api/frontend/observation', json=observation(session, revision=3))).status_code == 401


async def test_client_event_route_delivers_origin_action_while_showing_another_conversation(config):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        user, token = await Auth(app.state.store).register('stream-owner', 'test-password')
        origin = await app.state.store.session(user_id=user['id'])
        selected = await app.state.store.session(user_id=user['id'])
        await observe_client(app.state.store, user['id'], selected, token)
        requested = (await app_operation(app.state.store, origin, None, {'action': 'open', 'target': 'history', 'client_id': 'browser-one'}))['request']
        incoming = asyncio.Queue()
        await incoming.put({'type': 'http.request', 'body': b'', 'more_body': False})
        messages = []
        async def send(message):
            messages.append(message)
            if message['type'] == 'http.response.body' and b'"app_action"' in message.get('body', b''):
                await incoming.put({'type': 'http.disconnect'})
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': 'GET',
                 'scheme': 'http', 'path': '/api/frontend/clients/browser-one/events', 'raw_path': b'/api/frontend/clients/browser-one/events',
                 'query_string': b'', 'root_path': '', 'server': ('127.0.0.1', 4317), 'client': ('127.0.0.1', 1234),
                 'headers': [(b'host', b'127.0.0.1:4317'), (b'cookie', f'{COOKIE}={token}'.encode())]}
        await asyncio.wait_for(app(scope, incoming.get, send), 3)
        assert next(message for message in messages if message['type'] == 'http.response.start')['status'] == 200
        payload = b''.join(message.get('body', b'') for message in messages).decode()
        event = json.loads(payload.split('data: ', 1)[1].split('\n\n', 1)[0])
        assert event['payload']['id'] == requested['id'] and event['payload']['session_id'] == origin
        assert event['payload']['target_client_id'] == 'browser-one'


async def test_legacy_untargeted_pending_action_is_preserved_but_never_claimed(store, surface):
    user, session, token = surface
    await app_operation(store, session, None, {'action': 'list'})
    await store.execute("INSERT INTO app_actions(id,user_id,session_id,action,payload,status,created_at) VALUES (?,?,?,'open',?,'pending',?)", ('legacy-action', user, session, '{"target":"history"}', now()))
    with pytest.raises(UserError):
        await claim_app_action(store, session, user, 'legacy-action', 'browser-one', token_hash(token))
    assert (await app_operation(store, session, None, {'action': 'read', 'id': 'legacy-action'}))['request']['status'] == 'pending'
    assert await client_app_actions(store, user, 'browser-one', token_hash(token)) == []


async def test_applied_ack_requires_fresh_state_but_refresh_never_reexecutes_action(store, surface):
    user, session, token = surface
    action = (await app_operation(store, session, None, {'action': 'open', 'target': 'history'}))['request']
    await claim_app_action(store, session, user, action['id'], 'browser-one')
    await observe_client(store, user, session, token, revision=2, open_views=['history'])
    await store.execute("UPDATE frontend_clients SET expires_at='2000-01-01' WHERE client_id='browser-one'")
    with pytest.raises(UserError) as error:
        await acknowledge_app_action(store, session, user, action['id'], 'browser-one', 'applied', observed_revision=2)
    assert error.value.code == 'frontend_unavailable'
    assert (await app_operation(store, session, None, {'action': 'read', 'id': action['id']}))['request']['status'] == 'dispatched'
    await observe_client(store, user, session, token, revision=2, open_views=['history'])
    assert not (await claim_app_action(store, session, user, action['id'], 'browser-one'))['accepted']
    assert (await acknowledge_app_action(store, session, user, action['id'], 'browser-one', 'applied', observed_revision=2))['completed']


async def test_concurrent_out_of_order_reports_cannot_overwrite_newer_state(store, surface):
    user, session, token = surface
    results = await asyncio.gather(observe_client(store, user, session, token, revision=3, open_views=['files']),
                                   observe_client(store, user, session, token, revision=2, open_views=['history']), return_exceptions=True)
    assert any(isinstance(result, UserError) for result in results)
    state = await app_operation(store, session, None, {'action': 'snapshot', 'client_id': 'browser-one'})
    assert state['revision'] == 3 and state['state']['open_views'] == ['files']


async def test_ack_accepts_newer_preview_ready_observation_after_loading_report(store, surface):
    user, session, token = surface
    await initialize_files(store)
    await store.execute('INSERT INTO file_entries VALUES (?,?,?,?,?,?,?,?,?)', ('ready-race', user, session, 'report.md', 'report.md', 3, 'text/markdown', 'output', now()))
    await observe_client(store, user, session, token, revision=2)
    action = (await app_operation(store, session, None, {'action': 'preview', 'file_id': 'ready-race'}))['request']
    await claim_app_action(store, session, user, action['id'], 'browser-one')
    await observe_client(store, user, session, token, revision=3, open_views=['preview'], preview={'file_id': 'ready-race', 'status': 'loading'})
    await observe_client(store, user, session, token, revision=4, open_views=['preview'], preview={'file_id': 'ready-race', 'status': 'ready'})
    # The ACK was already prepared from report 3 while report 4 reached the server.
    for invalid in (1, 5):
        with pytest.raises(UserError):
            await acknowledge_app_action(store, session, user, action['id'], 'browser-one', 'applied', observed_revision=invalid)
    receipt = await acknowledge_app_action(store, session, user, action['id'], 'browser-one', 'applied', observed_revision=3)
    assert receipt['completed']
    assert receipt['request']['receipt'] == {'observed_revision': 4, 'reported_revision': 3,
                                           'observed_at': receipt['request']['receipt']['observed_at'],
                                           'effect': 'ui_state_observed', 'file_ready': True, 'file_status': 'ready'}


async def test_newer_observation_must_still_show_effect_before_accepting_racing_ack(store, surface):
    user, session, token = surface
    action = (await app_operation(store, session, None, {'action': 'open', 'target': 'history'}))['request']
    await claim_app_action(store, session, user, action['id'], 'browser-one')
    await observe_client(store, user, session, token, revision=2, open_views=['history'])
    await observe_client(store, user, session, token, revision=3, open_views=[])
    with pytest.raises(UserError):
        await acknowledge_app_action(store, session, user, action['id'], 'browser-one', 'applied', observed_revision=2)
    assert (await app_operation(store, session, None, {'action': 'read', 'id': action['id']}))['request']['status'] == 'dispatched'


async def test_same_account_cookie_rotation_retires_old_identity_without_cross_account_adoption(store, surface):
    user, session, token = surface
    other, other_token = await Auth(store).register('cookie-other', 'test-password')
    other_session = await store.session(user_id=other['id'])
    with pytest.raises(UserError) as other_error:
        await observe_client(store, other['id'], other_session, other_token)
    assert other_error.value.code == 'frontend_conflict'
    assert len((await app_operation(store, session, None, {'action': 'list'}))['clients']) == 1
    new_token = await Auth(store).issue(user)
    with pytest.raises(UserError) as same_error:
        await observe_client(store, user, session, new_token)
    assert same_error.value.code == 'frontend_identity_changed'
    assert (await app_operation(store, session, None, {'action': 'list'}))['clients'] == []
    with pytest.raises(UserError) as late_report:
        await observe_client(store, user, session, token, revision=2)
    assert late_report.value.code == 'frontend_identity_revoked'
    await observe_client(store, user, session, new_token, 'rotated-browser')
    request = await app_operation(store, session, None, {'action': 'open', 'target': 'history'})
    assert request['request']['target_client_id'] == 'rotated-browser'
