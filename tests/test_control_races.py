"""Deterministic coverage for controls that must survive async boundaries."""
import asyncio

import httpx
import pytest

from athena.accounts import account_operation, pending_account_action
from athena.api import create_app
from athena.auth import Auth
from athena.errors import UserError
from athena.registry import initialize_registry


async def test_submit_started_before_completed_reset_cannot_enqueue_after_key_gate(engine, store, monkeypatch):
    await initialize_registry(store)
    user, _ = await Auth(store).register('reset-race-user', 'control-test-password')
    session = await store.session(user_id=user['id'])
    entered, release = asyncio.Event(), asyncio.Event()
    kicked = []

    async def require_key(profile):
        entered.set()
        await release.wait()
        return 'fake-test-provider-key'

    monkeypatch.setattr(engine.models, 'require_key', require_key)
    monkeypatch.setattr(engine, 'kick', kicked.append)
    submission = asyncio.create_task(engine.submit(session, 'This input predates reset.'))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await asyncio.wait_for(engine.reset_user(user['id']), 3)
        assert user['id'] not in engine.resetting_users
        assert not submission.done()
        release.set()
        with pytest.raises(UserError) as rejected:
            await asyncio.wait_for(submission, 3)
        assert rejected.value.code == 'resetting'
        assert not await store.rows('SELECT * FROM runs WHERE session_id=?', (session,))
        assert not await store.rows('SELECT * FROM messages WHERE session_id=?', (session,))
        assert not await store.rows('SELECT * FROM requests')
        assert kicked == [] and not engine.active_runs
    finally:
        release.set()
        if not submission.done():
            submission.cancel()
        await asyncio.gather(submission, return_exceptions=True)


async def test_password_form_action_survives_restart_then_owned_dismissal_prevents_replay(config):
    cookies = None
    session = user_id = sequence = password_hash = None
    for iteration in range(2):
        app = create_app(config)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317', cookies=cookies) as client:
                if iteration == 0:
                    user = (await client.post('/api/auth/register', json={'username': 'pending-action-owner', 'password': 'control-test-password'})).json()['user']
                    user_id = user['id']
                    session = (await client.post('/api/sessions', json={})).json()['id']
                    password_hash = (await app.state.store.rows('SELECT password_hash FROM users WHERE id=?', (user_id,)))[0]['password_hash']
                    assert await pending_account_action(app.state.store, user_id) is None
                    await account_operation(app.state.store, config, session, None, {'op': 'change_password'})
                    pending = await pending_account_action(app.state.store, user_id)
                    sequence = pending['seq']
                    assert pending['action'] == 'change_password'
                    assert (await client.get(f'/api/sessions/{session}')).json()['account_action'] == pending
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317') as other:
                        other_user = (await other.post('/api/auth/register', json={'username': 'unrelated-action-user', 'password': 'other-control-password'})).json()['user']
                        assert await pending_account_action(app.state.store, other_user['id']) is None
                        assert (await other.post(f'/api/account/actions/{sequence}/dismiss', json={})).status_code == 404
                    assert (await pending_account_action(app.state.store, user_id))['seq'] == sequence
                    cookies = client.cookies
                else:
                    # A new Store/Engine with no listener state recovers the intent from SQLite.
                    restored = await pending_account_action(app.state.store, user_id)
                    assert restored['seq'] == sequence and restored['action'] == 'change_password'
                    assert (await client.get(f'/api/sessions/{session}')).json()['account_action'] == restored
                    dismissed = await client.post(f'/api/account/actions/{sequence}/dismiss', json={})
                    assert dismissed.status_code == 200
                    assert await pending_account_action(app.state.store, user_id) is None
                    assert (await client.get(f'/api/sessions/{session}')).json()['account_action'] is None
                    assert (await client.post(f'/api/account/actions/{sequence}/dismiss', json={})).status_code == 200
                    assert await pending_account_action(app.state.store, user_id) is None
                    assert (await app.state.store.rows('SELECT password_hash FROM users WHERE id=?', (user_id,)))[0]['password_hash'] == password_hash
                    # Dismissing the prior form does not suppress a later explicit request.
                    await account_operation(app.state.store, config, session, None, {'op': 'change_password'})
                    assert (await pending_account_action(app.state.store, user_id))['seq'] > sequence
