"""Task origins are durable facts, not guesses based on user-visible text."""
import asyncio
import json
import sqlite3

import httpx
import pytest

from athena.api import create_app, public_message
from athena.auth import Auth
from athena.store import now
from athena.task_events import enqueue_task, task_origin
from athena.trace import run_trace


async def schedule(engine, session, **fields):
    return await engine.tools.runtime({'op': 'schedule', 'title': 'Water the plants',
        'prompt': 'Remind me to water the plants.', 'due_at': now(), **fields}, session, None)


async def test_timer_origin_is_committed_once_and_does_not_name_the_conversation(engine, store, monkeypatch):
    session = await store.session()
    monkeypatch.setattr(engine, 'kick', lambda session_id: None)
    task = await schedule(engine, session, interval_seconds=60)
    await asyncio.gather(engine.tick(), engine.tick())
    wakes = await store.rows('SELECT * FROM wakes')
    assert len(wakes) == 1
    message = (await store.history(session))[0]
    expected = {'kind': 'task', 'trigger': 'time', 'task_id': task['task_id'],
                'title': 'Water the plants', 'occurrence': 1}
    assert public_message(message)['origin'] == expected
    assert task_origin(message['origin']) == expected
    assert (await run_trace(store, session, wakes[0]['run_id']))['entries'][0]['origin'] == expected
    assert (await store.rows('SELECT title FROM sessions WHERE id=?', (session,)))[0]['title'] == '新的事情'
    # An actual later first user message still sets the conversation's title.
    await store.enqueue(session, 'Help with my garden')
    assert (await store.rows('SELECT title FROM sessions WHERE id=?', (session,)))[0]['title'] == 'Help with my garden'
    assert not await store.rows('SELECT * FROM requests')


async def test_condition_trigger_retains_observation_without_calling_model_while_waiting(engine, store, monkeypatch):
    session = await store.session()
    monkeypatch.setattr(engine, 'kick', lambda session_id: None)
    triggered = False
    async def check(*args):
        return {'exit_code': 0, 'error': None, 'artifact_id': None,
                'output': json.dumps({'trigger': triggered, 'context': {'evidence': 'A document appeared.'}})}
    monkeypatch.setattr(engine.tools, 'shell', check)
    task = await schedule(engine, session, condition_command='check-the-document', interval_seconds=60)
    await engine.tick()
    await asyncio.gather(*engine.watchers.values())
    assert not await store.history(session)
    triggered = True
    await store.execute('UPDATE tasks SET due_at=? WHERE id=?', (now(), task['task_id']))
    await engine.tick()
    await asyncio.gather(*engine.watchers.values())
    await engine.tick()
    message = (await store.history(session))[0]
    assert task_origin(message['origin'])['trigger'] == 'condition'
    assert 'A document appeared.' in message['content']
    assert '不是新的用户指令' in message['content']
    assert len(await store.rows('SELECT * FROM wakes')) == 1
    assert not await store.rows('SELECT * FROM requests')


async def test_duplicate_occurrence_rolls_back_its_run_message_and_source(engine, store):
    session = await store.session()
    task = await schedule(engine, session)
    row = (await store.rows('SELECT * FROM tasks WHERE id=?', (task['task_id'],)))[0]
    await store.call(lambda db: enqueue_task(db, row))
    before = await store.history(session)
    with pytest.raises(sqlite3.IntegrityError):
        await store.call(lambda db: enqueue_task(db, row))
    assert await store.history(session) == before
    assert len(await store.rows('SELECT * FROM runs')) == 1
    assert len(await store.rows('SELECT * FROM wakes')) == 1


@pytest.mark.parametrize('condition', [False, True])
async def test_legacy_backfill_uses_recorded_wakes_and_preserves_protocol_and_historical_title(engine, store, condition):
    session = await store.session()
    task = await schedule(engine, session)
    row = (await store.rows('SELECT * FROM tasks WHERE id=?', (task['task_id'],)))[0]
    run, _ = await store.call(lambda db: enqueue_task(db, row, condition_context='original evidence' if condition else None))
    await store.execute('UPDATE messages SET origin=NULL WHERE run_id=?', (run,))
    initial = (await store.history(session))[0]
    # Identical user-authored text and later task renames are not trusted provenance.
    ordinary = await store.enqueue(session, initial['content'])
    await store.execute('UPDATE tasks SET title=? WHERE id=?', ('New unrelated task title', task['task_id']))
    context = await engine.context(session)
    originals = await store.history(session)
    await store.execute('PRAGMA user_version=10')
    await store.initialize()
    annotated = await store.history(session)
    assert task_origin(annotated[0]['origin']) == {'kind': 'task', 'trigger': 'condition' if condition else 'time',
        'task_id': task['task_id'], 'title': 'Water the plants', 'occurrence': 1}
    assert annotated[1]['run_id'] == ordinary and annotated[1]['origin'] is None
    assert [{k: v for k, v in item.items() if k != 'origin'} for item in annotated] == [{k: v for k, v in item.items() if k != 'origin'} for item in originals]
    assert await engine.context(session) == context
    await store.initialize()
    assert await store.history(session) == annotated


