"""A short-lived effect is a durable operation, not a replayable HTML decoration."""
import asyncio
from datetime import datetime, timezone
import json

import httpx
import pytest

from athena.api import create_app
from athena.auth import Auth
from athena.component_views import update_view
from athena.errors import UserError
from athena.registry import Registry, expire_components


async def setup(config, store):
    user, _ = await Auth(store).register('effect-owner', 'test-password')
    session = await store.session(user_id=user['id'])
    return user['id'], session, Registry(config, store)


def effect(**changes):
    return {'kind': 'component', 'action': 'register', 'id': 'celebration',
            'source': '<canvas aria-label="A brief celebration"></canvas>', 'placement': 'desktop',
            'duration_ms': 10000, **changes}


async def test_registration_has_an_absolute_deadline_without_changing_persistent_components(config, store):
    _, session, registry = await setup(config, store)
    before = datetime.now(timezone.utc)
    registration = await registry.operate(effect(), session)
    assert registration['duration_ms'] == 10000
    deadline = datetime.fromisoformat(registration['expires_at'])
    assert 9.9 <= (deadline - before).total_seconds() <= 12
    component = (await store.components(session))[0]
    assert component['duration_ms'] == 10000 and component['expires_at'] == registration['expires_at']
    await registry.operate(effect(id='notes', duration_ms=None, source='<textarea>Keep my work</textarea>'), session)
    persistent = next(row for row in await store.components(session) if row['id'] == 'notes')
    assert persistent['duration_ms'] is None and persistent['expires_at'] is None


async def test_expiry_appends_one_terminal_version_and_preserves_original_source(config, store):
    _, session, registry = await setup(config, store)
    await registry.operate(effect(), session)
    original = (await store.rows('SELECT * FROM components'))[0]
    source = config.data_dir / original['file_path']
    original_text = source.read_text()
    changed = await store.call(lambda db: expire_components(db, stamp='9999-01-01T00:00:00+00:00'))
    assert changed == {session}
    assert not await store.components(session)
    assert await store.call(lambda db: expire_components(db, stamp='9999-01-01T00:00:00+00:00')) == set()
    versions = await store.rows('SELECT * FROM components ORDER BY version')
    assert len(versions) == 2 and versions[0] == original and not versions[1]['active']
    assert versions[1]['expires_at'] == original['expires_at'] and source.read_text() == original_text
    events = await store.rows("SELECT payload FROM events WHERE type='registry_changed' ORDER BY seq")
    finished = json.loads(events[-1]['payload'])
    assert finished['action'] == 'finish' and finished['reason'] == 'expired' and not finished['enabled']
    assert finished['finished_version'] == 1 and finished['version'] == 2
    read = await registry.operate({'kind': 'component', 'action': 'read', 'id': 'celebration'}, session)
    assert not read['enabled'] and read['duration_ms'] == 10000


async def test_snapshot_and_layout_expire_before_presenting_even_without_scheduler(config, store, monkeypatch):
    _, session, registry = await setup(config, store)
    await registry.operate(effect(), session)
    monkeypatch.setattr('athena.registry.now', lambda: '9999-01-01T00:00:00+00:00')
    assert not await store.components(session)
    with pytest.raises(UserError):
        await update_view(store, session, 'celebration', 1, 0, {'hidden': False})
    assert len(await store.rows('SELECT * FROM components')) == 2
    assert not (await registry.operate({'kind': 'component', 'action': 'read', 'id': 'celebration'}, session))['enabled']


async def test_finish_is_exact_version_idempotent_and_cannot_end_a_new_replay(config, store):
    _, session, registry = await setup(config, store)
    await registry.operate(effect(), session)
    first = await registry.operate({'kind': 'component', 'action': 'finish', 'id': 'celebration', 'version': 1}, session)
    assert first['changed'] and first['accepted'] and first['status'] == 'finished'
    second = await registry.finish_component(session, 'celebration', 1)
    assert second['accepted'] and not second['changed']
    replay = await registry.operate(effect(), session)
    assert replay['version'] == 3 and replay['enabled']
    late = await registry.finish_component(session, 'celebration', 1)
    assert not late['accepted'] and not late['changed']
    assert (await store.components(session))[0]['version'] == 3
    await registry.finish_component(session, 'celebration', 3)
    restored = await registry.operate({'kind': 'component', 'action': 'restore', 'id': 'celebration', 'version': 1}, session)
    assert restored['version'] == 5 and restored['duration_ms'] == 10000
    assert datetime.fromisoformat(restored['expires_at']) > datetime.fromisoformat(replay['expires_at'])


