"""Real files are registry sources, with durable delivery and native receipts."""
import asyncio
import json

import httpx
import pytest

from athena.api import create_app
from athena.app_controls import app_operation
from athena.auth import Auth
from athena.component_views import update_view
from athena.errors import UserError
from athena.files import initialize_files
from athena.registry import Registry
from athena.store import Store, now
from frontend_fixtures import observe_client
from test_desktop_surfaces import native, receipt


@pytest.fixture
async def file_surface(config, store):
    await initialize_files(store)
    user, token = await Auth(store).register('file-surface-owner', 'test-password')
    session = await store.session(user_id=user['id'])
    await store.execute('INSERT INTO file_entries(id,user_id,session_id,name,path,size,media_type,source,created_at,content_revision) VALUES (?,?,?,?,?,?,?,?,?,?)',
                        ('report', user['id'], session, 'Budget.xlsx', 'Budget.xlsx', 250, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'output', now(), 's1-revision'))
    return user['id'], session, token, Registry(config, store)


async def test_file_delivery_is_concurrently_idempotent_and_has_one_external_manifest(config, store, file_surface):
    _, session, _, registry = file_surface
    results = await asyncio.gather(*(registry.present_file(session, 'report', reveal=False) for _ in range(8)))
    assert sum(result['created'] for result in results) == 1
    assert sum(result['changed'] for result in results) == 1
    current = (await store.components(session))[0]
    assert current['id'] == 'athena_file_report' and current['version'] == 1 and current['active']
    assert current['html'] == '' and current['file_id'] == current['file']['id'] == 'report'
    assert current['file']['content_revision'] == 's1-revision'
    assert current['file']['content_url'] == '/api/files/report/content'
    assert 'user_id' not in current['file'] and 'workspace_path' not in current['file']
    assert current['view'] == {'width': 860, 'height': 560, 'x': 0.05, 'y': 0.1, 'hidden': False}
    assert current['placement'] == 'desktop' and current['view_revision'] == 1
    manifests = list((config.data_dir / 'extensions').rglob('*.json'))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text()) == {'kind': 'file', 'file_id': 'report'}
    assert len(await store.rows('SELECT * FROM events WHERE session_id=?', (session,))) == 2
    await store.initialize()
    assert (await store.components(session))[0] == current
    # Content freshness is canonical, not a stale descriptor copied into HTML.
    await store.execute("UPDATE file_entries SET content_revision='s1-new',size=300 WHERE id='report'")
    assert (await store.components(session))[0]['file']['content_revision'] == 's1-new'
    passive = await registry.present_file(session, 'report', reveal=False)
    assert not passive['changed'] and passive['component']['file']['size'] == 300


async def test_default_delivery_reuses_agent_file_view_concurrently_without_changing_it(config, store, file_surface):
    _, session, _, registry = file_surface
    await registry.operate({'kind': 'component', 'action': 'register', 'id': 'quote_compare',
                            'file_id': 'report', 'title': 'Compare these quotes', 'placement': 'desktop'}, session)
    await update_view(store, session, 'quote_compare', 1, 0, {'width': 920, 'height': 620, 'x': 0.4, 'y': 0.2})
    before = (await store.components(session))[0]
    event_count = len(await store.rows('SELECT * FROM events'))
    results = await asyncio.gather(*(registry.present_file(session, 'report', reveal=False) for _ in range(8)))
    assert all(not item['created'] and not item['changed'] and item['component'] == before for item in results)
    assert await store.components(session) == [before]
    assert len(await store.rows('SELECT * FROM events')) == event_count
    assert len(list((config.data_dir / 'extensions').rglob('*.json'))) == 1
    await store.execute("UPDATE file_entries SET content_revision='s1-latest',size=321 WHERE id='report'")
    clicked = await registry.present_file(session, 'report', reveal=True)
    assert clicked['component']['id'] == 'quote_compare' and not clicked['changed']
    assert clicked['component']['file']['content_revision'] == 's1-latest'
    assert clicked['component']['file']['size'] == 321
    assert clicked['component']['title'] == before['title'] and clicked['component']['view'] == before['view']


async def test_file_delivery_serializes_selection_across_database_connections(config, store, file_surface):
    _, session, _, registry = file_surface
    other_store = Store(store.path)
    try:
        other_registry = Registry(config, other_store)
        results = await asyncio.gather(*(client.present_file(session, 'report', reveal=False)
                                         for client in (registry, other_registry) * 4))
        assert sum(item['created'] for item in results) == 1
        assert len(await store.components(session)) == 1
        assert len(await store.rows('SELECT * FROM components WHERE session_id=?', (session,))) == 1
    finally:
        await other_store.close()