async def test_unknown_legacy_input_is_not_relabelled_and_forks_do_not_duplicate_jobs(engine, store):
    session = await store.session()
    task = await schedule(engine, session, interval_seconds=60)
    row = (await store.rows('SELECT * FROM tasks WHERE id=?', (task['task_id'],)))[0]
    run, _ = await store.call(lambda db: enqueue_task(db, row))
    await store.execute("UPDATE messages SET origin=NULL,content='Unrecognized legacy input' WHERE run_id=?", (run,))
    await store.execute('PRAGMA user_version=10')
    await store.initialize()
    assert (await store.history(session))[0]['origin'] is None
    # A verified origin is inherited with the history, not as another active job.
    await store.execute('UPDATE tasks SET occurrence=1 WHERE id=?', (task['task_id'],))
    row['occurrence'] = 1
    second, _ = await store.call(lambda db: enqueue_task(db, row))
    await store.execute("UPDATE runs SET status='complete'")
    await store.execute("UPDATE messages SET status='complete'")
    inherited = (await store.history(session))[-1]
    fork = await store.fork(session)
    assert (await store.history(fork))[-1] == inherited
    assert task_origin((await store.history(fork))[-1]['origin'])['occurrence'] == 2
    assert (await store.rows('SELECT status FROM tasks WHERE session_id=?', (fork,))) == [{'status': 'paused'}]
    assert (await run_trace(store, fork, second))['entries'][0]['origin']['title'] == 'Water the plants'
    assert not await store.rows('SELECT 1 FROM wakes w JOIN runs r ON r.id=w.run_id WHERE r.session_id=?', (fork,))


async def test_authenticated_api_exposes_sources_without_accepting_spoofed_input(config, monkeypatch):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        monkeypatch.setattr(app.state.engine, 'kick', lambda session_id: None)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317') as client:
            await client.post('/api/auth/register', json={'username': 'task-owner', 'password': 'synthetic-password'})
            session = (await client.post('/api/sessions', json={})).json()['id']
            await schedule(app.state.engine, session)
            await app.state.engine.tick()
            snapshot = (await client.get(f'/api/sessions/{session}')).json()
            source = snapshot['messages'][0]['origin']
            assert source['title'] == 'Water the plants'
            response = await client.post(f'/api/sessions/{session}/messages', json={'text': 'I typed this myself.', 'origin': source})
            assert response.status_code == 200
            later = (await client.get(f'/api/sessions/{session}')).json()
            assert 'origin' not in later['messages'][-1]
            trace = (await client.get(f'/api/sessions/{session}/runs/{snapshot["runs"][0]["id"]}/trace')).json()
            assert trace['entries'][0]['origin'] == source
            await client.post('/api/auth/logout', json={})
            await client.post('/api/auth/register', json={'username': 'other-task-owner', 'password': 'synthetic-password'})
            assert (await client.get(f'/api/sessions/{session}')).status_code == 404


async def test_scheduled_reminder_still_produces_a_visible_answer(engine, store, monkeypatch):
    async def provider(client, base, key, data, on_text, on_reasoning):
        await on_text('Time to water the plants.')
        return {'message': {'content': 'Time to water the plants.', 'reasoning_content': ''}, 'usage': {}}
    monkeypatch.setattr('athena.engine.complete', provider)
    session = await store.session()
    await schedule(engine, session)
    await engine.tick()
    await asyncio.wait_for(asyncio.gather(*engine.actors.values()), 3)
    messages = [public_message(row) for row in await store.history(session)]
    assert messages[0]['origin']['kind'] == 'task'
    assert messages[-1]['content'] == 'Time to water the plants.' and 'origin' not in messages[-1]
    assert (await store.rows('SELECT status FROM runs')) == [{'status': 'complete'}]


async def test_agent_can_inspect_task_provenance_through_the_universal_tool(engine, store):
    user, _ = await Auth(store).register('source-reader', 'synthetic-password')
    current, other = await store.session(user_id=user['id']), await store.session(user_id=user['id'])
    task = await schedule(engine, other)
    row = (await store.rows('SELECT * FROM tasks WHERE id=?', (task['task_id'],)))[0]
    await store.call(lambda db: enqueue_task(db, row))
    message = (await store.history(other))[0]
    read = await engine.tools.execute('runtime', {'op': 'sessions', 'args': {'action': 'read', 'session_id': other, 'offset': 5, 'limit': 20}}, current, None)
    assert read['messages'][0]['origin'] == task_origin(message['origin'])
    original = await engine.tools.runtime({'op': 'read_message', 'session_id': other, 'message_id': message['id']}, current, None)
    assert original['origin'] == read['messages'][0]['origin']
    assert original['content'] == message['content']