async def test_restart_ends_even_unexpired_effects_and_never_deletes_source_or_persistent_work(config, store):
    _, session, registry = await setup(config, store)
    await registry.operate(effect(duration_ms=120000), session)
    await registry.operate(effect(id='notes', duration_ms=None), session)
    await store.initialize()
    assert [row['id'] for row in await store.components(session)] == ['notes']
    versions = await store.rows("SELECT * FROM components WHERE id='celebration' ORDER BY version")
    assert len(versions) == 2 and not versions[-1]['active']
    assert (config.data_dir / versions[0]['file_path']).is_file()
    event = (await store.rows("SELECT payload FROM events WHERE type='registry_changed' ORDER BY seq DESC LIMIT 1"))[0]
    assert json.loads(event['payload'])['reason'] == 'restarted'
    await store.initialize()
    assert len(await store.rows("SELECT * FROM components WHERE id='celebration'")) == 2


async def test_fork_keeps_history_but_never_activates_a_copy_of_a_live_or_finished_effect(config, store):
    _, session, registry = await setup(config, store)
    await registry.operate(effect(duration_ms=120000), session)
    await registry.operate(effect(id='notes', duration_ms=None), session)
    fork = await store.fork(session)
    assert {row['id'] for row in await store.components(session)} == {'celebration', 'notes'}
    assert [row['id'] for row in await store.components(fork)] == ['notes']
    inherited = await registry.operate({'kind': 'component', 'action': 'read', 'id': 'celebration', 'version': 1}, fork)
    assert inherited['source'] == effect()['source'] and not inherited['enabled']
    await registry.finish_component(session, 'celebration', 1)
    another = await store.fork(session)
    assert [row['id'] for row in await store.components(another)] == ['notes']
    explicit = await registry.operate({'kind': 'component', 'action': 'restore', 'id': 'celebration', 'version': 1}, fork)
    assert explicit['enabled'] and explicit['version'] == 3
    assert [row['id'] for row in await store.components(session)] == ['notes']


async def test_stop_ends_an_effect_even_after_model_completion_but_keeps_persistent_surfaces(config, store, engine):
    _, session, registry = await setup(config, store)
    await registry.operate(effect(), session)
    await registry.operate(effect(id='notes', duration_ms=None), session)
    run = await store.enqueue(session, 'Celebrate briefly')
    await store.execute("UPDATE runs SET status='complete' WHERE id=?", (run,))
    await engine.stop(session)
    assert [row['id'] for row in await store.components(session)] == ['notes']
    events = await store.rows("SELECT payload FROM events WHERE type='registry_changed' ORDER BY seq")
    assert json.loads(events[-1]['payload'])['reason'] == 'stopped'
    # A completed or stopped model round cannot silently create another effect.
    with pytest.raises(UserError, match='stopped'):
        await engine.tools.execute('runtime', {'op': 'registry', 'args': effect(id='late')}, session, run)
    assert len(await store.rows("SELECT * FROM components WHERE id='late'")) == 0


async def test_registration_in_flight_cannot_cross_the_stop_boundary(config, store, engine, monkeypatch):
    _, session, registry = await setup(config, store)
    original_call = store.call
    entered, resume = asyncio.Event(), asyncio.Event()
    async def paused(operation):
        if operation.__name__ == 'register':
            entered.set()
            await resume.wait()
        return await original_call(operation)
    monkeypatch.setattr(store, 'call', paused)
    pending = asyncio.create_task(registry.operate(effect(), session))
    await entered.wait()
    await engine.stop(session)
    resume.set()
    with pytest.raises(UserError, match='stopped'):
        await pending
    assert not await store.components(session)
    assert not list((config.data_dir / 'extensions').rglob('*.html'))


async def test_running_round_is_fenced_while_stop_cancellation_is_still_completing(config, store, engine):
    _, session, _ = await setup(config, store)
    run = await store.enqueue(session, 'Celebrate briefly')
    assert await store.claim(session) == run
    await engine.stop(session)
    row = (await store.rows('SELECT status,phase FROM runs WHERE id=?', (run,)))[0]
    assert row == {'status': 'running', 'phase': 'stopped'}  # No actor in this fixture finalizes it yet.
    with pytest.raises(UserError, match='stopped'):
        await engine.tools.execute('update_frontend', {'op': 'upsert', 'id': 'late', 'html': '<canvas></canvas>', 'duration_ms': 1000}, session, run)
    assert not await store.components(session)


