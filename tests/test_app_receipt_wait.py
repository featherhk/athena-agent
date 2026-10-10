"""A browser receipt reaches the original tool result without model polling."""
import asyncio
import json

import httpx
import pytest

from athena.api import create_app
from athena.app_controls import (acknowledge_app_action, app_operation, claim_app_action,
                                 initialize_app_controls, wait_app_action_receipt)
from athena.auth import Auth, COOKIE, token_hash
from athena.errors import UserError
from athena.frontend_state import capture_source
from athena.store import Store
from frontend_fixtures import observation, observe_client


@pytest.fixture(autouse=True)
def short_receipt_wait(monkeypatch):
    monkeypatch.setattr('athena.app_controls.RECEIPT_WAIT_SECONDS', .15)
    monkeypatch.setattr('athena.app_controls.RECEIPT_POLL_SECONDS', .005)


@pytest.fixture
async def surface(store):
    await initialize_app_controls(store)
    user, token = await Auth(store).register('receipt-owner', 'test-password')
    session = await store.session(user_id=user['id'])
    await observe_client(store, user['id'], session, token)
    source = await store.call(lambda db: capture_source(db, session, 'browser-one', token_hash(token)))
    run = await store.enqueue(session, 'Open history', frontend_source=source)
    await store.claim(session)
    return user['id'], session, token, run


async def pending_action(store, run):
    async with asyncio.timeout(2):
        while True:
            rows = await store.rows('SELECT id FROM app_actions WHERE run_id=?', (run,))
            if rows:
                return rows[0]['id']
            await asyncio.sleep(.005)


@pytest.mark.parametrize('claimed', [False, True])
async def test_timeout_preserves_pending_or_unknown_dispatch_and_late_receipt(store, surface, claimed):
    owner, session, token, run = surface
    request = (await app_operation(store, session, run, {'action': 'open', 'target': 'history'}))['request']
    if claimed:
        assert (await claim_app_action(store, session, owner, request['id'], 'browser-one', token_hash(token)))['accepted']
    before = await store.rows('SELECT * FROM app_actions')
    began = asyncio.get_running_loop().time()
    result = await wait_app_action_receipt(store, session, owner, run, request)
    assert asyncio.get_running_loop().time() - began < 1
    assert result['wait_timed_out'] and not result['completed']
    assert result['status'] == ('dispatched' if claimed else 'pending')
    assert await store.rows('SELECT * FROM app_actions') == before
    # Timing out never takes ownership or executes again; one late claim/ACK is valid.
    claim = await claim_app_action(store, session, owner, request['id'], 'browser-one', token_hash(token))
    assert claim['accepted'] is not claimed
    await observe_client(store, owner, session, token, revision=2, open_views=['history'])
    await acknowledge_app_action(store, session, owner, request['id'], 'browser-one', 'applied', observed_revision=2)
    read = await app_operation(store, session, run, {'action': 'read', 'id': request['id']})
    assert read['completed'] and read['request']['receipt']['effect'] == 'ui_state_observed'
    assert result['status'] == ('dispatched' if claimed else 'pending'), 'late ACK must not rewrite the returned snapshot'
    assert not (await claim_app_action(store, session, owner, request['id'], 'browser-one'))['accepted']


@pytest.mark.parametrize('status,error_code', [('failed', 'user_gesture_required'), ('dismissed', 'interrupted'), ('applied', None)])
async def test_runtime_returns_receipt_already_committed_before_waiting(engine, store, surface, monkeypatch, status, error_code):
    owner, session, token, run = surface
    original = app_operation
    async def acknowledged(*args, **kwargs):
        result = await original(*args, **kwargs)
        identifier = result['request']['id']
        await claim_app_action(store, session, owner, identifier, 'browser-one')
        if status == 'applied':
            await observe_client(store, owner, session, token, revision=2, open_views=['history'])
        await acknowledge_app_action(store, session, owner, identifier, 'browser-one', status,
                                     error_code, observed_revision=2 if status == 'applied' else None)
        return result
    monkeypatch.setattr('athena.app_controls.app_operation', acknowledged)
    result = await engine.tools.execute('runtime', {'op': 'app', 'args': {'action': 'open', 'target': 'history'}}, session, run)
    assert result['status'] == status and result['completed'] is (status == 'applied')
    assert result['request']['error_code'] == error_code
    assert 'wait_timed_out' not in result
    assert len(await store.rows('SELECT id FROM app_actions')) == 1


