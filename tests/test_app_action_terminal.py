"""A failed task cannot perform a delayed UI effect after connectivity returns."""
import asyncio
from dataclasses import replace
import json

import pytest

from athena.app_controls import (acknowledge_app_action, app_operation, cancel_app_actions,
                                 claim_app_action)
from athena.auth import Auth, token_hash
from athena.errors import UserError
from frontend_fixtures import observe_client


async def account(store):
    user, token = await Auth(store).register('terminal-owner', 'test-password')
    session = await store.session(user_id=user['id'])
    await observe_client(store, user['id'], session, token)
    return user['id'], session, token


async def intent(store, session, run, target='history'):
    return (await app_operation(store, session, run, {'action': 'open', 'target': target}))['request']


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'interrupted', 'stopped'])
async def test_terminal_run_rejects_legacy_pending_claim_and_preserves_other_run(store, status):
    user, session, token = await account(store)
    run = await store.enqueue(session, 'First task')
    assert await store.claim(session) == run
    first, second = await intent(store, session, run), await intent(store, session, run, 'files')
    await store.execute('UPDATE runs SET status=? WHERE id=?', (status, run))
    other_run = await store.enqueue(session, 'Other task')
    assert await store.claim(session) == other_run
    other = await intent(store, session, other_run)

    result = await claim_app_action(store, session, user, first['id'], 'browser-one', token_hash(token))
    assert not result['accepted']
    assert result['request']['status'] == 'dismissed'
    assert result['request']['error_code'] == 'interrupted'
    assert result['request']['receipt'] == {'effect': 'not_dispatched', 'may_have_applied': False}
    assert result['request']['dispatched_at'] is None
    rows = {row['id']: row for row in await store.rows('SELECT id,status,error_code,receipt FROM app_actions')}
    assert rows[second['id']]['status'] == 'dismissed'
    assert rows[other['id']]['status'] == 'pending'
    events = await store.rows("SELECT payload FROM events WHERE type='app_action'")
    assert any(json.loads(row['payload'])['receipt'] == result['request']['receipt'] for row in events)
    assert not (await claim_app_action(store, session, user, first['id'], 'browser-one', token_hash(token)))['accepted']


@pytest.mark.parametrize('status', ['running', 'complete', 'waiting_user', 'waiting_agents'])
async def test_live_or_completed_run_keeps_delayed_ui_intent_claimable(store, status):
    user, session, token = await account(store)
    run = await store.enqueue(session, 'Show my files')
    assert await store.claim(session) == run
    request = await intent(store, session, run)
    await store.execute('UPDATE runs SET status=? WHERE id=?', (status, run))
    assert (await claim_app_action(store, session, user, request['id'], 'browser-one', token_hash(token)))['accepted']


def tool(identifier, target):
    return {'id': identifier, 'type': 'function', 'function': {'name': 'runtime',
            'arguments': json.dumps({'op': 'app', 'args': {'action': 'open', 'target': target}})}}


async def test_step_limit_durably_dismisses_only_its_unclaimed_ui_actions(engine, store, monkeypatch):
    _, session, _ = await account(store)
    other_run = await store.enqueue(session, 'Previously completed task')
    assert await store.claim(session) == other_run
    other = await intent(store, session, other_run)
    await store.execute("UPDATE runs SET status='complete' WHERE id=?", (other_run,))
    engine.config = replace(engine.config, max_steps=1)

    async def provider(*args, **kwargs):
        return {'message': {'role': 'assistant', 'content': '', 'reasoning_content': '',
                            'tool_calls': [tool('show-files', 'files')]}, 'usage': {}}
    monkeypatch.setattr('athena.engine.complete', provider)
    run = await engine.submit(session, 'Show my files')
    await asyncio.wait_for(asyncio.gather(*engine.actors.values()), 5)
    assert (await store.rows('SELECT status,error_code FROM runs WHERE id=?', (run,)))[0] == {'status': 'failed', 'error_code': 'step_limit'}
    action = (await store.rows('SELECT * FROM app_actions WHERE run_id=?', (run,)))[0]
    assert action['status'] == 'dismissed' and action['error_code'] == 'interrupted'
    assert action['resolved_at'] and action['dispatched_at'] is None
    assert json.loads(action['receipt']) == {'effect': 'not_dispatched', 'may_have_applied': False}
    assert (await store.rows('SELECT status FROM app_actions WHERE id=?', (other['id'],)))[0]['status'] == 'pending'


async def test_failed_run_retains_unknown_dispatched_effect_until_real_ack(engine, store, monkeypatch):
    user, session, token = await account(store)
    calls, sent = 0, None

    async def provider(*args, **kwargs):
        nonlocal calls, sent
        calls += 1
        if calls == 1:
            return {'message': {'role': 'assistant', 'content': '', 'reasoning_content': '',
                                'tool_calls': [tool('sent', 'history'), tool('not-sent', 'files')]}, 'usage': {}}
        sent = (await store.rows("SELECT id FROM app_actions WHERE action='open' ORDER BY rowid"))[0]['id']
        assert (await claim_app_action(store, session, user, sent, 'browser-one', token_hash(token)))['accepted']
        raise UserError('provider_unavailable', 'Synthetic provider failure')
    monkeypatch.setattr('athena.engine.complete', provider)
    run = await engine.submit(session, 'Open history and files')
    await asyncio.wait_for(asyncio.gather(*engine.actors.values()), 5)
    assert (await store.rows('SELECT status FROM runs WHERE id=?', (run,)))[0]['status'] == 'failed'
    rows = await store.rows('SELECT id,status,receipt FROM app_actions WHERE run_id=? ORDER BY rowid', (run,))
    assert rows[0] == {'id': sent, 'status': 'dispatched', 'receipt': None}
    assert rows[1]['status'] == 'dismissed'
    assert not (await claim_app_action(store, session, user, sent, 'browser-one', token_hash(token)))['accepted']
    await observe_client(store, user, session, token, revision=2, open_views=['history'])
    result = await acknowledge_app_action(store, session, user, sent, 'browser-one', 'applied',
                                         observed_revision=2, auth_hash=token_hash(token))
    assert result['completed'] and result['request']['receipt']['effect'] == 'ui_state_observed'


async def test_explicit_stop_does_not_claim_already_dispatched_effect_was_undone(store):
    user, session, token = await account(store)
    run = await store.enqueue(session, 'Open history')
    assert await store.claim(session) == run
    request = await intent(store, session, run)
    await claim_app_action(store, session, user, request['id'], 'browser-one', token_hash(token))
    await store.call(lambda db: cancel_app_actions(db, user, session_id=session, reason='interrupted'))
    result = (await app_operation(store, session, run, {'action': 'read', 'id': request['id']}))['request']
    assert result['status'] == 'dismissed' and result['error_code'] == 'interrupted'
    assert result['receipt'] == {'effect': 'unknown', 'may_have_applied': True}
    assert not (await claim_app_action(store, session, user, request['id'], 'browser-one', token_hash(token)))['accepted']
    await observe_client(store, user, session, token, revision=2, open_views=['history'])
    with pytest.raises(UserError):
        await acknowledge_app_action(store, session, user, request['id'], 'browser-one', 'applied',
                                     observed_revision=2, auth_hash=token_hash(token))
    latest = (await app_operation(store, session, run, {'action': 'read', 'id': request['id']}))['request']
    assert latest['receipt'] == result['receipt']
