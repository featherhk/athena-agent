"""Deliberate sends survive lost HTTP responses without repeating admission."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from athena.api import create_app
from athena.auth import Auth, token_hash
from athena.engine import Engine
from athena.errors import ServiceError
from athena.store import SCHEMA_VERSION, Store, now
from athena.submissions import StopIntent, SubmissionIntent, lookup_stop, revision
from frontend_fixtures import observe_client


def payload(identifier='deliberate-send', *, text='Please do this once', mode='queue', files=(), version=0):
    return {'text': text, 'mode': mode, 'file_ids': list(files), 'client_submission_id': identifier,
            'expected_submission_revision': version}


def intent(owner, body):
    return SubmissionIntent(owner, body['client_submission_id'], body['text'], body['mode'],
                            tuple(body['file_ids']), body['expected_submission_revision'])


async def setup(store):
    user, token = await Auth(store).register('submission-owner', 'synthetic-test-password')
    return user['id'], await store.session(user_id=user['id']), token


async def send(engine, owner, session, body, **kwargs):
    return await engine.submit(session, body['text'].strip(), body['mode'], submission=intent(owner, body), **kwargs)


@pytest.fixture
async def api(config, monkeypatch):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        kicks = []
        monkeypatch.setattr(app.state.engine, 'kick', kicks.append)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317') as client:
            result = await client.post('/api/auth/register', json={'username': 'submission-http', 'password': 'synthetic-test-password'})
            assert result.status_code == 200
            session = (await client.post('/api/sessions', json={})).json()['id']
            yield client, app, session, result.json()['user']['id'], kicks


async def test_http_twenty_retries_of_lost_response_create_one_input_and_receipt(api):
    client, app, session, owner, kicks = api
    body = payload()
    initial = await client.post(f'/api/sessions/{session}/messages', json=body)
    assert initial.status_code == 200 and not initial.json()['replayed']
    # Drop the original result at the client boundary and explicitly retry its
    # frozen identity twenty times. No actor or provider can run twice.
    results = await asyncio.gather(*(client.post(f'/api/sessions/{session}/messages', json=body) for _ in range(20)))
    assert all(result.status_code == 200 and result.json()['replayed'] for result in results)
    assert {result.json()['run_id'] for result in results} == {initial.json()['run_id']}
    for table in ('submission_receipts', 'runs', 'messages', 'events'):
        assert len(await app.state.store.rows(f'SELECT * FROM {table}')) == 1
    assert kicks == [session]
    assert not await app.state.store.rows('SELECT * FROM requests')
    read = (await client.get(f"/api/sessions/{session}/submissions/{body['client_submission_id']}")).json()
    assert read == {'found': True, **results[0].json()}
    assert (await client.get(f'/api/sessions/{session}')).json()['submission_revision'] == 0


async def test_twenty_first_attempts_are_serialized_before_interrupt(engine, store, monkeypatch):
    owner, session, _ = await setup(store)
    old = await store.enqueue(session, 'Old queued work')
    kicks = []
    monkeypatch.setattr(engine, 'kick', kicks.append)
    results = await asyncio.gather(*(send(engine, owner, session, payload(mode='interrupt')) for _ in range(20)))
    assert sum(not result['replayed'] for result in results) == 1
    assert len({result['run_id'] for result in results}) == 1
    assert all(result['submission_revision'] == 1 for result in results)
    assert (await store.rows('SELECT status FROM runs WHERE id=?', (old,)))[0]['status'] == 'cancelled'
    assert len(await store.rows('SELECT * FROM runs')) == 2
    assert len(await store.rows('SELECT * FROM submission_receipts')) == 1
    assert kicks == [session]


@pytest.mark.parametrize('stage', ['emit', 'kick'])
async def test_post_commit_notification_failure_is_never_a_confirmed_http_rejection(api, monkeypatch, stage):
    client, app, session, _, kicks = api
    async def fail_emit(*args, **kwargs):
        raise ValueError('injected post-commit notification failure')
    def fail_kick(*args, **kwargs):
        raise ValueError('injected post-commit actor wake failure')
    monkeypatch.setattr(app.state.engine, stage, fail_emit if stage == 'emit' else fail_kick)
    body = payload()
    response = await client.post(f'/api/sessions/{session}/messages', json=body)
    assert response.status_code == 503 and response.json()['code'] == 'submission_acknowledgement_unavailable'
    lookup_result = (await client.get(f'/api/sessions/{session}/submissions/deliberate-send')).json()
    assert lookup_result['found'] and lookup_result['accepted'] and lookup_result['status'] == 'queued'
    retry = await client.post(f'/api/sessions/{session}/messages', json=body)
    assert retry.status_code == 200 and retry.json()['run_id'] == lookup_result['run_id']
    assert len(await app.state.store.rows('SELECT * FROM submission_receipts')) == 1
    assert len(await app.state.store.rows('SELECT * FROM runs')) == 1
    assert len(await app.state.store.rows('SELECT * FROM messages')) == 1
    assert not kicks


@pytest.mark.parametrize('change', [{'text': 'Other text'}, {'text': ' Please do this once'},
                                  {'mode': 'interrupt'}, {'file_ids': ['missing']},
                                  {'expected_submission_revision': 1}, {'text': ''}])
async def test_http_conflict_precedes_all_effects_and_preflight(api, monkeypatch, change):
    client, app, session, _, kicks = api
    body = payload()
    accepted = (await client.post(f'/api/sessions/{session}/messages', json=body)).json()
    async def forbidden(*args, **kwargs):
        raise AssertionError('Conflict must not enter model or attachment admission')
    monkeypatch.setattr(app.state.engine.models, 'selected', forbidden)
    monkeypatch.setattr('athena.api.attachment_info', forbidden)
    response = await client.post(f'/api/sessions/{session}/messages', json={**body, **change})
    assert response.status_code == 409 and response.json()['code'] == 'submission_conflict'
    assert len(await app.state.store.rows('SELECT * FROM runs')) == 1
    assert (await app.state.store.rows('SELECT status FROM runs WHERE id=?', (accepted['run_id'],)))[0]['status'] == 'queued'
    assert await app.state.store.call(lambda db: revision(db, session)) == 0
    assert kicks == [session]


async def test_payload_hash_freezes_attachment_order_and_excludes_window_identity():
    one = intent('owner', payload(files=('first', 'second')))
    two = intent('owner', payload(files=('second', 'first')))
    assert one.payload_hash.startswith('v1:') and one.payload_hash != two.payload_hash
    assert one.payload_hash == intent('owner', {**payload(files=('first', 'second')), 'frontend_client_id': 'new-window'}).payload_hash


async def test_receipt_retry_after_stop_bypasses_missing_file_credit_and_stale_source(api, monkeypatch):
    client, app, session, _, kicks = api
    uploaded = await client.post(f'/api/sessions/{session}/files', params={'name': 'notes.txt'}, content=b'original')
    assert uploaded.status_code == 201
    body = payload(files=(uploaded.json()['id'],))
    first = (await client.post(f'/api/sessions/{session}/messages', json=body)).json()
    stopped = await client.post(f'/api/sessions/{session}/stop', json={})
    assert stopped.json() == {'ok': True, 'submission_revision': 1}
    before = await app.state.store.rows('SELECT * FROM events')
    async def forbidden(*args, **kwargs):
        raise AssertionError('A matching receipt must bypass preflight')
    monkeypatch.setattr('athena.api.attachment_info', forbidden)
    monkeypatch.setattr(app.state.engine.models, 'selected', forbidden)
    monkeypatch.setattr(app.state.engine, 'cloud', SimpleNamespace(require_credit=forbidden))
    result = await client.post(f'/api/sessions/{session}/messages', json={**body, 'frontend_client_id': 'expired-browser'})
    assert result.status_code == 200
    assert result.json() == {**first, 'replayed': True, 'status': 'cancelled', 'submission_revision': 1}
    assert await app.state.store.rows('SELECT * FROM events') == before
    assert kicks == [session]


async def test_duplicate_interrupt_does_not_cancel_newer_running_task(engine, store, monkeypatch):
    owner, session, _ = await setup(store)
    monkeypatch.setattr(engine, 'kick', lambda _: None)
    body = payload(mode='interrupt')
    first = await send(engine, owner, session, body)
    await engine.stop(session)
    new_run = await store.enqueue(session, 'New work after Stop')
    assert await store.claim(session) == new_run
    active = asyncio.create_task(asyncio.Event().wait())
    engine.active_runs[session] = active
    before = await store.rows('SELECT * FROM events')
    try:
        retry = await send(engine, owner, session, body)
        assert retry['run_id'] == first['run_id'] and retry['status'] == 'cancelled'
        assert retry['submission_revision'] == 2 and not active.done()
        assert (await store.rows('SELECT status FROM runs WHERE id=?', (new_run,)))[0]['status'] == 'running'
        assert await store.rows('SELECT * FROM events') == before
    finally:
        active.cancel()
        await asyncio.gather(active, return_exceptions=True)
        engine.active_runs.pop(session, None)


@pytest.mark.parametrize('stage', ['attachments', 'key', 'credit'])
async def test_stop_revision_rejects_inflight_preflight(api, monkeypatch, stage):
    client, app, session, _, kicks = api
    entered, release = asyncio.Event(), asyncio.Event()
    async def blocked(*args, **kwargs):
        entered.set()
        await release.wait()
        return []
    if stage == 'attachments':
        monkeypatch.setattr('athena.api.attachment_info', blocked)
    elif stage == 'key':
        monkeypatch.setattr(app.state.engine.models, 'require_key', blocked)
    else:
        monkeypatch.setattr(app.state.engine, 'cloud', SimpleNamespace(require_credit=blocked))
    pending = asyncio.create_task(client.post(f'/api/sessions/{session}/messages', json=payload()))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert (await client.post(f'/api/sessions/{session}/stop', json={})).json()['submission_revision'] == 1
        release.set()
        response = await pending
        assert response.status_code == 409 and response.json()['code'] == 'submission_revision_conflict'
        assert not kicks and not await app.state.store.rows('SELECT * FROM submission_receipts')
        assert not await app.state.store.rows('SELECT * FROM runs')
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_old_http_body_arriving_after_stop_cannot_acquire_a_fresh_epoch(api):
    client, app, session, _, kicks = api
    entered, release = asyncio.Event(), asyncio.Event()
    async def body():
        entered.set()
        await release.wait()
        yield json.dumps(payload()).encode()
    pending = asyncio.create_task(client.post(f'/api/sessions/{session}/messages', content=body(), headers={'content-type': 'application/json'}))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        stopped = (await client.post(f'/api/sessions/{session}/stop', json={})).json()
        release.set()
        response = await pending
        assert response.status_code == 409 and response.json()['code'] == 'submission_revision_conflict'
        assert not await app.state.store.rows('SELECT * FROM runs') and not kicks
        fresh = await client.post(f'/api/sessions/{session}/messages', json=payload('new-click', version=stopped['submission_revision']))
        assert fresh.status_code == 200 and not fresh.json()['replayed']
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_concurrent_preflight_failure_rechecks_the_committed_receipt(engine, store, monkeypatch):
    owner, session, _ = await setup(store)
    monkeypatch.setattr(engine, 'kick', lambda _: None)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0
    async def credit(_):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
            raise ServiceError('insufficient_credit', 'No remaining credit.', 402)
    engine.cloud = SimpleNamespace(require_credit=credit)
    pending = asyncio.create_task(send(engine, owner, session, payload()))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        accepted = await send(engine, owner, session, payload())
        release.set()
        retry = await pending
        assert retry == {**accepted, 'replayed': True}
        assert len(await store.rows('SELECT * FROM submission_receipts')) == 1
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_source_binding_and_receipt_are_one_transaction_with_rollback(engine, store, monkeypatch):
    owner, session, token = await setup(store)
    await observe_client(store, owner, session, token, 'first-window')
    monkeypatch.setattr(engine, 'kick', lambda _: None)
    # SQLite aborts after run/message/source writes but before the final receipt.
    await store.execute("""CREATE TRIGGER fail_receipt BEFORE INSERT ON submission_receipts
        BEGIN SELECT RAISE(ABORT, 'injected receipt failure'); END""")
    with pytest.raises(Exception, match='injected receipt failure'):
        await send(engine, owner, session, payload(), frontend_client_id='first-window', frontend_auth_hash=token_hash(token))
    for table in ('runs', 'messages', 'run_frontend_sources', 'submission_receipts', 'events'):
        assert not await store.rows(f'SELECT * FROM {table}')
    assert (await store.rows('SELECT title FROM sessions WHERE id=?', (session,)))[0]['title'] == '新的事情'
    await store.execute('DROP TRIGGER fail_receipt')
    accepted = await send(engine, owner, session, payload(), frontend_client_id='first-window', frontend_auth_hash=token_hash(token))
    await observe_client(store, owner, session, token, 'second-window')
    retry = await send(engine, owner, session, payload(), frontend_client_id='second-window', frontend_auth_hash=token_hash(token))
    assert retry['run_id'] == accepted['run_id'] and retry['replayed']
    rows = await store.rows('SELECT * FROM run_frontend_sources')
    assert len(rows) == 1 and rows[0]['client_id'] == 'first-window'
    receipt = (await store.rows('SELECT * FROM submission_receipts'))[0]
    assert rows[0]['run_id'] == receipt['run_id'] and rows[0]['message_id'] == receipt['message_id']


@pytest.mark.parametrize('crash_boundary', ['after_stop', 'after_commit'])
async def test_interrupt_restart_never_repeats_stop_at_either_crash_boundary(config, store, engine, monkeypatch, crash_boundary):
    owner, session, _ = await setup(store)
    monkeypatch.setattr(engine, 'kick', lambda _: None)
    old = await store.enqueue(session, 'Prior work')
    body = payload(mode='interrupt')
    if crash_boundary == 'after_stop':
        async def fail(*args, **kwargs):
            raise RuntimeError('simulated process exit before enqueue')
        with monkeypatch.context() as patch:
            patch.setattr(store, 'enqueue', fail)
            with pytest.raises(RuntimeError, match='simulated process exit'):
                await send(engine, owner, session, body)
    else:
        accepted = await send(engine, owner, session, body)
    assert await store.call(lambda db: revision(db, session)) == 1
    assert (await store.rows('SELECT status FROM runs WHERE id=?', (old,)))[0]['status'] == 'cancelled'
    reopened = Store(store.path)
    await reopened.initialize()
    restarted = Engine(config, reopened)
    kicks = []
    monkeypatch.setattr(restarted, 'kick', kicks.append)
    try:
        newer = await reopened.enqueue(session, 'A newer deliberate task after restart')
        if crash_boundary == 'after_stop':
            with pytest.raises(ServiceError) as rejected:
                await send(restarted, owner, session, body)
            assert rejected.value.code == 'submission_revision_conflict'
        else:
            result = await send(restarted, owner, session, body)
            assert result['run_id'] == accepted['run_id'] and result['replayed']
        assert await reopened.call(lambda db: revision(db, session)) == 1
        assert (await reopened.rows('SELECT status FROM runs WHERE id=?', (newer,)))[0]['status'] == 'queued'
        assert not kicks
    finally:
        await restarted.close()
        await reopened.close()


async def test_http_receipts_are_owned_missing_reads_are_inert_and_new_ids_are_new_work(api):
    client, app, session, _, kicks = api
    first = (await client.post(f'/api/sessions/{session}/messages', json=payload())).json()
    second = (await client.post(f'/api/sessions/{session}/messages', json=payload('second-click'))).json()
    assert first['run_id'] != second['run_id']
    other = (await client.post('/api/sessions', json={})).json()['id']
    before = await app.state.store.rows('SELECT * FROM events')
    missing = await client.get(f'/api/sessions/{other}/submissions/deliberate-send')
    assert missing.json() == {'found': False, 'client_submission_id': 'deliberate-send', 'submission_revision': 0}
    assert await app.state.store.rows('SELECT * FROM events') == before
    await client.post('/api/auth/logout', json={})
    await client.post('/api/auth/register', json={'username': 'another-person', 'password': 'synthetic-test-password'})
    for method, path in [('get', f'/api/sessions/{session}/submissions/deliberate-send'),
                         ('post', f'/api/sessions/{session}/messages')]:
        response = await getattr(client, method)(path, **({'json': payload()} if method == 'post' else {}))
        assert response.status_code == 404
    assert kicks == [session, session]


async def test_fork_has_no_receipts_or_inherited_revision_and_legacy_clients_still_work(api):
    client, app, session, _, _ = api
    await client.post(f'/api/sessions/{session}/messages', json=payload())
    await client.post(f'/api/sessions/{session}/stop', json={})
    child = (await client.post(f'/api/sessions/{session}/fork', json={})).json()['id']
    assert (await client.get(f'/api/sessions/{child}')).json()['submission_revision'] == 0
    assert not (await client.get(f'/api/sessions/{child}/submissions/deliberate-send')).json()['found']
    new = await client.post(f'/api/sessions/{child}/messages', json=payload())
    assert new.status_code == 200 and not new.json()['replayed']
    legacy = await client.post(f'/api/sessions/{child}/messages', json={'text': 'Legacy message'})
    assert legacy.status_code == 200 and list(legacy.json()) == ['run_id']
    assert len(await app.state.store.rows('SELECT * FROM submission_receipts WHERE session_id=?', (child,))) == 1


@pytest.mark.parametrize('body', [dict(text='x', client_submission_id='only-id'),
                                dict(text='x', expected_submission_revision=0),
                                dict(text='x', client_submission_id='invalid id', expected_submission_revision=0),
                                dict(text='x', client_submission_id='valid', expected_submission_revision=True)])
async def test_partial_or_invalid_intent_cannot_silently_fall_back_to_legacy(api, body):
    client, app, session, _, kicks = api
    response = await client.post(f'/api/sessions/{session}/messages', json=body)
    assert response.status_code == 422
    assert not kicks and not await app.state.store.rows('SELECT * FROM runs')


async def test_schema_14_migration_preserves_original_columns_and_rows(config):
    path = config.data_dir / 'submission-upgrade.sqlite3'
    original = Store(path)
    await original.initialize()
    owner, session, _ = await setup(original)
    run = await original.enqueue(session, 'A completed pre-upgrade conversation')
    await original.execute("UPDATE runs SET status='complete',finished_at=? WHERE id=?", (now(), run))
    await original.execute('DROP TABLE submission_receipts')
    await original.execute('DROP TABLE stop_receipts')
    await original.execute('ALTER TABLE sessions DROP COLUMN submission_revision')
    await original.execute('PRAGMA user_version=14')
    def snapshot(db, tables=None):
        if tables is None:
            tables = {row[0]: [column[1] for column in db.execute('PRAGMA table_info("'+row[0]+'")')]
                      for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        return tables, {table: [tuple(row) for row in db.execute('SELECT '+','.join('"'+column+'"' for column in columns)+' FROM "'+table+'" ORDER BY rowid')]
                        for table, columns in tables.items()}
    tables, before = await original.call(snapshot)
    await original.close()
    reopened = Store(path)
    try:
        await reopened.initialize()
        await reopened.initialize()
        assert (await reopened.call(lambda db: snapshot(db, tables)))[1] == before
        assert await reopened.call(lambda db: revision(db, session, owner)) == 0
        assert not await reopened.rows('SELECT * FROM submission_receipts')
        assert not await reopened.rows('SELECT * FROM stop_receipts')
        assert (await reopened.rows('PRAGMA user_version'))[0]['user_version'] == SCHEMA_VERSION
        assert (await reopened.rows('PRAGMA integrity_check'))[0]['integrity_check'] == 'ok'
    finally:
        await reopened.close()


async def test_delayed_stop_body_requires_its_own_receipt_and_retry_cannot_stop_new_work(api):
    client, app, session, _, kicks = api
    entered, release = asyncio.Event(), asyncio.Event()
    stop_body = {'client_stop_id': 'delayed-stop'}
    async def body():
        entered.set()
        await release.wait()
        yield json.dumps(stop_body).encode()
    pending = asyncio.create_task(client.post(f'/api/sessions/{session}/stop', content=body(), headers={'content-type': 'application/json'}))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        # This snapshot is authoritative but does NOT prove the in-flight Stop
        # completed. The client must remain fenced until this exact receipt exists.
        assert (await client.get(f'/api/sessions/{session}')).json()['submission_revision'] == 0
        missing = (await client.get(f'/api/sessions/{session}/stops/delayed-stop')).json()
        assert missing == {'found': False, 'client_stop_id': 'delayed-stop'}
        assert not await app.state.store.rows('SELECT * FROM runs')
        release.set()
        response = await pending
        assert response.json() == {'ok': True, 'client_stop_id': 'delayed-stop', 'submission_revision': 1}
        receipt = (await client.get(f'/api/sessions/{session}/stops/delayed-stop')).json()
        assert receipt == {'found': True, 'client_stop_id': 'delayed-stop', 'submission_revision': 1}
        accepted = (await client.post(f'/api/sessions/{session}/messages', json=payload(version=receipt['submission_revision']))).json()
        before = await app.state.store.rows('SELECT * FROM events')
        repeated = await client.post(f'/api/sessions/{session}/stop', json=stop_body)
        assert repeated.json() == response.json()
        assert (await app.state.store.rows('SELECT status FROM runs WHERE id=?', (accepted['run_id'],)))[0]['status'] == 'queued'
        assert await app.state.store.rows('SELECT * FROM events') == before and kicks == [session]
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_twenty_stop_retries_advance_revision_and_broadcast_only_once(api):
    client, app, session, _, _ = api
    run = await app.state.store.enqueue(session, 'Existing work')
    results = await asyncio.gather(*(client.post(f'/api/sessions/{session}/stop', json={'client_stop_id': 'one-stop'}) for _ in range(20)))
    assert all(result.status_code == 200 and result.json() == {'ok': True, 'client_stop_id': 'one-stop', 'submission_revision': 1} for result in results)
    assert await app.state.store.call(lambda db: revision(db, session)) == 1
    assert len(await app.state.store.rows('SELECT * FROM stop_receipts')) == 1
    assert len(await app.state.store.rows('SELECT * FROM events')) == 1
    assert (await app.state.store.rows('SELECT status FROM runs WHERE id=?', (run,)))[0]['status'] == 'cancelled'


async def test_stop_receipts_are_owned_scoped_and_retain_the_original_revision(api):
    client, app, session, _, _ = api
    await client.post(f'/api/sessions/{session}/stop', json={'client_stop_id': 'original-stop'})
    await client.post(f'/api/sessions/{session}/stop', json={'client_stop_id': 'later-stop'})
    # A Stop receipt confirms its own operation, never whichever unrelated Stop
    # happens to be the latest when the client polls.
    old = (await client.get(f'/api/sessions/{session}/stops/original-stop')).json()
    assert old['submission_revision'] == 1
    other = (await client.post('/api/sessions', json={})).json()['id']
    assert (await client.get(f'/api/sessions/{other}/stops/original-stop')).json() == {'found': False, 'client_stop_id': 'original-stop'}
    assert (await client.post(f'/api/sessions/{other}/stop', json={'client_stop_id': 'original-stop'})).json()['submission_revision'] == 1
    assert await app.state.store.call(lambda db: revision(db, session)) == 2
    await client.post('/api/auth/logout', json={})
    await client.post('/api/auth/register', json={'username': 'foreign-stop', 'password': 'synthetic-test-password'})
    assert (await client.get(f'/api/sessions/{session}/stops/original-stop')).status_code == 404
    assert (await client.post(f'/api/sessions/{session}/stop', json={'client_stop_id': 'original-stop'})).status_code == 404
    assert await app.state.store.call(lambda db: revision(db, session)) == 2


async def test_stop_keeps_control_lock_after_receipt_and_survives_caller_cancellation(engine, store, monkeypatch):
    owner, session, _ = await setup(store)
    kicks = []
    monkeypatch.setattr(engine, 'kick', kicks.append)
    run = await store.enqueue(session, 'Running work with asynchronous cleanup')
    assert await store.claim(session) == run
    started, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def active_work():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelling.set()
            await release.wait()
            await store.execute("UPDATE runs SET status='interrupted' WHERE id=?", (run,))
            raise
    active = asyncio.create_task(active_work())
    engine.active_runs[session] = active
    await started.wait()
    stopped = asyncio.create_task(engine.stop(session, stop_intent=StopIntent(owner, 'cancelled-http')))
    pending = None
    try:
        await asyncio.wait_for(cancelling.wait(), 2)
        receipt = await store.call(lambda db: lookup_stop(db, session, owner, 'cancelled-http'))
        assert receipt == {'client_stop_id': 'cancelled-http', 'submission_revision': 1}
        assert engine.controls[session].locked()
        stopped.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stopped
        pending = asyncio.create_task(send(engine, owner, session, payload(version=1)))
        # The exact receipt lets the client request new work, but the server lock
        # prevents admission until cancellation of the old active work settles.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(pending), .05)
        assert not await store.rows('SELECT * FROM submission_receipts')
        release.set()
        accepted = await asyncio.wait_for(pending, 2)
        assert accepted['accepted'] and not accepted['replayed']
        before = await store.rows('SELECT * FROM events')
        await engine.stop(session, stop_intent=StopIntent(owner, 'cancelled-http'))
        assert await store.rows('SELECT * FROM events') == before
        assert (await store.rows('SELECT status FROM runs WHERE id=?', (accepted['run_id'],)))[0]['status'] == 'queued'
    finally:
        release.set()
        await asyncio.gather(stopped, active, *(tuple([pending]) if pending else ()), return_exceptions=True)
        engine.active_runs.pop(session, None)


async def test_stop_restart_after_fence_commit_does_not_replay_cancellation(config, store, engine, monkeypatch):
    owner, session, _ = await setup(store)
    old = await store.enqueue(session, 'Active before simulated process exit')
    assert await store.claim(session) == old
    original_call = store.call
    async def crash_after_transaction(operation):
        result = await original_call(operation)
        if operation.__name__ == 'cancel_queue':
            raise RuntimeError('simulated exit after stop transaction')
        return result
    with monkeypatch.context() as patch:
        patch.setattr(store, 'call', crash_after_transaction)
        with pytest.raises(RuntimeError, match='simulated exit'):
            await engine.stop(session, stop_intent=StopIntent(owner, 'crash-stop'))
    assert await store.call(lambda db: lookup_stop(db, session, owner, 'crash-stop')) == {'client_stop_id': 'crash-stop', 'submission_revision': 1}
    reopened = Store(store.path)
    await reopened.initialize()
    restarted = Engine(config, reopened)
    try:
        assert (await reopened.rows('SELECT status FROM runs WHERE id=?', (old,)))[0]['status'] == 'interrupted'
        newer = await reopened.enqueue(session, 'Work deliberately accepted after restart')
        before = await reopened.rows('SELECT * FROM events')
        receipt = await restarted.stop(session, stop_intent=StopIntent(owner, 'crash-stop'))
        assert receipt['submission_revision'] == 1
        assert (await reopened.rows('SELECT status FROM runs WHERE id=?', (newer,)))[0]['status'] == 'queued'
        assert await reopened.rows('SELECT * FROM events') == before
    finally:
        await restarted.close()
        await reopened.close()


async def test_stop_receipt_write_failure_rolls_back_revision_and_cancellation(api):
    client, app, session, _, _ = api
    run = await app.state.store.enqueue(session, 'Must remain queued after failed transaction')
    await app.state.store.execute("""CREATE TRIGGER fail_stop BEFORE INSERT ON stop_receipts
        BEGIN SELECT RAISE(ABORT, 'injected stop receipt failure'); END""")
    owner = (await app.state.store.rows('SELECT user_id FROM sessions WHERE id=?', (session,)))[0]['user_id']
    with pytest.raises(Exception, match='injected stop receipt failure'):
        await app.state.engine.stop(session, stop_intent=StopIntent(owner, 'transaction-stop'))
    assert await app.state.store.call(lambda db: revision(db, session)) == 0
    assert not await app.state.store.rows('SELECT * FROM stop_receipts')
    assert (await app.state.store.rows('SELECT status FROM runs WHERE id=?', (run,)))[0]['status'] == 'queued'


async def test_stop_post_commit_notification_failure_retains_exact_receipt(api, monkeypatch):
    client, app, session, _, _ = api
    async def fail(*args, **kwargs):
        raise ValueError('injected Stop notification failure')
    monkeypatch.setattr(app.state.engine, 'emit', fail)
    body = {'client_stop_id': 'stop-notification-failure'}
    response = await client.post(f'/api/sessions/{session}/stop', json=body)
    assert response.status_code == 503 and response.json()['code'] == 'stop_acknowledgement_unavailable'
    receipt = (await client.get(f'/api/sessions/{session}/stops/stop-notification-failure')).json()
    assert receipt == {'found': True, 'client_stop_id': body['client_stop_id'], 'submission_revision': 1}
    retry = await client.post(f'/api/sessions/{session}/stop', json=body)
    assert retry.status_code == 200 and retry.json() == {'ok': True, **{key: value for key, value in receipt.items() if key != 'found'}}


async def test_stop_empty_body_remains_compatible_and_fork_does_not_copy_receipts(api):
    client, app, session, _, _ = api
    assert (await client.post(f'/api/sessions/{session}/stop')).json() == {'ok': True, 'submission_revision': 1}
    assert (await client.post(f'/api/sessions/{session}/stop', json={})).json() == {'ok': True, 'submission_revision': 2}
    await client.post(f'/api/sessions/{session}/stop', json={'client_stop_id': 'parent-stop'})
    child = (await client.post(f'/api/sessions/{session}/fork', json={})).json()['id']
    assert (await client.get(f'/api/sessions/{child}/stops/parent-stop')).json() == {'found': False, 'client_stop_id': 'parent-stop'}
    assert await app.state.store.call(lambda db: revision(db, child)) == 0
