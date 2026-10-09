"""Restart continuity must retain intentions without ever admitting model work."""
import asyncio
import json

import httpx
import pytest
from fastapi import HTTPException

from athena.api import create_app
from athena.auth import Auth
from athena.desktop_resume import Claim, Save, claim, save
from athena.files import initialize_files
from athena.store import now


def key(kind, owner, session=None):
    return 'athena-session:' + owner if kind == 'selection' else kind + ':' + json.dumps([owner, session], separators=(',', ':'))


async def setup(store):
    await initialize_files(store)
    user, _ = await Auth(store).register('resume-owner', 'synthetic-test-password')
    return user['id'], await store.session(user_id=user['id'])


async def write(store, owner, writer, sequence, changes):
    return await store.call(lambda db: save(db, owner, Save(writer=writer, sequence=sequence, changes=changes)))


async def test_new_window_fences_old_writes_and_lost_save_response_retries_identically(store):
    owner, sid = await setup(store)
    await store.call(lambda db: claim(db, owner, 'old'))
    changes = {key('selection', owner): sid}
    first = await write(store, owner, 'old', 1, changes)
    assert await write(store, owner, 'old', 1, changes) == first
    for seq, other in [(1, {key('selection', owner): ''}), (3, changes)]:
        with pytest.raises(HTTPException) as exc:
            await write(store, owner, 'old', seq, other)
        assert exc.value.status_code == 409
    newest = await store.call(lambda db: claim(db, owner, 'new'))
    assert newest['entries'] == changes and newest['sequence'] == 0
    await write(store, owner, 'new', 1, {key('selection', owner): ''})
    with pytest.raises(HTTPException) as exc:
        await write(store, owner, 'old', 2, changes)
    assert exc.value.status_code == 409
    retry_claim = await store.call(lambda db: claim(db, owner, 'new'))
    assert retry_claim['sequence'] == 1 and retry_claim['entries'][key('selection', owner)] == ''


async def test_concurrent_sequence_race_has_one_winner_without_partial_updates(store):
    owner, sid = await setup(store)
    await store.call(lambda db: claim(db, owner, 'client'))
    result = await asyncio.gather(*(write(store, owner, 'client', 1, {key('selection', owner): value}) for value in [sid, '']), return_exceptions=True)
    assert sum(isinstance(item, HTTPException) for item in result) == 1
    row = (await store.rows('SELECT sequence,entries FROM desktop_resume'))[0]
    assert row['sequence'] == 1
    assert len(json.loads(row['entries'])) == 1


async def test_owned_sessions_files_and_key_names_are_required(store):
    owner, sid = await setup(store)
    other, _ = await Auth(store).register('other-owner', 'synthetic-test-password')
    foreign = await store.session(user_id=other['id'])
    await store.call(lambda db: claim(db, owner, 'client'))
    for changes in ({key('selection', owner): foreign}, {key('athena-draft', owner, foreign): '{}'},
                    {key('selection', other['id']): foreign}, {'provider-secret': 'no'}, {'athena-draft:invalid-json': '{}'}):
        with pytest.raises(HTTPException):
            await write(store, owner, 'client', 1, changes)
    assert (await store.rows('SELECT sequence FROM desktop_resume'))[0]['sequence'] == 0
    await store.execute('''INSERT INTO file_entries(id,user_id,session_id,name,path,size,media_type,source,created_at)
        VALUES('owned',?,?,'notes.txt','uploads/notes.txt',1,'text/plain','upload',?)''', (owner, sid, now()))
    raw = json.dumps({'text': 'My private draft', 'revision': 1, 'attached': [{'id': 'owned', 'content_url': 'https://malicious.example/'}]})
    await write(store, owner, 'client', 1, {key('athena-draft', owner, sid): raw})
    read = await store.call(lambda db: claim(db, owner, 'restart'))
    restored = json.loads(read['entries'][key('athena-draft', owner, sid)])
    assert restored['attached'][0]['content_url'] == '/api/files/owned/content'
    assert restored['attached'][0]['name'] == 'notes.txt'
    stranger = await store.call(lambda db: claim(db, other['id'], 'other-client'))
    assert stranger['entries'] == {}