async def test_scheduler_finishes_expired_effect_before_refresh(config, store, engine, monkeypatch):
    _, session, registry = await setup(config, store)
    await registry.operate(effect(), session)
    monkeypatch.setattr('athena.registry.now', lambda: '9999-01-01T00:00:00+00:00')
    emitted = []
    original_emit = engine.emit
    async def observe_emit(sid, kind, payload):
        if kind == 'refresh':
            assert not await store.components(sid)
            last = (await store.rows('SELECT * FROM components WHERE session_id=? ORDER BY version DESC LIMIT 1', (sid,)))[0]
            assert not last['active']
            emitted.append(sid)
        return await original_emit(sid, kind, payload)
    monkeypatch.setattr(engine, 'emit', observe_emit)
    await engine.tick()
    assert session in emitted


@pytest.mark.parametrize('duration', [True, False, 0, -1, 120001, 1.5, '1000'])
async def test_duration_is_bounded_and_invalid_registration_is_not_saved(config, store, duration):
    _, session, registry = await setup(config, store)
    with pytest.raises(UserError):
        await registry.operate(effect(duration_ms=duration), session)
    assert not await store.components(session)
    assert not list((config.data_dir / 'extensions').rglob('*.html'))


async def test_finish_http_is_owned_versioned_and_committed_before_refresh(config):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317') as client:
            await client.post('/api/auth/register', json={'username': 'finish-owner', 'password': 'test-password'})
            sid = (await client.post('/api/sessions', json={})).json()['id']
            await app.state.engine.tools.registry.operate(effect(), sid)
            endpoint = f'/api/sessions/{sid}/components/celebration/finish'
            assert (await client.post(endpoint, json={'version': True})).status_code == 422
            first = (await client.post(endpoint, json={'version': 1})).json()
            assert first['changed'] and not (await client.get(f'/api/sessions/{sid}')).json()['components']
            assert (await client.post(endpoint, json={'version': 1})).json()['changed'] is False
            await client.post('/api/auth/logout')
            assert (await client.post(endpoint, json={'version': 1})).status_code == 401
            await client.post('/api/auth/register', json={'username': 'other-owner', 'password': 'test-password'})
            assert (await client.post(endpoint, json={'version': 1})).status_code == 404


async def test_source_updates_inherit_duration_and_only_explicit_null_becomes_persistent(config, store):
    _, session, registry = await setup(config, store)
    initial = await registry.operate(effect(), session)
    updated = await registry.operate({'kind': 'component', 'action': 'register', 'id': 'celebration', 'source': '<canvas>Updated</canvas>'}, session)
    assert updated['duration_ms'] == initial['duration_ms'] and updated['expires_at'] > initial['expires_at']
    await registry.operate({'kind': 'component', 'action': 'remove', 'id': 'celebration'}, session)
    removed = await registry.operate({'kind': 'component', 'action': 'read', 'id': 'celebration'}, session)
    assert not removed['active'] and removed['duration_ms'] == 10000
    persistent = await registry.operate(effect(duration_ms=None), session)
    assert persistent['duration_ms'] is None and persistent['expires_at'] is None
    await store.initialize()
    assert (await store.components(session))[0]['version'] == persistent['version']
    with pytest.raises(UserError, match='persistent'):
        await registry.finish_component(session, 'celebration', persistent['version'])


async def test_reset_preserves_original_files_and_transient_version_metadata(config, store, engine):
    from athena.files import initialize_files
    from athena.store import now
    user, session, registry = await setup(config, store)
    await initialize_files(store)
    workspace = config.data_dir / 'files' / user / session
    workspace.mkdir(parents=True)
    original = workspace / 'notes.md'
    original.write_text('My original notes')
    await store.execute('INSERT INTO file_entries(id,user_id,session_id,name,path,size,media_type,source,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                        ('notes', user, session, 'notes.md', 'notes.md', original.stat().st_size, 'text/markdown', 'upload', now()))
    await registry.operate(effect(), session)
    await registry.operate({'kind': 'component', 'action': 'register', 'id': 'document', 'file_id': 'notes', 'placement': 'desktop'}, session)
    await engine.models.ensure_default()
    await engine.reset_user(user)
    assert not await store.components(session)
    assert original.read_text() == 'My original notes'
    assert len(await store.rows('SELECT * FROM file_entries')) == 1
    ended = (await store.rows("SELECT * FROM components WHERE id='celebration' ORDER BY version DESC LIMIT 1"))[0]
    assert not ended['active'] and ended['duration_ms'] == 10000 and ended['expires_at']
    file_component = (await store.rows("SELECT * FROM components WHERE id='document' ORDER BY version DESC LIMIT 1"))[0]
    assert not file_component['active'] and file_component['file_id'] == 'notes'
