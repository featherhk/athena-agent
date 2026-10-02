import asyncio
import base64
import json

import httpx
import pytest
from fastapi import HTTPException

from athena.accounts import account_operation, initialize_accounts, register_account_routes
from athena.api import create_app
from athena.auth import COOKIE, token_hash, verify
from athena.errors import UserError
from athena.store import now

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/v04AAAAASUVORK5CYII=')
PASSWORD = 'local-only-old-password'
NEW_PASSWORD = 'local-only-new-password'


@pytest.fixture
async def accounts(config):
    app = create_app(config)
    if not any(getattr(route, 'path', '') == '/api/account/profile' for route in app.routes):
        register_account_routes(app)
    async with app.router.lifespan_context(app):
        await initialize_accounts(app.state.store)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317') as client:
            user = (await client.post('/api/auth/register', json={'username': 'profile-owner', 'password': PASSWORD})).json()['user']
            session = (await client.post('/api/sessions', json={})).json()['id']
            yield client, app, user, session


async def test_profile_public_fields_and_durable_owned_events(accounts):
    client, app, user, session = accounts
    other = await app.state.auth.register('other-account', PASSWORD)
    other_session = await app.state.store.session(user_id=other[0]['id'])
    sibling = await app.state.store.session(user_id=user['id'])
    response = await client.post('/api/account/profile', json={'display_name': 'Ada 阿达'})
    assert response.status_code == 200
    profile = response.json()['user']
    assert profile['display_name'] == 'Ada 阿达'
    assert profile['profile_version'] == 1
    assert 'password_hash' not in profile and 'avatar_path' not in profile
    assert (await client.get('/api/auth/me')).json()['user'] == profile
    events = await app.state.store.rows("SELECT * FROM events WHERE type='account_changed'")
    assert {row['session_id'] for row in events} == {session, sibling}
    assert other_session not in {row['session_id'] for row in events}
    assert all(json.loads(row['payload'])['user'] == profile for row in events)
    again = await client.post('/api/account/profile', json={'display_name': 'Ada 阿达'})
    assert again.json()['user']['profile_version'] == 1
    assert (await client.post('/api/account/profile', json={'display_name': 'x' * 61})).status_code == 422
    assert (await client.post('/api/account/profile', json={'display_name': 'bad\x00name'})).status_code == 400
    assert (await client.post('/api/account/profile', json={'display_name': 'okay', 'role': 'owner'})).status_code == 422
    await client.post('/api/auth/logout')
    assert (await client.get('/api/account/profile')).status_code == 401
    assert (await client.post('/api/account/profile', json={'display_name': 'guest'})).status_code == 401


async def test_password_verifies_old_revokes_other_sessions_and_keeps_current(accounts):
    client, app, user, _ = accounts
    other_token = await app.state.auth.issue(user['id'])
    untouched_user, untouched_token = await app.state.auth.register('unrelated-person', PASSWORD)
    wrong = await client.post('/api/account/password', json={'current_password': 'wrong-old-password', 'new_password': NEW_PASSWORD})
    assert wrong.status_code == 400 and wrong.json()['code'] == 'invalid_password'
    assert await app.state.auth.current(other_token)
    changed = await client.post('/api/account/password', json={'current_password': PASSWORD, 'new_password': NEW_PASSWORD})
    assert changed.status_code == 200
    assert (await client.get('/api/auth/me')).json()['user']['id'] == user['id']
    assert await app.state.auth.current(other_token) is None
    assert (await app.state.auth.current(untouched_token))['id'] == untouched_user['id']
    encoded = (await app.state.store.rows('SELECT password_hash FROM users WHERE id=?', (user['id'],)))[0]['password_hash']
    assert verify(NEW_PASSWORD, encoded) and not verify(PASSWORD, encoded)
    for table in ('events', 'messages', 'requests'):
        serialized = json.dumps(await app.state.store.rows(f'SELECT * FROM {table}'))
        assert PASSWORD not in serialized and NEW_PASSWORD not in serialized
    assert (await client.post('/api/auth/login', json={'username': user['username'], 'password': PASSWORD})).status_code == 400
    assert (await client.post('/api/auth/login', json={'username': user['username'], 'password': NEW_PASSWORD})).status_code == 200


async def test_concurrent_password_changes_have_one_winner(accounts):
    client, app, user, _ = accounts
    responses = await asyncio.gather(*(client.post('/api/account/password', json={'current_password': PASSWORD, 'new_password': f'new-concurrent-password-{index}'}) for index in range(2)))
    assert sorted(response.status_code for response in responses) == [200, 400]
    tokens = await app.state.store.rows('SELECT * FROM auth_sessions WHERE user_id=?', (user['id'],))
    assert len(tokens) == 1 and tokens[0]['token_hash'] == token_hash(client.cookies[COOKIE])