async def test_wait_cannot_read_other_owner_session_or_run(store, surface):
    owner, session, _, run = surface
    request = (await app_operation(store, session, run, {'action': 'open', 'target': 'history'}))['request']
    other, _ = await Auth(store).register('foreign-receipt', 'test-password')
    other_session = await store.session(user_id=other['id'])
    for identities in ((other['id'], session, run), (owner, other_session, run), (owner, session, 'other-run')):
        user, sid, rid = identities
        with pytest.raises(UserError) as failure:
            await wait_app_action_receipt(store, sid, user, rid, request)
        assert failure.value.code == 'not_found'
    assert (await store.rows('SELECT status FROM app_actions')) == [{'status': 'pending'}]


@pytest.mark.parametrize('action', ['stop', 'reset', 'list', 'snapshot', 'read'])
async def test_noncreating_app_operations_do_not_enter_receipt_wait(engine, store, surface, monkeypatch, action):
    _, session, _, run = surface
    async def unexpected(*args, **kwargs):
        pytest.fail('This operation must not wait for a browser')
    monkeypatch.setattr('athena.app_controls.wait_app_action_receipt', unexpected)
    args = {'action': action}
    if action == 'snapshot':
        args['client_id'] = 'browser-one'
    if action == 'read':
        args['id'] = (await app_operation(store, session, run, {'action': 'open', 'target': 'history'}))['request']['id']
    result = await engine.tools.execute('runtime', {'op': 'app', 'args': args}, session, run)
    if action in ('stop', 'reset'):
        assert result['control'] == {'stop_run' if action == 'stop' else 'reset_user': True}