@pytest.mark.parametrize('closed', ['hidden', 'removed', 'reset'])
async def test_default_delivery_respects_closed_custom_file_views_and_explicit_reopen(store, engine, file_surface, closed):
    user, session, _, registry = file_surface
    await registry.operate({'kind': 'component', 'action': 'register', 'id': 'custom_file',
                            'file_id': 'report', 'placement': 'desktop'}, session)
    await update_view(store, session, 'custom_file', 1, 0, {'width': 950, 'hidden': True})
    if closed == 'removed':
        await registry.operate({'kind': 'component', 'action': 'remove', 'id': 'custom_file'}, session)
    elif closed == 'reset':
        await engine.models.ensure_default()
        await engine.reset_user(user)
    for _ in range(2):
        passive = await registry.present_file(session, 'report', reveal=False)
        assert passive['component']['id'] == 'custom_file'
        assert not passive['created'] and not passive['changed']
        assert passive['component']['view'].get('hidden') or not passive['component']['active']
    assert not await store.rows("SELECT * FROM components WHERE session_id=? AND id='athena_file_report'", (session,))
    clicked = await registry.present_file(session, 'report', reveal=True)
    assert clicked['component']['id'] == 'custom_file' and clicked['component']['active']
    assert not clicked['component']['view']['hidden']
    assert clicked['component']['view']['width'] == (860 if closed == 'reset' else 950)
    assert len(await store.components(session)) == 1


async def test_multiple_explicit_file_views_remain_valid_with_deterministic_default_selection(store, file_surface):
    _, session, _, registry = file_surface
    canonical = (await registry.present_file(session, 'report', reveal=False))['component']
    for name in ('other_a', 'other_b'):
        await registry.operate({'kind': 'component', 'action': 'register', 'id': name,
                                'file_id': 'report', 'placement': 'desktop'}, session)
    assert len(await store.components(session)) == 3
    assert (await registry.present_file(session, 'report', reveal=False))['component']['id'] == canonical['id']
    await registry.operate({'kind': 'component', 'action': 'remove', 'id': canonical['id']}, session)
    assert (await registry.present_file(session, 'report', reveal=False))['component']['id'] == 'other_b'
    await update_view(store, session, 'other_b', 1, 0, {'hidden': True})
    assert (await registry.present_file(session, 'report', reveal=False))['component']['id'] == 'other_a'
    await update_view(store, session, 'other_a', 1, 0, {'hidden': True})
    assert (await registry.present_file(session, 'report', reveal=False))['component']['id'] == 'other_b'
    clicked = await registry.present_file(session, 'report', reveal=True)
    assert clicked['component']['id'] == 'other_b' and not clicked['component']['view']['hidden']
    assert len(await store.components(session)) == 2


async def test_file_delivery_uses_current_native_source_and_never_other_sessions_layout(store, file_surface):
    user, session, _, registry = file_surface
    for name, placement in (('inline_file', 'inline'), ('moved_file', 'desktop'), ('replaced_file', 'desktop')):
        await registry.operate({'kind': 'component', 'action': 'register', 'id': name,
                                'file_id': 'report', 'placement': placement}, session)
    await update_view(store, session, 'moved_file', 1, 0, {'placement': 'inline'})
    await registry.operate({'kind': 'component', 'action': 'register', 'id': 'replaced_file', 'source': '<p>Now HTML</p>'}, session)
    other = await store.session(user_id=user)
    await registry.operate({'kind': 'component', 'action': 'register', 'id': 'elsewhere', 'file_id': 'report', 'placement': 'desktop'}, other)
    presented = await registry.present_file(session, 'report', reveal=False)
    assert presented['created'] and presented['component']['id'] == 'athena_file_report'
    # A current layout override to desktop is reusable even if registration was inline.
    third = await store.session(user_id=user)
    await registry.operate({'kind': 'component', 'action': 'register', 'id': 'promoted', 'file_id': 'report', 'placement': 'inline'}, third)
    await update_view(store, third, 'promoted', 1, 0, {'placement': 'desktop'})
    selected = await registry.present_file(third, 'report', reveal=True)
    assert not selected['created'] and selected['component']['id'] == 'promoted'
    with pytest.raises(UserError):
        await registry.present_file(third, 'report', reveal=False)


