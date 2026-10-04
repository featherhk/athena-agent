"""Stop also invalidates older inputs awaiting admission, without holding I/O locks."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from athena.api import create_app
from athena.auth import Auth
from athena.errors import UserError


class Gate:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def wait(self):
        self.entered.set()
        await self.release.wait()


async def owner_session(store):
    user, _ = await Auth(store).register('stop-admission', 'synthetic-test-password')
    return user['id'], await store.session(user_id=user['id'])


def delay_preflight(engine, monkeypatch, stage):
    gate = Gate()
    target, name = {
        'owner': (engine.tools, 'owner'),
        'profile': (engine.models, 'selected'),
        'key': (engine.models, 'require_key'),
    }.get(stage, (None, None))
    if stage == 'credit':
        monkeypatch.setattr(engine, 'cloud', SimpleNamespace(require_credit=lambda _: gate.wait()))
    else:
        original = getattr(target, name)

        async def delayed(*args, **kwargs):
            await gate.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(target, name, delayed)
    return gate


async def admission_state(store):
    return {table: await store.rows(f'SELECT * FROM {table} ORDER BY rowid') for table in
            ('runs', 'messages', 'requests', 'events', 'app_actions', 'run_frontend_sources')}


@pytest.mark.parametrize('stage', ['owner', 'profile', 'key', 'credit'])
@pytest.mark.parametrize('mode', ['queue', 'interrupt'])
async def test_stop_rejects_pre_stop_admission_after_delayed_preflight(engine, store, monkeypatch, stage, mode):
    _, session = await owner_session(store)
    queued = await store.enqueue(session, 'Previously accepted queued work')
    kicks = []
    monkeypatch.setattr(engine, 'kick', kicks.append)
    gate = delay_preflight(engine, monkeypatch, stage)
    pending = asyncio.create_task(engine.submit(session, 'Keep this pending input in the client draft', mode))
    try:
        await asyncio.wait_for(gate.entered.wait(), 1)
        # Stop must complete while provider/ownership preflight remains blocked.
        await asyncio.wait_for(engine.stop(session), 1)
        assert (await store.rows('SELECT status FROM runs WHERE id=?', (queued,)))[0]['status'] == 'cancelled'
        assert not pending.done()
        stopped = await admission_state(store)
        gate.release.set()
        with pytest.raises(UserError) as rejected:
            await asyncio.wait_for(pending, 1)
        assert rejected.value.code == 'submission_stopped'
        assert await admission_state(store) == stopped
        assert not kicks, 'Rejected input must not wake the model or create an action/event'
        # An explicit fresh submission remains possible in this same session.
        fresh = await engine.submit(session, 'Send this after Stop')
        assert (await store.rows('SELECT status FROM runs WHERE id=?', (fresh,)))[0]['status'] == 'queued'
        assert kicks == [session]
    finally:
        gate.release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_interrupt_admits_its_replacement_but_rejects_an_older_preflight(engine, store, monkeypatch):
    _, session = await owner_session(store)
    original = await store.enqueue(session, 'Old queued work')
    kicks = []
    monkeypatch.setattr(engine, 'kick', kicks.append)
    gate = Gate()
    calls = 0

    async def credit(_):
        nonlocal calls
        calls += 1
        if calls == 1:
            await gate.wait()

    monkeypatch.setattr(engine, 'cloud', SimpleNamespace(require_credit=credit))
    pending = asyncio.create_task(engine.submit(session, 'Older input still checking credit'))
    try:
        await asyncio.wait_for(gate.entered.wait(), 1)
        replacement = await asyncio.wait_for(engine.submit(session, 'Do this instead', 'interrupt'), 1)
        rows = await store.rows('SELECT id,status FROM runs ORDER BY created_at')
        assert rows == [{'id': original, 'status': 'cancelled'}, {'id': replacement, 'status': 'queued'}]
        interrupted = await admission_state(store)
        gate.release.set()
        with pytest.raises(UserError) as rejected:
            await pending
        assert rejected.value.code == 'submission_stopped'
        assert await admission_state(store) == interrupted
        assert kicks == [session]
    finally:
        gate.release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_tool_stop_invalidates_only_its_owned_target_admission(engine, store, monkeypatch):
    owner, source = await owner_session(store)
    target, unrelated = await store.session(user_id=owner), await store.session(user_id=owner)
    source_run = await store.enqueue(source, 'Stop another conversation')
    assert await store.claim(source) == source_run
    target_run = await store.enqueue(target, 'Previously accepted target work')
    monkeypatch.setattr(engine, 'kick', lambda _: None)
    gate = delay_preflight(engine, monkeypatch, 'credit')
    pending = asyncio.create_task(engine.submit(target, 'Older target input'))
    elsewhere = asyncio.create_task(engine.submit(unrelated, 'Unrelated input remains valid'))
    try:
        await asyncio.wait_for(gate.entered.wait(), 1)
        receipt = await asyncio.wait_for(engine.stop_from_tool(source, source_run, target), 1)
        assert receipt['status'] == 'applied' and receipt['completed'] and receipt['remaining'] == []
        assert (await store.rows('SELECT status FROM runs WHERE id=?', (target_run,)))[0]['status'] == 'cancelled'
        gate.release.set()
        with pytest.raises(UserError) as rejected:
            await pending
        assert rejected.value.code == 'submission_stopped'
        other_run = await elsewhere
        assert (await store.rows('SELECT status FROM runs WHERE id=?', (other_run,)))[0]['status'] == 'queued'
        assert (await store.rows('SELECT status FROM runs WHERE id=?', (source_run,)))[0]['status'] == 'running'
        assert len(await store.rows('SELECT id FROM runs WHERE session_id=?', (target,))) == 1
    finally:
        gate.release.set()
        await asyncio.gather(pending, elsewhere, return_exceptions=True)


async def test_rejected_foreign_tool_stop_does_not_invalidate_target_input(engine, store, monkeypatch):
    _, source = await owner_session(store)
    foreign, _ = await Auth(store).register('other-stop-owner', 'synthetic-test-password')
    target = await store.session(user_id=foreign['id'])
    run = await store.enqueue(source, 'Cannot control another account')
    await store.claim(source)
    monkeypatch.setattr(engine, 'kick', lambda _: None)
    gate = delay_preflight(engine, monkeypatch, 'credit')
    pending = asyncio.create_task(engine.submit(target, 'Other owner input'))
    try:
        await asyncio.wait_for(gate.entered.wait(), 1)
        with pytest.raises(UserError) as rejected:
            await engine.stop_from_tool(source, run, target)
        assert rejected.value.code == 'not_found'
        gate.release.set()
        accepted = await pending
        assert (await store.rows('SELECT status FROM runs WHERE id=?', (accepted,)))[0]['status'] == 'queued'
    finally:
        gate.release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_reset_keeps_its_existing_rejection_and_allows_fresh_input(engine, store, monkeypatch):
    owner, session = await owner_session(store)
    monkeypatch.setattr(engine, 'kick', lambda _: None)
    gate = delay_preflight(engine, monkeypatch, 'credit')
    pending = asyncio.create_task(engine.submit(session, 'Input overtaken by Reset'))
    try:
        await asyncio.wait_for(gate.entered.wait(), 1)
        await asyncio.wait_for(engine.reset_user(owner), 2)
        reset = await admission_state(store)
        gate.release.set()
        with pytest.raises(UserError) as rejected:
            await pending
        assert rejected.value.code == 'resetting'
        assert await admission_state(store) == reset
        accepted = await engine.submit(session, 'Fresh input after Reset')
        assert (await store.rows('SELECT status FROM runs WHERE id=?', (accepted,)))[0]['status'] == 'queued'
    finally:
        gate.release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_http_stop_reports_pending_submission_rejected_instead_of_accepting_new_work(config, monkeypatch):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        engine = app.state.engine
        monkeypatch.setattr(engine, 'kick', lambda _: None)
        gate = delay_preflight(engine, monkeypatch, 'key')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317') as browser:
            registered = await browser.post('/api/auth/register', json={'username': 'http-stop', 'password': 'synthetic-test-password'})
            assert registered.status_code == 200
            session = (await browser.post('/api/sessions', json={})).json()['id']
            pending = asyncio.create_task(browser.post(f'/api/sessions/{session}/messages', json={'text': 'Retain my words'}))
            try:
                await asyncio.wait_for(gate.entered.wait(), 1)
                response = await asyncio.wait_for(browser.post(f'/api/sessions/{session}/stop', json={}), 1)
                assert response.status_code == 200
                gate.release.set()
                response = await pending
                assert response.status_code == 400 and response.json()['code'] == 'submission_stopped'
                snapshot = (await browser.get(f'/api/sessions/{session}')).json()
                assert snapshot['runs'] == [] and snapshot['messages'] == [] and snapshot['app_actions'] == []
            finally:
                gate.release.set()
                await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize('stage', ['exists', 'attachments'])
@pytest.mark.parametrize('mode', ['queue', 'interrupt'])
async def test_http_stop_covers_handler_checks_and_keeps_owned_file_for_a_fresh_send(config, monkeypatch, stage, mode):
    from athena.files import attachment_info

    app = create_app(config)
    async with app.router.lifespan_context(app):
        engine, store = app.state.engine, app.state.store
        kicks = []
        monkeypatch.setattr(engine, 'kick', kicks.append)
        gate = Gate()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317') as browser:
            registered = await browser.post('/api/auth/register', json={'username': 'http-file-stop', 'password': 'synthetic-test-password'})
            assert registered.status_code == 200
            session = (await browser.post('/api/sessions', json={})).json()['id']
            uploaded = await browser.post(f'/api/sessions/{session}/files', params={'name': 'retain.md'}, content=b'Retain this input file.')
            assert uploaded.status_code == 201
            file = uploaded.json()
            if stage == 'attachments':
                async def delayed_attachments(*args, **kwargs):
                    await gate.wait()
                    return await attachment_info(*args, **kwargs)
                monkeypatch.setattr('athena.api.attachment_info', delayed_attachments)
            else:
                original_rows, armed = store.rows, True

                async def delayed_exists(sql, *args):
                    nonlocal armed
                    if armed and sql == 'SELECT id FROM sessions WHERE id=? AND user_id=?':
                        armed = False
                        await gate.wait()
                    return await original_rows(sql, *args)

                monkeypatch.setattr(store, 'rows', delayed_exists)
            pending = asyncio.create_task(browser.post(f'/api/sessions/{session}/messages', json={
                'text': 'My pending input', 'file_ids': [file['id']], 'mode': mode,
                '_admission_epoch': 999999,  # An untrusted body cannot choose the host's epoch.
            }))
            try:
                await asyncio.wait_for(gate.entered.wait(), 1)
                stopped = await asyncio.wait_for(browser.post(f'/api/sessions/{session}/stop', json={}), 1)
                assert stopped.status_code == 200
                before = await admission_state(store)
                gate.release.set()
                rejected = await pending
                assert rejected.status_code == 400 and rejected.json()['code'] == 'submission_stopped'
                assert await admission_state(store) == before and kicks == []
                assert (await browser.get(file['content_url'])).content == b'Retain this input file.'
                fresh = await browser.post(f'/api/sessions/{session}/messages', json={
                    'text': 'Explicit new input after Stop', 'file_ids': [file['id']], 'mode': mode,
                })
                assert fresh.status_code == 200
                snapshot = (await browser.get(f'/api/sessions/{session}')).json()
                assert len(snapshot['runs']) == len(snapshot['messages']) == 1
                assert snapshot['runs'][0]['id'] == fresh.json()['run_id']
                assert snapshot['messages'][0]['attachments'][0]['id'] == file['id']
                assert kicks == [session]
                assert '_admission_epoch' not in app.openapi()['components']['schemas']['Input']['properties']
            finally:
                gate.release.set()
                await asyncio.gather(pending, return_exceptions=True)


async def test_unauthorized_http_stop_cannot_invalidate_an_owned_pending_submission(config, monkeypatch):
    from athena.files import attachment_info

    app = create_app(config)
    async with app.router.lifespan_context(app):
        monkeypatch.setattr(app.state.engine, 'kick', lambda _: None)
        gate = Gate()

        async def delayed_attachments(*args, **kwargs):
            await gate.wait()
            return await attachment_info(*args, **kwargs)

        monkeypatch.setattr('athena.api.attachment_info', delayed_attachments)
        transport = httpx.ASGITransport(app)
        async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1:4317') as owner:
            await owner.post('/api/auth/register', json={'username': 'http-stop-owner', 'password': 'synthetic-test-password'})
            session = (await owner.post('/api/sessions', json={})).json()['id']
            pending = asyncio.create_task(owner.post(f'/api/sessions/{session}/messages', json={'text': 'Owned pending input'}))
            try:
                await asyncio.wait_for(gate.entered.wait(), 1)
                async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1:4317') as other:
                    anonymous = await other.post(f'/api/sessions/{session}/stop', json={})
                    assert anonymous.status_code == 401
                    await other.post('/api/auth/register', json={'username': 'http-stop-outsider', 'password': 'synthetic-test-password'})
                    foreign = await other.post(f'/api/sessions/{session}/stop', json={})
                    assert foreign.status_code == 404
                gate.release.set()
                accepted = await pending
                assert accepted.status_code == 200
                runs = await app.state.store.rows('SELECT id,status FROM runs WHERE session_id=?', (session,))
                assert runs == [{'id': accepted.json()['run_id'], 'status': 'queued'}]
            finally:
                gate.release.set()
                await asyncio.gather(pending, return_exceptions=True)