async def test_engine_tool_receipts_arrive_over_client_sse_before_tools_return(config, monkeypatch):
    monkeypatch.setattr('athena.app_controls.RECEIPT_WAIT_SECONDS', 3)
    app = create_app(config)
    payloads, received = [], []
    async with app.router.lifespan_context(app):
        store, engine = app.state.store, app.state.engine
        owner, token = await Auth(store).register('live-receipt', 'test-password')
        session = await store.session(user_id=owner['id'])
        target = await store.session(user_id=owner['id'])
        await observe_client(store, owner['id'], session, token, has_unsent_changes=True)
        await observe_client(store, owner['id'], session, token, 'other-window')
        async def provider(client, base, key, payload, *args, **kwargs):
            payloads.append(payload)
            calls = [{'id': 'navigate', 'type': 'function', 'function': {'name': 'runtime', 'arguments': json.dumps({'op': 'app', 'args': {'action': 'navigate', 'session_id': target}})}},
                     {'id': 'open', 'type': 'function', 'function': {'name': 'runtime', 'arguments': json.dumps({'op': 'app', 'args': {'action': 'open', 'target': 'history'}})}}]
            return {'message': {'role': 'assistant', 'content': '' if len(payloads) == 1 else 'Opened.', 'reasoning_content': '',
                                **({'tool_calls': calls} if len(payloads) == 1 else {})}, 'usage': {}}
        monkeypatch.setattr('athena.engine.complete', provider)
        run = await engine.submit(session, 'Navigate then open history', frontend_client_id='browser-one', frontend_auth_hash=token_hash(token))
        incoming = asyncio.Queue()
        await incoming.put({'type': 'http.request', 'body': b'', 'more_body': False})
        transport = httpx.ASGITransport(app)
        async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1:4317') as browser:
            browser.cookies.set(COOKIE, token)
            async def send(message):
                if message['type'] != 'http.response.body' or b'"app_action"' not in message.get('body', b''):
                    return
                event = json.loads(message['body'].decode().split('data: ', 1)[1].split('\n\n', 1)[0])
                action = event['payload']
                assert action['target_client_id'] == 'browser-one' and action['session_id'] == session
                assert not await store.rows('SELECT id FROM messages WHERE run_id=? AND tool_call_id=?', (run, action['action'] if action['action'] == 'navigate' else 'open'))
                assert len(payloads) == 1, 'the first model response must await its own tool receipts'
                if not received:
                    # The accepted input clears while the immediate action is pending.
                    assert (await browser.post('/api/frontend/observation', json=observation(session, revision=2))).status_code == 200
                base = f'/api/sessions/{session}/app-actions/{action["id"]}'
                claim = await browser.post(base + '/claim', json={'client_id': 'browser-one'})
                assert claim.json()['accepted']
                revision = 3 + len(received)
                report = observation(target, revision=revision, open_views=['history'] if received else [])
                assert (await browser.post('/api/frontend/observation', json=report)).status_code == 200
                ack = await browser.post(base + '/ack', json={'client_id': 'browser-one', 'status': 'applied', 'observed_revision': revision})
                assert ack.json()['completed']
                received.append(action['id'])
                if len(received) == 2:
                    await incoming.put({'type': 'http.disconnect'})
            path = '/api/frontend/clients/browser-one/events'
            scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': 'GET',
                     'scheme': 'http', 'path': path, 'raw_path': path.encode(), 'query_string': b'', 'root_path': '',
                     'server': ('127.0.0.1', 4317), 'client': ('127.0.0.1', 1234),
                     'headers': [(b'host', b'127.0.0.1:4317'), (b'cookie', f'{COOKIE}={token}'.encode())]}
            await asyncio.wait_for(app(scope, incoming.get, send), 5)
        await asyncio.wait_for(asyncio.gather(*engine.actors.values()), 3)
        assert len(received) == 2 and len(payloads) == 2
        results = [json.loads(message['content']) for message in payloads[1]['messages'] if message['role'] == 'tool']
        assert len(results) == 2 and all(result['completed'] and result['status'] == 'applied' for result in results)
        assert all(result['request']['receipt']['effect'] == 'ui_state_observed' for result in results)
        assert '_claim_guard' not in json.dumps(payloads) and token_hash(token) not in json.dumps(payloads)
        assert (await store.rows("SELECT session_id,revision FROM frontend_clients WHERE client_id='other-window'")) == [{'session_id': session, 'revision': 1}]
        assert (await store.rows('SELECT status FROM runs WHERE id=?', (run,)))[0]['status'] == 'complete'