async def test_fork_keeps_custom_file_identity_and_closed_choice(store, file_surface):
    _, session, _, registry = file_surface
    await registry.operate({'kind': 'component', 'action': 'register', 'id': 'custom_file', 'file_id': 'report', 'placement': 'desktop'}, session)
    await update_view(store, session, 'custom_file', 1, 0, {'hidden': True})
    fork = await store.fork(session)
    passive = await registry.present_file(fork, 'report', reveal=False)
    assert not passive['created'] and not passive['changed'] and passive['component']['id'] == 'custom_file'
    assert passive['component']['view']['hidden']
    clicked = await registry.present_file(fork, 'report', reveal=True)
    assert clicked['component']['id'] == 'custom_file' and not clicked['component']['view']['hidden']
    assert (await store.components(session))[0]['view']['hidden']


async def test_transient_file_delivery_is_observed_without_becoming_persistent(store, file_surface):
    _, session, _, registry = file_surface
    await registry.operate({'kind': 'component', 'action': 'register', 'id': 'temporary',
                            'file_id': 'report', 'placement': 'desktop', 'duration_ms': 60000}, session)
    active = await registry.present_file(session, 'report', reveal=False)
    assert not active['created'] and not active['changed'] and active['component']['id'] == 'temporary'
    assert active['status'] == 'transient'
    await registry.finish_component(session, 'temporary', 1)
    finished = await registry.present_file(session, 'report', reveal=False)
    assert not finished['created'] and not finished['changed'] and not finished['component']['active']
    explicit = await registry.present_file(session, 'report', reveal=True)
    assert explicit['created'] and explicit['component']['id'] == 'athena_file_report'
    assert explicit['component']['duration_ms'] is None
    assert len(await store.components(session)) == 1


async def test_close_remove_and_reset_are_not_undone_by_passive_delivery(config, store, engine, file_surface):
    user, session, _, registry = file_surface
    first = (await registry.present_file(session, 'report', reveal=False))['component']
    await update_view(store, session, first['id'], 1, 1, {'hidden': True, 'width': 900})
    events = len(await store.rows('SELECT * FROM events'))
    for _ in range(3):
        result = await registry.present_file(session, 'report', reveal=False)
        assert not result['changed'] and result['component']['view']['hidden']
    assert len(await store.rows('SELECT * FROM events')) == events
    reopened = (await registry.present_file(session, 'report', reveal=True))['component']
    assert not reopened['view']['hidden'] and reopened['view']['width'] == 900
    assert reopened['view_revision'] == 3 and reopened['version'] == 1
    assert not (await registry.present_file(session, 'report', reveal=True))['changed']
    await registry.operate({'kind': 'component', 'action': 'remove', 'id': first['id']}, session)
    passive = await registry.present_file(session, 'report', reveal=False)
    assert not passive['changed'] and not passive['component']['active'] and passive['component']['version'] == 2
    restored = (await registry.present_file(session, 'report', reveal=True))['component']
    assert restored['active'] and restored['version'] == 3 and restored['file_id'] == 'report'
    await engine.models.ensure_default()
    await engine.reset_user(user)
    assert not await store.components(session)
    passive = await registry.present_file(session, 'report', reveal=False)
    assert not passive['component']['active'] and passive['component']['file_id'] == 'report'
    restored = (await registry.present_file(session, 'report', reveal=True))['component']
    assert restored['version'] == 5 and restored['file_id'] == 'report'
    assert restored['view']['width'] == 860 and not restored['view']['hidden']
    assert (await store.rows("SELECT * FROM file_entries WHERE id='report'"))[0]['size'] == 250


async def test_fork_preserves_file_versions_and_independent_hidden_state(store, file_surface):
    _, session, _, registry = file_surface
    first = (await registry.present_file(session, 'report', reveal=False))['component']
    await update_view(store, session, first['id'], 1, 1, {'hidden': True})
    fork = await store.fork(session)
    inherited = (await registry.present_file(fork, 'report', reveal=False))['component']
    assert inherited['file_id'] == 'report' and inherited['view']['hidden']
    assert not (await registry.present_file(fork, 'report', reveal=True))['component']['view']['hidden']
    assert (await store.components(session))[0]['view']['hidden']
    await registry.operate({'kind': 'component', 'action': 'remove', 'id': first['id']}, fork)
    assert await store.components(session) and not await store.components(fork)
    assert len(await store.rows('SELECT * FROM file_entries')) == 1


