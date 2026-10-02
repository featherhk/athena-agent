"""Cloud credential recovery never borrows another account's authorization."""
import json

import httpx
import pytest

from athena.auth import token_hash
from athena.cloud_client import CloudAccounts
from athena.errors import ServiceError
from athena.store import now


@pytest.fixture
async def cloud(store):
    accounts = CloudAccounts(store, 'https://accounts.example/v1')
    await accounts.initialize()
    await accounts.http.aclose()
    yield accounts
    await accounts.http.aclose()


async def seed_user(cloud, user_id, username=None):
    await cloud.store.execute(
        "INSERT INTO users(id,username,password_hash,role,created_at) VALUES (?,?,'cloud-managed','user',?)",
        (user_id, username or user_id, now()),
    )


async def seed_login(cloud, user_id, remote_token, date):
    cookie = await cloud.local.issue(user_id)
    await cloud.store.execute('INSERT INTO cloud_logins VALUES (?,?,?,?)',
                              (token_hash(cookie), user_id, remote_token, date))
    return cookie


def identity(user_id):
    return {'user': {'id': user_id}, 'credit': {'enabled': True, 'available_micros': 50}}


async def test_latest_revoked_token_falls_back_to_older_valid_login(cloud):
    await seed_user(cloud, 'alice')
    older = await seed_login(cloud, 'alice', 'alice-valid', '2026-01-01')
    revoked = await seed_login(cloud, 'alice', 'alice-revoked', '2026-01-02')
    seen = []

    def provider(request):
        token = request.headers['authorization'].removeprefix('Bearer ')
        seen.append(token)
        return httpx.Response(401, json={'code': 'login_required'}) if token == 'alice-revoked' else httpx.Response(200, json=identity('alice'))

    cloud.http = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    assert await cloud.token('alice') == 'alice-valid'
    assert seen == ['alice-revoked', 'alice-valid']
    remaining = await cloud.store.rows('SELECT local_hash FROM cloud_logins')
    assert remaining == [{'local_hash': token_hash(older)}]
    assert await cloud.current(revoked) is None
    assert (await cloud.current(older))['id'] == 'alice'
    assert (await cloud.require_credit('alice'))['available_micros'] == 50


async def test_current_revoked_cookie_is_cleared_without_borrowing_another_login(cloud):
    await seed_user(cloud, 'alice')
    await seed_user(cloud, 'bob')
    revoked = await seed_login(cloud, 'alice', 'alice-revoked', '2026-01-01')
    valid = await seed_login(cloud, 'alice', 'alice-valid', '2026-01-02')
    other = await seed_login(cloud, 'bob', 'bob-valid', '2026-01-03')
    seen = []

    def provider(request):
        token = request.headers['authorization'].removeprefix('Bearer ')
        seen.append(token)
        return httpx.Response(401, json={'code': 'login_required'}) if token == 'alice-revoked' else httpx.Response(200, json=identity('bob' if token == 'bob-valid' else 'alice'))

    cloud.http = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    assert await cloud.current(revoked) is None
    assert seen == ['alice-revoked']
    assert not await cloud.store.rows('SELECT 1 FROM cloud_logins WHERE local_hash=?', (token_hash(revoked),))
    assert not await cloud.store.rows('SELECT 1 FROM auth_sessions WHERE token_hash=?', (token_hash(revoked),))
    assert (await cloud.current(valid))['id'] == 'alice'
    assert (await cloud.current(other))['id'] == 'bob'


async def test_token_lookup_does_not_probe_other_accounts_or_expired_local_sessions(cloud):
    await seed_user(cloud, 'alice')
    await seed_user(cloud, 'bob')
    await seed_login(cloud, 'alice', 'alice-valid', '2026-01-01')
    expired = await seed_login(cloud, 'alice', 'alice-expired', '2026-01-02')
    await seed_login(cloud, 'bob', 'bob-valid', '2026-01-03')
    await cloud.store.execute("UPDATE auth_sessions SET expires_at='2000-01-01' WHERE token_hash=?", (token_hash(expired),))
    seen = []

    def provider(request):
        seen.append(request.headers['authorization'])
        return httpx.Response(200, json=identity('alice'))

    cloud.http = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    assert await cloud.token('alice') == 'alice-valid'
    assert seen == ['Bearer alice-valid']
    assert await cloud.current(expired) is None
    with pytest.raises(ServiceError) as error:
        await cloud.token('nobody')
    assert error.value.http_status == 401
    assert seen == ['Bearer alice-valid']


async def test_remote_identity_mismatch_fails_closed(cloud):
    await seed_user(cloud, 'alice')
    cookie = await seed_login(cloud, 'alice', 'wrong-subject-token', '2026-01-01')
    cloud.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=identity('bob'))))
    assert await cloud.current(cookie) is None
    with pytest.raises(ServiceError) as error:
        await cloud.token('alice')
    assert error.value.http_status == 401
    assert 'wrong-subject-token' not in str(error.value)


async def test_transient_cloud_failure_does_not_delete_credentials_or_try_other_tokens(cloud):
    await seed_user(cloud, 'alice')
    await seed_login(cloud, 'alice', 'older-token', '2026-01-01')
    cookie = await seed_login(cloud, 'alice', 'newer-token', '2026-01-02')
    seen = []

    def provider(request):
        seen.append(request.headers['authorization'])
        return httpx.Response(503, json={'code': 'cloud_unavailable', 'debug': 'never surface upstream details'})

    cloud.http = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    with pytest.raises(ServiceError) as error:
        await cloud.token('alice')
    assert error.value.http_status == 503
    assert seen == ['Bearer newer-token']
    assert 'upstream details' not in str(error.value)
    assert len(await cloud.store.rows('SELECT local_hash FROM cloud_logins')) == 2
    with pytest.raises(ServiceError):
        await cloud.current(cookie)
    assert len(await cloud.store.rows('SELECT local_hash FROM cloud_logins')) == 2


async def test_same_username_cannot_merge_different_cloud_identity(cloud):
    await seed_user(cloud, 'local-existing', 'shared-name')
    seen = []

    def provider(request):
        seen.append((request.url.path, request.headers.get('authorization')))
        if request.url.path.endswith('/auth/login'):
            assert json.loads(request.content)['username'] == 'shared-name'
            return httpx.Response(200, json={'user': {'id': 'remote-other', 'username': 'shared-name'}, 'token': 'new-remote-token'})
        return httpx.Response(200, json={'ok': True})

    cloud.http = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    with pytest.raises(ServiceError) as error:
        await cloud.authenticate('login', 'shared-name', 'test-password')
    assert error.value.code == 'cloud_account_conflict'
    assert seen == [('/v1/auth/login', None), ('/v1/auth/logout', 'Bearer new-remote-token')]
    assert await cloud.store.rows('SELECT id FROM users') == [{'id': 'local-existing'}]
    assert not await cloud.store.rows('SELECT 1 FROM cloud_logins')
    assert not await cloud.store.rows('SELECT 1 FROM auth_sessions')