@pytest.mark.parametrize('ordering,control', [
    ('cancel_first', 'stop'), ('cancel_first', 'reset'), ('cancel_first', 'shutdown'),
    ('receipt_first', 'stop'), ('receipt_first', 'reset'),
])
@pytest.mark.parametrize('claimed', [False, True])
async def test_stopping_receipt_wait_preserves_pairing_and_never_replays(engine, store, surface, monkeypatch, ordering, control, claimed):
    owner, session, token, initial = surface
    # Use the real engine for a new run, rather than leave fixture work running.
    await store.execute("UPDATE runs SET status='complete' WHERE id=?", (initial,))
    monkeypatch.setattr('athena.app_controls.RECEIPT_WAIT_SECONDS', 30)
    read_reached, dismissal_committed, tool_persisted = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_call, original_message = store.call, store.message

    async def gated_call(operation):
        if operation.__qualname__ == 'wait_app_action_receipt.<locals>.read':
            read_reached.set()
            # Cancel-first never releases this read. Receipt-first lets the real
            # waiter see the durable dismissal before Stop cancels its actor.
            await dismissal_committed.wait()
        result = await original_call(operation)
        if ordering == 'receipt_first' and operation.__qualname__ == 'Engine._stop.<locals>.cancel_queue':
            dismissal_committed.set()
            await tool_persisted.wait()
        return result

    async def gated_message(sid, rid, role, content='', status='complete', **kwargs):
        result = await original_message(sid, rid, role, content, status, **kwargs)
        if ordering == 'receipt_first' and role == 'tool' and kwargs.get('tool_call_id') == 'waiting-ui' and json.loads(content)['status'] == 'dismissed':
            tool_persisted.set()
            # Exercise cancellation after the real result committed but before
            # outstanding.pop: the interrupted fallback must not overwrite it.
            await asyncio.Event().wait()
        return result

    monkeypatch.setattr(store, 'call', gated_call)
    monkeypatch.setattr(store, 'message', gated_message)
    payloads = []
    async def provider(client, base, key, payload, *args, **kwargs):
        payloads.append(payload)
        return {'message': {'role': 'assistant', 'content': '', 'reasoning_content': '', 'tool_calls': [
            {'id': 'waiting-ui', 'type': 'function', 'function': {'name': 'runtime', 'arguments': json.dumps({'op': 'app', 'args': {'action': 'open', 'target': 'history'}})}}]}, 'usage': {}}
    monkeypatch.setattr('athena.engine.complete', provider)
    run = await engine.submit(session, 'Open history', frontend_client_id='browser-one', frontend_auth_hash=token_hash(token))
    identifier = await pending_action(store, run)
    if claimed:
        assert (await claim_app_action(store, session, owner, identifier, 'browser-one', token_hash(token)))['accepted']
    await asyncio.wait_for(read_reached.wait(), 1)
    if control == 'reset':
        await asyncio.wait_for(engine.reset_user(owner), 1)
    elif control == 'shutdown':
        await asyncio.wait_for(engine.close(), 1)
    else:
        await asyncio.wait_for(engine.stop(session), 1)
    assert len(payloads) == 1
    tools = await store.rows('SELECT content FROM messages WHERE run_id=? AND tool_call_id=?', (run, 'waiting-ui'))
    assert len(tools) == 1
    result = json.loads(tools[0]['content'])
    assert result['status'] == ('dismissed' if ordering == 'receipt_first' else 'interrupted')
    row = (await store.rows('SELECT status,error_code,receipt FROM app_actions WHERE id=?', (identifier,)))[0]
    assert row['status'] == ('dispatched' if control == 'shutdown' and claimed else 'dismissed')
    if row['status'] == 'dismissed':
        receipt = json.loads(row['receipt'])
        assert receipt == {'effect': 'unknown' if claimed else 'not_dispatched', 'may_have_applied': claimed}
        assert row['error_code'] == ('reset' if control == 'reset' else 'interrupted')
    if ordering == 'receipt_first':
        assert tool_persisted.is_set() and not result['completed'] and 'wait_timed_out' not in result
        assert result['request']['id'] == identifier and result['request']['run_id'] == run
        assert result['request']['receipt'] == receipt and result['request']['error_code'] == row['error_code']
    assistants = await store.rows('SELECT tool_calls FROM messages WHERE run_id=? AND tool_calls IS NOT NULL', (run,))
    assert len(assistants) == 1 and [call['id'] for call in json.loads(assistants[0]['tool_calls'])] == ['waiting-ui']
    assert (await store.rows('SELECT status,phase FROM runs WHERE id=?', (run,))) == [{'status': 'interrupted', 'phase': 'stopped'}]
    assert not engine.active_runs or all(task.done() for task in engine.active_runs.values())
    reopened = Store(store.path)
    try:
        await reopened.initialize()
        assert (await reopened.rows('SELECT status,error_code,receipt FROM app_actions WHERE id=?', (identifier,)))[0] == row
        assert await reopened.rows('SELECT content FROM messages WHERE run_id=? AND tool_call_id=?', (run, 'waiting-ui')) == tools
        if control == 'reset':
            # Reset revokes the old client identity. Neither it nor a fresh
            # valid browser may reclaim an intent addressed to the old view.
            with pytest.raises(UserError) as revoked:
                await observe_client(reopened, owner, session, token, revision=3)
            assert revoked.value.code == 'frontend_identity_revoked'
            await observe_client(reopened, owner, session, token, 'browser-after-reset')
            for client in ('browser-one', 'browser-after-reset'):
                with pytest.raises(UserError) as unavailable:
                    await claim_app_action(reopened, session, owner, identifier, client, token_hash(token))
                assert unavailable.value.code == 'frontend_unavailable'
        else:
            await observe_client(reopened, owner, session, token, revision=3)
            assert not (await claim_app_action(reopened, session, owner, identifier, 'browser-one', token_hash(token)))['accepted']
        assert (await reopened.rows('SELECT status,error_code,receipt FROM app_actions WHERE id=?', (identifier,)))[0] == row
        assert await reopened.claim(session) is None
    finally:
        await reopened.close()


