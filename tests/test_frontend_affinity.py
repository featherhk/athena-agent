"""A UI request follows the authenticated window that sent its user input."""
import asyncio
import json

import httpx
import pytest

from athena.api import create_app
from athena.app_controls import (acknowledge_app_action, app_operation, claim_app_action,
                                 client_app_actions, list_app_actions)
from athena.auth import Auth, COOKIE, token_hash
from athena.errors import UserError
from athena.files import initialize_files
from athena.frontend_state import capture_source, clear, observe, source_summary
from athena.store import Store, now, uid
from athena.task_events import enqueue_task
from athena.user_help import activate_help, answer_help, request_help
from frontend_fixtures import observation, observe_client


async def setup(store):
    user, token = await Auth(store).register('affinity-owner', 'test-affinity-password')
    session = await store.session(user_id=user['id'])
    await observe_client(store, user['id'], session, token, 'window-one')
    await observe_client(store, user['id'], session, token, 'window-two')
    return user['id'], session, token


async def bound_run(store, session, token, client_id='window-one'):
    source = await store.call(lambda db: capture_source(db, session, client_id, token_hash(token)))
    return await store.enqueue(session, 'Use my window', frontend_source=source)


async def input_clear_fixture(store, *, unsent=True, sourced=True, target_client='window-one'):
    owner, session, token = await setup(store)
    await observe_client(store, owner, session, token, target_client, revision=4, has_unsent_changes=unsent)
    run = (await bound_run(store, session, token) if sourced
           else await store.enqueue(session, 'Legacy input'))
    await store.claim(session)
    await initialize_files(store)
    await store.execute('INSERT INTO file_entries VALUES (?,?,?,?,?,?,?,?,?)',
                        ('input-clear-file', owner, session, 'note.md', 'note.md', 3, 'text/markdown', 'output', now()))
    return owner, session, token, run


@pytest.mark.parametrize('action', ['open', 'close', 'preview', 'navigate'])
async def test_source_input_clear_rebases_one_revision_and_verifies_effect_without_replay(store, action):
    owner, session, token, run = await input_clear_fixture(store)
    target = await store.session(user_id=owner)
    args = ({'target': 'history'} if action in ('open', 'close') else
            {'file_id': 'input-clear-file'} if action == 'preview' else {'session_id': target})
    requested = await app_operation(store, session, run, {'action': action, **args})
    identifier = requested['request']['id']
    assert requested['request']['expected_revision'] == 4
    stored = json.loads((await store.rows('SELECT payload FROM app_actions WHERE id=?', (identifier,)))[0]['payload'])
    assert stored['_claim_guard']['revision'] == 4
    public = [requested, await app_operation(store, session, run, {'action': 'list'}),
              await app_operation(store, session, run, {'action': 'read', 'id': identifier}),
              await list_app_actions(store, session, owner),
              await client_app_actions(store, owner, 'window-one', token_hash(token))]
    assert '_claim_guard' not in json.dumps(public) and stored['_claim_guard']['state_hash'] not in json.dumps(public)
    await observe_client(store, owner, session, token, 'window-one', revision=5, has_unsent_changes=False)
    await store.execute("UPDATE runs SET status='complete',finished_at=? WHERE id=?", (now(), run))
    # A second Store models restart; the guard is durable and claims still have
    # exactly one owner even if duplicate streams deliver the pending request.
    reopened = Store(store.path)
    try:
        await reopened.initialize()
        claims = await asyncio.gather(*(claim_app_action(reopened, session, owner, identifier,
                                       'window-one', token_hash(token)) for _ in range(2)))
        assert sum(item['accepted'] for item in claims) == 1
        assert all(item['request']['expected_revision'] == 5 for item in claims)
        effect = ({'open_views': ['history']} if action == 'open' else
                  {'open_views': ['preview'], 'preview': {'file_id': 'input-clear-file', 'status': 'ready'}}
                  if action == 'preview' else {})
        await observe_client(store, owner, target if action == 'navigate' else session, token,
                             'window-one', revision=6, **effect)
        with pytest.raises(UserError) as old_ack:
            await acknowledge_app_action(reopened, session, owner, identifier, 'window-one',
                                         'applied', observed_revision=4, auth_hash=token_hash(token))
        assert old_ack.value.code == 'frontend_conflict'
        applied = await acknowledge_app_action(reopened, session, owner, identifier, 'window-one',
                                              'applied', observed_revision=6, auth_hash=token_hash(token))
        assert applied['completed'] and applied['request']['receipt']['observed_revision'] == 6
        read = await app_operation(reopened, session, run, {'action': 'read', 'id': identifier})
        events = await reopened.rows("SELECT payload FROM events WHERE type='app_action'")
        assert '_claim_guard' not in json.dumps([claims, applied, read, events])
        assert stored['_claim_guard']['state_hash'] not in json.dumps([claims, applied, read, events])
    finally:
        await reopened.close()