async def test_registry_file_source_can_version_switch_and_restore_without_losing_file(store, file_surface):
    _, session, _, registry = file_surface
    def args(**change):
        return {'kind': 'component', 'action': 'register', 'id': 'budget', **change}
    initial = await registry.operate(args(file_id='report', placement='desktop'), session)
    assert initial['file']['id'] == 'report' and initial['render']['scope'] == 'file_preview_only'
    await registry.operate(args(source='<p>Other view</p>'), session)
    assert (await store.components(session))[0]['file_id'] is None
    restored = await registry.operate({'kind': 'component', 'action': 'restore', 'id': 'budget', 'version': 1}, session)
    assert restored['file_id'] == 'report' and restored['version'] == 3
    historical = await registry.operate({'kind': 'component', 'action': 'read', 'id': 'budget', 'version': 1}, session)
    assert historical['file_id'] == 'report' and historical['source'] == '' and not historical['enabled']
    listed = await registry.operate({'kind': 'component', 'action': 'list'}, session)
    assert [row['file_id'] for row in listed['items']] == ['report', None, 'report']
    with pytest.raises(ValueError, match='either'):
        await registry.operate(args(file_id='report', source='<p>ambiguous</p>'), session)
    with pytest.raises(ValueError, match='file-aware'):
        await registry.report_component(session, 'budget', 3, 'fake', 'mounted', True, True)


async def test_passive_delivery_cannot_select_another_conversation_but_explicit_selection_can(store, file_surface):
    user, session, _, registry = file_surface
    other = await store.session(user_id=user)
    with pytest.raises(UserError):
        await registry.present_file(other, 'report', reveal=False)
    explicit = await registry.present_file(other, 'report', reveal=True)
    assert explicit['component']['file']['session_id'] == session
    await registry.operate({'kind': 'component', 'action': 'register', 'id': 'explicit-file', 'file_id': 'report'}, other)
    stranger, _ = await Auth(store).register('other-account', 'test-password')
    foreign = await store.session(user_id=stranger['id'])
    for reveal in (True, False):
        with pytest.raises(UserError):
            await registry.present_file(foreign, 'report', reveal=reveal)
    with pytest.raises(UserError):
        await registry.operate({'kind': 'component', 'action': 'register', 'id': 'stolen', 'file_id': 'report'}, foreign)
    assert not await store.components(foreign)
    # Defense in depth against a corrupt registration never projects foreign data.
    await store.execute("INSERT INTO components(session_id,id,version,title,html,active,created_at,placement,file_id) VALUES (?,'corrupt',1,'', '',1,?,'desktop','report')", (foreign, now()))
    assert (await store.components(foreign))[0]['file'] is None


async def test_reserved_namespace_never_overwrites_an_existing_component(store, file_surface):
    _, session, _, registry = file_surface
    with pytest.raises(UserError):
        await registry.operate({'kind': 'component', 'action': 'register', 'id': 'athena_file_report', 'source': '<p>Hijack</p>'}, session)
    await store.execute("INSERT INTO components(session_id,id,version,title,html,active,created_at,placement) VALUES (?,'athena_file_report',1,'Existing','<p>Keep</p>',1,?,'inline')", (session, now()))
    with pytest.raises(UserError, match='not overwritten'):
        await registry.present_file(session, 'report', reveal=True)
    current = (await store.components(session))[0]
    assert current['html'] == '<p>Keep</p>' and current['version'] == 1


async def test_native_file_receipt_is_bound_to_file_revision_and_not_html_load(store, file_surface):
    user, session, token, registry = file_surface
    current = (await registry.present_file(session, 'report', reveal=False))['component']
    valid = receipt(id=current['id'], view_revision=1, file_id='report', content_revision='s1-revision')
    for fields in ({'file_id': None}, {'file_id': 'other'}, {'content_revision': 'old'}, {'content_revision': None}):
        with pytest.raises(UserError):
            await observe_client(store, user, session, token, desktop=native(components=[{**valid, **fields}]))
    await observe_client(store, user, session, token, desktop=native(components=[valid]))
    snapshot = await app_operation(store, session, None, {'action': 'snapshot', 'client_id': 'browser-one'})
    assert snapshot['desktop_registration'][0]['content_revision'] == 's1-revision'
    assert snapshot['state']['desktop']['components'][0]['file_id'] == 'report'
    failed = {**valid, 'state': 'failed', 'error_code': 'preview_unavailable'}
    await observe_client(store, user, session, token, revision=2, desktop=native(components=[failed]))
    snapshot = await app_operation(store, session, None, {'action': 'snapshot', 'client_id': 'browser-one'})
    assert snapshot['state']['desktop']['components'][0]['state'] == 'failed'
    read = await registry.operate({'kind': 'component', 'action': 'read', 'id': current['id']}, session)
    assert read['render']['state'] == 'unobserved' and read['render']['scope'] == 'file_preview_only'