async def test_pending_send_stop_survive_claim_without_automatic_replay(store):
    owner, sid = await setup(store)
    await store.call(lambda db: claim(db, owner, 'client'))
    send = {'id': 'operation-1', 'owner': owner, 'session': sid, 'text': 'Do this once', 'mode': 'queue', 'file_ids': [], 'revision': 0, 'draft': None}
    stop = {'id': 'stop-1', 'owner': owner, 'session': sid}
    changes = {key('athena-submissions', owner, sid): json.dumps([send]), key('stops:athena-submissions', owner, sid): json.dumps([stop])}
    await write(store, owner, 'client', 1, changes)
    restored = await store.call(lambda db: claim(db, owner, 'new-client'))
    assert json.loads(restored['entries'][key('athena-submissions', owner, sid)]) == [send]
    assert json.loads(restored['entries'][key('stops:athena-submissions', owner, sid)]) == [stop]
    for table in ('runs', 'messages', 'requests'):
        assert await store.rows('SELECT * FROM ' + table) == []


async def test_same_database_new_process_and_origin_restores_explicit_blank_and_draft(config):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:51111') as client:
            response = await client.post('/api/auth/register', json={'username': 'restart-owner', 'password': 'synthetic-test-password'})
            assert response.status_code == 200
            owner = response.json()['user']['id']
            cookies = client.cookies
            assert (await client.post('/api/desktop/resume/claim', json={'writer': 'first-origin'})).status_code == 200
            changes = {key('selection', owner): '', key('athena-draft', owner): json.dumps({'text': 'Remember this unsent text', 'attached': [], 'revision': 7})}
            result = await client.post('/api/desktop/resume/save', json={'writer': 'first-origin', 'sequence': 1, 'changes': changes})
            assert result.status_code == 200
    restarted = create_app(config)
    async with restarted.router.lifespan_context(restarted):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(restarted), base_url='http://127.0.0.1:52222', cookies=cookies) as client:
            result = await client.post('/api/desktop/resume/claim', json={'writer': 'second-origin'})
            assert result.status_code == 200
            assert result.json()['entries'][key('selection', owner)] == ''
            assert json.loads(result.json()['entries'][key('athena-draft', owner)])['text'] == 'Remember this unsent text'
            assert await restarted.state.store.rows('SELECT * FROM runs') == []
            client.cookies.clear()
            assert (await client.post('/api/desktop/resume/claim', json={'writer': 'unauthenticated'})).status_code == 401


async def test_completed_empty_drafts_do_not_exhaust_retention_and_uncertain_revision_is_kept(store):
    owner, sid = await setup(store)
    await store.call(lambda db: claim(db, owner, 'client'))
    original = key('athena-draft', owner, sid)
    await write(store, owner, 'client', 1, {original: json.dumps({'text': 'Keep my real unfinished draft', 'revision': 1, 'attached': []})})
    for sequence in range(2, 282):
        session = await store.session(user_id=owner)
        await write(store, owner, 'client', sequence, {
            key('athena-draft', owner, session): json.dumps({'text': '', 'revision': 3, 'attached': []}),
            key('athena-submissions', owner, session): '[]',
            key('stops:athena-submissions', owner, session): '[]',
            key('selection', owner): session,
        })
    entries = json.loads((await store.rows('SELECT entries FROM desktop_resume'))[0]['entries'])
    assert len(entries) == 2 and json.loads(entries[original])['text'] == 'Keep my real unfinished draft'
    pending = {'id': 'uncertain', 'owner': owner, 'session': sid, 'text': 'old', 'mode': 'queue', 'file_ids': [], 'revision': 0, 'draft': None}
    await write(store, owner, 'client', 282, {original: json.dumps({'text': '', 'revision': 8, 'attached': []}), key('athena-submissions', owner, sid): json.dumps([pending])})
    entries = (await store.call(lambda db: claim(db, owner, 'restart')))['entries']
    assert json.loads(entries[original])['revision'] == 8
    await write(store, owner, 'restart', 1, {key('athena-submissions', owner, sid): '[]'})
    assert original not in json.loads((await store.rows('SELECT entries FROM desktop_resume'))[0]['entries'])