@pytest.mark.parametrize('change', [
    'explicit_revision', 'skipped_revision', 'still_unsent', 'new_unsent', 'attachments',
    'navigation', 'foreground', 'views', 'viewport', 'preview', 'workspace', 'component',
    'waiting', 'errors', 'other_client', 'unsourced', 'auth_rotation',
])
async def test_input_clear_exception_does_not_erase_other_preconditions(store, change):
    client_id = 'window-two' if change == 'other_client' else 'window-one'
    owner, session, token, run = await input_clear_fixture(
        store, unsent=change != 'new_unsent', sourced=change != 'unsourced', target_client=client_id)
    args = {'action': 'open', 'target': 'history'}
    if change == 'explicit_revision':
        args['expected_revision'] = 4
    if change in ('other_client', 'unsourced'):
        args['client_id'] = client_id
    requested = await app_operation(store, session, run, args)
    identifier = requested['request']['id']
    state = {'has_unsent_changes': change in ('still_unsent', 'new_unsent')}
    revision, observed_session = (6 if change == 'skipped_revision' else 5), session
    if change == 'attachments':
        state['attachment_ids'] = ['input-clear-file']
    elif change == 'navigation':
        observed_session = await store.session(user_id=owner)
    elif change == 'foreground':
        state['foreground'] = False
    elif change == 'views':
        state['open_views'] = ['files']
    elif change == 'viewport':
        state['viewport'] = {'width': 800, 'height': 800}
    elif change == 'preview':
        state['preview'] = {'file_id': 'input-clear-file', 'status': 'loading'}
    elif change == 'workspace':
        state['workspace'] = {'id': 'table', 'version': 1}
    elif change == 'component':
        state['components'] = [{'id': 'table', 'version': 1, 'view_revision': 1, 'placement': 'inline',
                                'rect': {'x': 0, 'y': 0, 'width': 100, 'height': 100},
                                'visible': True, 'state': 'mounted'}]
    elif change == 'waiting':
        state['waiting'] = [{'kind': 'upload'}]
    elif change == 'errors':
        state['errors'] = [{'source': 'connection', 'code': 'connection_lost'}]
    elif change == 'auth_rotation':
        token = await Auth(store).issue(owner)
        await store.execute('DELETE FROM frontend_clients WHERE client_id=?', (client_id,))
    await observe_client(store, owner, observed_session, token, client_id, revision=revision, **state)
    result = await claim_app_action(store, session, owner, identifier, client_id, token_hash(token))
    assert not result['accepted'] and result['request']['status'] == 'failed'
    assert result['request']['expected_revision'] == 4
    assert result['request']['error_code'] == ('frontend_unavailable' if change == 'foreground' else 'frontend_conflict')


@pytest.mark.parametrize('action', ['download', 'sign_out', 'attach', 'detach'])
async def test_input_clear_does_not_relax_effectful_or_attachment_actions(store, action):
    owner, session, token, run = await input_clear_fixture(store)
    args = {'action': action}
    if action != 'sign_out':
        args['file_id'] = 'input-clear-file'
    request = (await app_operation(store, session, run, args))['request']
    assert '_claim_guard' not in (await store.rows('SELECT payload FROM app_actions WHERE id=?', (request['id'],)))[0]['payload']
    await observe_client(store, owner, session, token, 'window-one', revision=5)
    claim = await claim_app_action(store, session, owner, request['id'], 'window-one', token_hash(token))
    assert not claim['accepted'] and claim['request']['error_code'] == 'frontend_conflict'


async def test_two_queued_inputs_in_one_conversation_keep_distinct_sources(engine, store, monkeypatch):
    owner, session, token = await setup(store)
    monkeypatch.setattr(engine, 'kick', lambda _: None)
    first, second = await asyncio.gather(*(
        engine.submit(session, client, frontend_client_id=client, frontend_auth_hash=token_hash(token))
        for client in ('window-one', 'window-two')))
    sources = {row['run_id']: row for row in await store.rows('SELECT * FROM run_frontend_sources')}
    assert sources[first]['client_id'] == 'window-one' and sources[second]['client_id'] == 'window-two'
    assert all(row['auth_hash'] == token_hash(token) and row['user_id'] == owner for row in sources.values())
    assert token not in json.dumps(sources)
    assert await store.claim(session) == first
    action = await app_operation(store, session, first, {'action': 'open', 'target': 'history'})
    assert action['request']['target_client_id'] == 'window-one'
    await store.execute("UPDATE runs SET status='complete',finished_at=? WHERE id=?", (now(), first))
    assert await store.claim(session) == second
    action = await app_operation(store, session, second, {'action': 'open', 'target': 'files'})
    assert action['request']['target_client_id'] == 'window-two'