async def test_receipt_wait_deadline_includes_waiting_for_the_database_worker(store, surface):
    from threading import Event
    owner, session, _, run = surface
    request = (await app_operation(store, session, run, {'action': 'open', 'target': 'history'}))['request']
    entered, release = Event(), Event()
    def busy(db):
        entered.set()
        assert release.wait(2)
    busy_task = asyncio.create_task(store.call(busy))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        result = await asyncio.wait_for(wait_app_action_receipt(store, session, owner, run, request), .5)
        assert result['wait_timed_out'] and result['status'] == 'pending'
        assert not busy_task.done(), 'waiting on the DB worker cannot extend the receipt deadline'
    finally:
        release.set()
        await busy_task
    assert (await store.rows('SELECT status FROM app_actions')) == [{'status': 'pending'}]


async def test_stop_after_applied_receipt_preserves_effect_and_interrupted_tool_pair(engine, store, surface, monkeypatch):
    owner, session, token, initial = surface
    await store.execute("UPDATE runs SET status='complete' WHERE id=?", (initial,))
    reached = asyncio.Event()
    original_wait = wait_app_action_receipt
    async def pause_after_receipt(*args, **kwargs):
        result = await original_wait(*args, **kwargs)
        assert result['completed']
        reached.set()
        await asyncio.Event().wait()
    monkeypatch.setattr('athena.app_controls.wait_app_action_receipt', pause_after_receipt)
    monkeypatch.setattr('athena.app_controls.RECEIPT_WAIT_SECONDS', 2)
    payloads = []
    async def provider(client, base, key, payload, *args, **kwargs):
        payloads.append(payload)
        return {'message': {'role': 'assistant', 'content': '', 'reasoning_content': '', 'tool_calls': [
            {'id': 'applied-before-stop', 'type': 'function', 'function': {'name': 'runtime', 'arguments': json.dumps({'op': 'app', 'args': {'action': 'open', 'target': 'history'}})}}]}, 'usage': {}}
    monkeypatch.setattr('athena.engine.complete', provider)
    run = await engine.submit(session, 'Open history', frontend_client_id='browser-one', frontend_auth_hash=token_hash(token))
    identifier = await pending_action(store, run)
    await claim_app_action(store, session, owner, identifier, 'browser-one')
    await observe_client(store, owner, session, token, revision=2, open_views=['history'])
    await acknowledge_app_action(store, session, owner, identifier, 'browser-one', 'applied', observed_revision=2)
    await asyncio.wait_for(reached.wait(), 1)
    await asyncio.wait_for(engine.stop(session), 1)
    assert len(payloads) == 1
    row = (await store.rows('SELECT status,receipt FROM app_actions WHERE id=?', (identifier,)))[0]
    assert row['status'] == 'applied' and json.loads(row['receipt'])['effect'] == 'ui_state_observed'
    tools = await store.rows('SELECT content FROM messages WHERE run_id=? AND tool_call_id=?', (run, 'applied-before-stop'))
    assert len(tools) == 1 and json.loads(tools[0]['content'])['status'] == 'interrupted'