async def test_password_change_rate_limit_does_not_echo_secrets(accounts):
    client, app, user, _ = accounts
    await app.state.store.execute('INSERT INTO auth_attempts VALUES (?,?,?)', ('password-change:' + user['id'], 10, now()))
    response = await client.post('/api/account/password', json={'current_password': PASSWORD, 'new_password': NEW_PASSWORD})
    assert response.status_code == 400 and response.json()['code'] == 'login_rate_limited'
    assert PASSWORD not in response.text and NEW_PASSWORD not in response.text
    invalid = await client.post('/api/account/password', json={'current_password': PASSWORD, 'new_password': 'tiny'})
    assert invalid.status_code == 422 and PASSWORD not in invalid.text


async def test_avatar_magic_ownership_and_safe_storage(accounts, config, tmp_path):
    client, app, user, _ = accounts
    response = await client.post('/api/account/avatar', content=PNG, headers={'Content-Type': 'text/html'})
    assert response.status_code == 200
    profile = response.json()['user']
    assert profile['avatar_url'].startswith('/api/account/avatar?owner=' + user['id'])
    image = await client.get(profile['avatar_url'])
    assert image.content == PNG and image.headers['content-type'] == 'image/png'
    assert image.headers['x-content-type-options'] == 'nosniff'
    assert (await client.post('/api/account/avatar', content=b'<svg onload="alert(1)"></svg>')).status_code == 400
    assert (await client.post('/api/account/avatar', content=b'x' * (2 * 1024 * 1024 + 1))).status_code == 400
    await client.post('/api/auth/logout')
    assert (await client.get(profile['avatar_url'])).status_code == 401
    await client.post('/api/auth/register', json={'username': 'other-avatar-owner', 'password': PASSWORD})
    assert (await client.post('/api/account/avatar', content=PNG)).status_code == 200
    assert (await client.get(profile['avatar_url'])).status_code == 404
    stored = (await app.state.store.rows('SELECT avatar_path FROM users WHERE id=?', (user['id'],)))[0]['avatar_path']
    path = config.data_dir / 'account-avatars' / user['id'] / stored
    path.unlink()
    outside = tmp_path / 'private-avatar.png'
    outside.write_bytes(PNG)
    path.symlink_to(outside)
    await client.post('/api/auth/login', json={'username': user['username'], 'password': PASSWORD})
    assert (await client.get(profile['avatar_url'])).status_code == 404


async def test_agent_profile_avatar_and_password_form_require_owned_context(accounts, config):
    client, app, user, session = accounts
    upload = (await client.post(f'/api/sessions/{session}/files', params={'name': 'portrait.png'}, content=PNG)).json()
    run = await app.state.store.enqueue(session, 'Change my profile')
    result = await account_operation(app.state.store, config, session, run, {'op': 'update_profile', 'display_name': 'New name', 'avatar_file_id': upload['id']})
    assert result['user']['display_name'] == 'New name'
    assert (await client.get(result['user']['avatar_url'])).content == PNG
    secret_before = (await app.state.store.rows('SELECT password_hash FROM users WHERE id=?', (user['id'],)))[0]
    action = await account_operation(app.state.store, config, session, run, {'op': 'change_password'})
    assert action['status'] == 'form_requested'
    assert (await app.state.store.rows('SELECT password_hash FROM users WHERE id=?', (user['id'],)))[0] == secret_before
    event = (await app.state.store.rows("SELECT * FROM events WHERE type='account_action'"))[0]
    assert json.loads(event['payload']) == {'action': 'change_password', 'source_run_id': run}
    with pytest.raises(UserError):
        await account_operation(app.state.store, config, session, run, {'op': 'change_password', 'new_password': NEW_PASSWORD})
    with pytest.raises(UserError):
        await account_operation(app.state.store, config, session, 'unknown-run', {'op': 'read'})
    second, _ = await app.state.auth.register('other-file-owner', PASSWORD)
    second_session = await app.state.store.session(user_id=second['id'])
    with pytest.raises(HTTPException):
        await account_operation(app.state.store, config, second_session, None, {'op': 'update_profile', 'avatar_file_id': upload['id']})
    with pytest.raises(UserError):
        await account_operation(app.state.store, config, session, run, {'op': 'update_profile', 'avatar_url': 'https://example.com/picture.png'})