async def test_navigation_preserves_source_default_without_following_another_window(store):
    owner, session, token = await setup(store)
    target = await store.session(user_id=owner)
    run = await bound_run(store, session, token)
    await store.claim(session)
    navigation = await app_operation(store, session, run, {'action': 'navigate', 'session_id': target})
    assert navigation['request']['target_client_id'] == 'window-one'
    await observe_client(store, owner, target, token, 'window-one', revision=2)
    next_action = await app_operation(store, session, run, {'action': 'open', 'target': 'history'})
    assert next_action['request']['target_client_id'] == 'window-one'
    stored = (await store.rows('SELECT target_session_id FROM app_actions WHERE id=?', (next_action['request']['id'],)))[0]
    assert stored['target_session_id'] == target
    listing = await app_operation(store, session, run, {'action': 'list'})
    assert listing['source'] == {'client_id': 'window-one', 'stale': False, 'available': True}
    assert token not in json.dumps(listing) and token_hash(token) not in json.dumps(listing)


@pytest.mark.parametrize('invalid', ['missing', 'foreign_account', 'new_auth', 'wrong_session', 'expired', 'background'])
async def test_invalid_source_does_not_interrupt_or_enqueue(engine, store, monkeypatch, invalid):
    owner, session, token = await setup(store)
    client_id, auth_hash = 'window-one', token_hash(token)
    old = await store.enqueue(session, 'Keep this queued work')
    if invalid == 'missing':
        client_id = 'unknown-window'
    elif invalid == 'foreign_account':
        other, other_token = await Auth(store).register('another-account', 'test-affinity-password')
        other_session = await store.session(user_id=other['id'])
        await observe_client(store, other['id'], other_session, other_token, 'foreign-window')
        client_id = 'foreign-window'
    elif invalid == 'new_auth':
        auth_hash = token_hash(await Auth(store).issue(owner))
    elif invalid == 'wrong_session':
        elsewhere = await store.session(user_id=owner)
        await observe_client(store, owner, elsewhere, token, revision=2, client_id=client_id)
    elif invalid == 'expired':
        await store.execute("UPDATE frontend_clients SET expires_at='2000-01-01' WHERE client_id=?", (client_id,))
    else:
        await observe_client(store, owner, session, token, client_id, revision=2, foreground=False)
    monkeypatch.setattr(engine, 'kick', lambda _: None)
    with pytest.raises(UserError) as failed:
        await engine.submit(session, 'Interrupt', 'interrupt', frontend_client_id=client_id, frontend_auth_hash=auth_hash)
    assert failed.value.code == 'frontend_unavailable'
    assert (await store.rows('SELECT id,status FROM runs')) == [{'id': old, 'status': 'queued'}]
    assert not await store.rows('SELECT * FROM run_frontend_sources')


@pytest.mark.parametrize('loss', ['expiry', 'reset', 'auth_rotation', 'background'])
async def test_unavailable_source_never_falls_back_but_explicit_target_still_works(store, loss):
    owner, session, token = await setup(store)
    run = await bound_run(store, session, token)
    await store.claim(session)
    if loss == 'expiry':
        await store.execute("UPDATE frontend_clients SET expires_at='2000-01-01' WHERE client_id='window-one'")
    elif loss == 'reset':
        await store.call(lambda db: clear(db, owner))
        await observe_client(store, owner, session, token, 'after-reset')
    elif loss == 'auth_rotation':
        new_token = await Auth(store).issue(owner)
        # A cached client ID cannot transfer a prior run to another login.
        await store.execute("DELETE FROM frontend_clients WHERE client_id='window-one'")
        await observe_client(store, owner, session, new_token, 'window-one')
    else:
        await observe_client(store, owner, session, token, 'window-one', revision=2, foreground=False)
    with pytest.raises(UserError) as failed:
        await app_operation(store, session, run, {'action': 'open', 'target': 'history'})
    assert failed.value.code == 'frontend_unavailable'
    assert not await store.rows('SELECT * FROM app_actions')
    listing = await app_operation(store, session, run, {'action': 'list'})
    assert listing['source']['available'] is False
    explicit = 'after-reset' if loss == 'reset' else 'window-two'
    result = await app_operation(store, session, run, {'action': 'open', 'target': 'history', 'client_id': explicit})
    assert result['request']['target_client_id'] == explicit