async def test_presentation_route_authentication_snapshot_and_commit_before_wakeup(config):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317') as client:
            await client.post('/api/auth/register', json={'username': 'files-api', 'password': 'test-password'})
            sid = (await client.post('/api/sessions', json={})).json()['id']
            file = (await client.post(f'/api/sessions/{sid}/files', params={'name': 'report.md'}, content=b'# Report')).json()
            endpoint = f'/api/sessions/{sid}/files/{file["id"]}/presentation'
            assert (await client.post(endpoint, json={'reveal': 'yes'})).status_code == 422
            queue = asyncio.Queue()
            app.state.engine.listeners[sid] = {queue}
            first = await client.post(endpoint, json={'reveal': False})
            assert first.status_code == 200
            assert first.json()['created']
            assert (await queue.get())['type'] == 'refresh'
            snapshot = (await client.get(f'/api/sessions/{sid}')).json()
            assert snapshot['components'][0]['file'] == file
            user = (await client.get('/api/auth/me')).json()['user']['id']
            (config.data_dir / 'files' / user / sid / file['path']).write_bytes(b'# Updated report')
            refreshed = (await client.get(f'/api/sessions/{sid}')).json()
            assert refreshed['components'][0]['file']['content_revision'] == refreshed['files'][0]['content_revision']
            assert refreshed['components'][0]['file']['content_revision'] != file['content_revision']
            assert (await client.post(endpoint, json={'reveal': False})).json()['changed'] is False
            assert queue.empty()
            await client.post('/api/auth/logout')
            assert (await client.post(endpoint, json={'reveal': True})).status_code == 401
            await client.post('/api/auth/register', json={'username': 'outsider', 'password': 'test-password'})
            assert (await client.post(endpoint, json={'reveal': True})).status_code == 404


async def test_additive_file_source_migration_preserves_existing_html_versions(store, file_surface):
    _, session, _, registry = file_surface
    await registry.operate({'kind': 'component', 'action': 'register', 'id': 'old-html', 'source': '<p>Preserve me</p>'}, session)
    await store.execute('ALTER TABLE components DROP COLUMN file_id')
    await store.execute('PRAGMA user_version=15')
    await store.initialize()
    row = (await store.components(session))[0]
    assert row['id'] == 'old-html' and row['html'] == '<p>Preserve me</p>'
    assert row['file_id'] is None and row['file'] is None and row['version'] == 1


async def test_lost_delivery_reply_never_deletes_committed_manifest(config, store, file_surface, monkeypatch):
    _, session, _, registry = file_surface
    original_call = store.call
    committed = asyncio.Event()
    async def lose_reply(operation):
        result = await original_call(operation)
        if operation.__name__ == 'present':
            committed.set()
            await asyncio.Event().wait()
        return result
    monkeypatch.setattr(store, 'call', lose_reply)
    delivery = asyncio.create_task(registry.present_file(session, 'report', reveal=False))
    await committed.wait()
    delivery.cancel()
    with pytest.raises(asyncio.CancelledError):
        await delivery
    monkeypatch.setattr(store, 'call', original_call)
    row = (await store.components(session))[0]
    assert (config.data_dir / row['file_path']).is_file()
    retry = await registry.present_file(session, 'report', reveal=False)
    assert not retry['changed'] and retry['component']['version'] == 1


async def test_legacy_file_revision_is_indexed_before_presentation_and_missing_file_fails(config, store, file_surface):
    user, session, _, registry = file_surface
    workspace = config.data_dir / 'files' / user / session
    workspace.mkdir(parents=True)
    (workspace / 'Budget.xlsx').write_bytes(b'legacy data')
    await store.execute("UPDATE file_entries SET content_revision=NULL WHERE id='report'")
    first = await registry.present_file(session, 'report', reveal=False)
    assert first['component']['file']['content_revision'].startswith('s1-')
    assert first['component']['file']['size'] == len(b'legacy data')
    await store.execute("INSERT INTO file_entries(id,user_id,session_id,name,path,size,media_type,source,created_at) VALUES ('missing',?,?, 'missing.xlsx','missing.xlsx',12,'application/octet-stream','output',?)", (user, session, now()))
    with pytest.raises(UserError, match='no longer available'):
        await registry.present_file(session, 'missing', reveal=True)
    assert len(await store.components(session)) == 1