async def test_legacy_unsourced_runs_keep_existing_unique_candidate_rule(store):
    owner, session, token = await setup(store)
    run = await store.enqueue(session, 'Old client request')
    await store.claim(session)
    with pytest.raises(UserError) as failed:
        await app_operation(store, session, run, {'action': 'open', 'target': 'history'})
    assert failed.value.code == 'frontend_ambiguous'
    await observe_client(store, owner, session, token, 'window-two', revision=2, foreground=False)
    action = await app_operation(store, session, run, {'action': 'open', 'target': 'history'})
    assert action['request']['target_client_id'] == 'window-one'
    assert (await app_operation(store, session, run, {'action': 'list'}))['source'] is None


async def test_source_cannot_be_rebound_to_another_input_scope_or_autonomous_origin(store):
    owner, session, token = await setup(store)
    elsewhere = await store.session(user_id=owner)
    source = await store.call(lambda db: capture_source(db, session, 'window-one', token_hash(token)))
    assert token_hash(token) not in repr(source)
    with pytest.raises(UserError):
        await store.enqueue(elsewhere, 'Wrong conversation', frontend_source=source)
    with pytest.raises(ValueError, match='Autonomous'):
        await store.call(lambda db: Store._enqueue(db, session, 'Bad inherited source', origin={'kind': 'task'}, frontend_source=source))
    assert not await store.rows('SELECT * FROM runs') and not await store.rows('SELECT * FROM messages')


async def test_help_answer_from_another_window_does_not_rebind_resumed_run(config):
    path = config.data_dir / 'affinity-restart.sqlite3'
    store = Store(path)
    await store.initialize()
    owner, session, token = await setup(store)
    run = await bound_run(store, session, token)
    await store.claim(session)
    help_request = await request_help(store, session, run, {'questions': [{'id': 'city', 'question': 'Which city?'}]})
    await activate_help(store, session, run, help_request['request']['id'], [])
    original = await store.rows('SELECT * FROM run_frontend_sources')
    await store.close()
    restored = Store(path)
    await restored.initialize()
    try:
        assert await restored.rows('SELECT * FROM run_frontend_sources') == original
        # A reply is account-owned; its sending surface does not redefine the
        # originating task. The resumed model may explicitly target another client.
        await observe_client(restored, owner, session, token, 'window-two', revision=2)
        await answer_help(restored, session, owner, help_request['request']['id'], {'city': 'Shanghai'})
        assert await restored.claim(session) == run
        result = await app_operation(restored, session, run, {'action': 'open', 'target': 'history'})
        assert result['request']['target_client_id'] == 'window-one'
        assert await restored.rows('SELECT * FROM run_frontend_sources') == original
    finally:
        await restored.close()


async def test_fork_wake_and_delegation_do_not_copy_browser_control(engine, store):
    owner, session, token = await setup(store)
    original = await bound_run(store, session, token)
    await store.claim(session)
    job = await engine.tools.agents.spawn(session, original, {'name': 'research', 'title': 'Research', 'prompt': 'Check one thing'})
    child = job['agent']
    assert await store.call(lambda db: source_summary(db, owner, child['child_session_id'], child['child_run_id'])) is None
    task = {'id': uid(), 'session_id': session, 'title': 'Reminder', 'prompt': 'Check later', 'occurrence': 0}
    await store.execute("INSERT INTO tasks(id,session_id,title,prompt,due_at,status,created_at) VALUES (?,?,?,?,?,'active',?)", (task['id'], session, task['title'], task['prompt'], now(), now()))
    wake, _ = await store.call(lambda db: enqueue_task(db, task))
    assert await store.call(lambda db: source_summary(db, owner, session, wake)) is None
    await store.execute("UPDATE runs SET status='complete',finished_at=?", (now(),))
    fork = await store.fork(session)
    fork_run = await store.enqueue(fork, 'A new branch')
    assert await store.call(lambda db: source_summary(db, owner, fork, fork_run)) is None
    assert await store.call(lambda db: source_summary(db, owner, fork, original)) is None
    assert len(await store.rows('SELECT * FROM run_frontend_sources')) == 1


async def test_api_binds_current_cookie_and_keeps_private_provenance_out_of_snapshot(config, monkeypatch):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        monkeypatch.setattr(app.state.engine, 'kick', lambda _: None)
        transport = httpx.ASGITransport(app)
        async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1:4317') as browser:
            user = (await browser.post('/api/auth/register', json={'username': 'api-affinity', 'password': 'test-affinity-password'})).json()['user']
            session = (await browser.post('/api/sessions', json={})).json()['id']
            assert (await browser.post('/api/frontend/observation', json=observation(session, 'real-window'))).status_code == 200
            sent = await browser.post(f'/api/sessions/{session}/messages', json={'text': 'Use this window', 'frontend_client_id': 'real-window'})
            assert sent.status_code == 200
            run = sent.json()['run_id']
            stored = (await app.state.store.rows('SELECT * FROM run_frontend_sources WHERE run_id=?', (run,)))[0]
            assert stored['auth_hash'] == token_hash(browser.cookies[COOKIE]) and stored['user_id'] == user['id']
            snapshot = (await browser.get(f'/api/sessions/{session}')).json()
            assert stored['auth_hash'] not in json.dumps(snapshot) and browser.cookies[COOKIE] not in json.dumps(snapshot)
            # A separate same-account login cannot send on behalf of this client.
            async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1:4317') as other:
                await other.post('/api/auth/login', json={'username': 'api-affinity', 'password': 'test-affinity-password'})
                rejected = await other.post(f'/api/sessions/{session}/messages', json={'text': 'Wrong login', 'frontend_client_id': 'real-window', 'mode': 'interrupt'})
                assert rejected.status_code == 400 and rejected.json()['code'] == 'frontend_unavailable'
            assert len(await app.state.store.rows('SELECT * FROM runs')) == 1
            assert (await app.state.store.rows('SELECT status FROM runs'))[0]['status'] == 'queued'


async def test_engine_executes_default_ui_action_on_message_source_without_prompt_credentials(engine, store, monkeypatch):
    # This fixture has no consuming browser; exercise the bounded pending result.
    monkeypatch.setattr('athena.app_controls.RECEIPT_WAIT_SECONDS', .02)
    owner, session, token = await setup(store)
    await observe_client(store, owner, session, token, 'window-two', revision=2, has_unsent_changes=True)
    payloads = []
    async def provider(client, base, key, payload, *args, **kwargs):
        payloads.append(payload)
        message = {'role': 'assistant', 'content': '', 'reasoning_content': ''}
        if len(payloads) == 1:
            message['tool_calls'] = [{'id': 'open-history', 'type': 'function', 'function': {
                'name': 'runtime', 'arguments': json.dumps({'op': 'app', 'args': {'action': 'open', 'target': 'history'}})}}]
        else:
            message['content'] = 'Requested the history view.'
        return {'message': message, 'usage': {}}
    monkeypatch.setattr('athena.engine.complete', provider)
    run = await engine.submit(session, 'Open history here', frontend_client_id='window-two', frontend_auth_hash=token_hash(token))
    await asyncio.wait_for(asyncio.gather(*engine.actors.values()), 3)
    action = (await store.rows('SELECT target_client_id,status FROM app_actions WHERE run_id=?', (run,)))[0]
    assert action == {'target_client_id': 'window-two', 'status': 'pending'}
    assert '_claim_guard' in (await store.rows('SELECT payload FROM app_actions WHERE run_id=?', (run,)))[0]['payload']
    assert '_claim_guard' not in json.dumps(payloads)
    assert len(payloads) == 2 and token not in json.dumps(payloads) and token_hash(token) not in json.dumps(payloads)


async def test_schema_13_upgrade_adds_only_empty_source_table_and_preserves_original_rows(config):
    path = config.data_dir / 'affinity-upgrade.sqlite3'
    store = Store(path)
    await store.initialize()
    _, session, _ = await setup(store)
    run = await store.enqueue(session, 'An existing conversation')
    await store.execute("UPDATE runs SET status='complete',finished_at=? WHERE id=?", (now(), run))
    await store.execute('DROP TABLE run_frontend_sources')
    await store.execute('PRAGMA user_version=13')
    def originals(db, tables=None):
        if tables is None:
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {table: [tuple(row) for row in db.execute('SELECT * FROM "' + table.replace('"', '""') + '" ORDER BY rowid')] for table in tables}
    before = await store.call(originals)
    await store.close()
    restored = Store(path)
    await restored.initialize()
    await restored.initialize()
    try:
        assert await restored.call(lambda db: originals(db, before)) == before
        assert set(await restored.call(originals)) - set(before) == {'run_frontend_sources'}
        assert not await restored.rows('SELECT * FROM run_frontend_sources')
        assert (await restored.rows('PRAGMA user_version'))[0]['user_version'] == 14
        assert (await restored.rows('PRAGMA integrity_check'))[0]['integrity_check'] == 'ok'
    finally:
        await restored.close()
