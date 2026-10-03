"""Delegation acceptance through the runtime tool, durable state and real loop."""
import asyncio
import json
import threading

import pytest
import httpx

from athena.agents import activate_wait, branch, finish_or_wait, resume_waits
from athena.auth import Auth
from athena.engine import Engine
from athena.errors import UserError
from athena.store import Store
from athena.api import create_app


async def parent(store, username='delegator'):
    user, _ = await Auth(store).register(username, 'test-password')
    session = await store.session(user_id=user['id'])
    run = await store.enqueue(session, 'The parent has private task context.')
    await store.claim(session)
    return user, session, run


async def operation(engine, session, run, action, **args):
    return await engine.tools.execute('runtime', {'op': 'agents', 'args': {'action': action, **args}}, session, run)


async def spawn(engine, session, run, name='research', **kwargs):
    return await operation(engine, session, run, 'spawn', name=name, title=kwargs.pop('title', name),
                           prompt=kwargs.pop('prompt', name), **kwargs)


async def state(store, run):
    return (await store.rows('SELECT status FROM runs WHERE id=?', (run,)))[0]['status']


async def until(check, timeout=5):
    async with asyncio.timeout(timeout):
        while not await check():
            await asyncio.sleep(.01)


def tool(identifier, action, **args):
    return {'id': identifier, 'type': 'function', 'function': {'name': 'runtime',
        'arguments': json.dumps({'op': 'agents', 'args': {'action': action, **args}})}}


def response(content='', tools=None):
    message = {'role': 'assistant', 'content': content, 'reasoning_content': ''}
    if tools:
        message['tool_calls'] = tools
    return {'message': message, 'usage': {'prompt_tokens': 10, 'completion_tokens': 2}}


async def test_atomic_idempotent_spawn_isolated_context_and_profile(engine, store):
    user, session, run = await parent(store)
    inputs = dict(title='Flight facts', prompt='Find flight facts', context='Only this reference is shared.')
    jobs = await asyncio.gather(*(spawn(engine, session, run, **inputs) for _ in range(12)))
    assert sum(job['created'] for job in jobs) == 1
    job = jobs[0]['agent']
    assert len({item['agent']['id'] for item in jobs}) == 1
    child = (await store.rows('SELECT * FROM sessions WHERE id=?', (job['child_session_id'],)))[0]
    original = (await store.rows('SELECT * FROM sessions WHERE id=?', (session,)))[0]
    assert child['user_id'] == user['id'] and child['project_id'] == original['project_id']
    assert child['model_profile_id'] == original['model_profile_id'] and child['parent_id'] is None
    history = await store.history(child['id'])
    assert len(history) == 1 and 'Only this reference is shared.' in history[0]['content']
    assert 'The parent has private task context.' not in history[0]['content']
    assert json.loads(history[0]['origin'])['kind'] == 'agent_task'
    assert await store.history(session) == await store.context_history(session)
    assert 'test-provider-key' not in json.dumps(await store.rows('SELECT * FROM agent_jobs'))
    with pytest.raises(UserError):
        await spawn(engine, session, run, prompt='Different task')
    assert len(await store.rows('SELECT * FROM agent_jobs')) == 1


async def test_owned_operations_limits_and_results_remain_recoverable(engine, store):
    _, session, run = await parent(store)
    _, other, other_run = await parent(store, 'other-delegator')
    jobs = [(await spawn(engine, session, run, name=f'job-{i}'))['agent'] for i in range(4)]
    with pytest.raises(UserError) as blocked:
        await spawn(engine, session, run, name='fifth')
    assert blocked.value.code == 'agent_limit'
    assert (await spawn(engine, other, other_run, name='other-account'))['created']
    for action, args in [('read', {'agent_id': jobs[0]['id']}), ('cancel', {'agent_id': jobs[0]['id']}), ('wait', {'agent_ids': [jobs[0]['id']]})]:
        with pytest.raises(UserError):
            await operation(engine, other, other_run, action, **args)
    with pytest.raises(UserError):
        await spawn(engine, other, run)
    child = jobs[0]
    text = 'Result evidence ' * 2000
    message = await store.message(child['child_session_id'], child['child_run_id'], 'assistant', text)
    await store.execute("UPDATE runs SET status='complete' WHERE id=?", (child['child_run_id'],))
    one = await operation(engine, session, run, 'read', agent_id=child['id'], limit=1234)
    two = await operation(engine, session, run, 'read', agent_id=child['id'], offset=1234, limit=12000)
    assert one['message_id'] == message and one['total_chars'] == len(text)
    assert one['content'] + two['content'] == text[:13234]
    assert (await store.history(child['child_session_id']))[-1]['content'] == text
    assert (await spawn(engine, session, run, name='fifth'))['created']
    listing = await operation(engine, session, run, 'list', limit=2)
    assert listing['total'] == 5 and listing['has_more'] and len(listing['agents']) == 2


async def test_wait_completion_race_closes_tool_slots_and_delivers_once(engine, store):
    _, session, run = await parent(store)
    job = (await spawn(engine, session, run))['agent']
    calls = [tool('wait', 'wait', agent_ids=[job['id']]), tool('later', 'list')]
    await store.message(session, run, 'assistant', tool_calls=calls)
    waiting = await operation(engine, session, run, 'wait', agent_ids=[job['id']])
    await store.message(session, run, 'tool', json.dumps(waiting), tool_call_id='wait')
    await store.message(job['child_session_id'], job['child_run_id'], 'assistant', 'Verified evidence.')
    await store.execute("UPDATE runs SET status='complete' WHERE id=?", (job['child_run_id'],))
    await activate_wait(store, session, run, waiting['wait_id'], [calls[1]])
    assert await state(store, run) == 'queued'
    for _ in range(10):
        assert not await store.call(resume_waits)
    messages = await store.history(session)
    results = [m for m in messages if m['origin']]
    assert len(results) == 1 and json.loads(results[0]['origin'])['kind'] == 'agent_results'
    assert [m['tool_call_id'] for m in messages if m['role'] == 'tool'] == ['wait', 'later']
    assert json.loads(messages[-2]['content'])['status'] == 'not_executed'
    assert 'Verified evidence.' in results[0]['content']


async def test_parallel_work_releases_parent_and_resumes_without_replaying_actions(engine, store, monkeypatch):
    user, _ = await Auth(store).register('parallel-owner', 'test-password')
    session = await store.session(user_id=user['id'])
    release = asyncio.Event()
    child_started = set()
    parent_calls, child_calls, forbidden = [], [], []
    async def provider(client, base, key, payload, on_text, on_reasoning=None):
        first = payload['messages'][1]['content']
        if first.startswith('[Athena delegated subtask]'):
            name = 'alpha' if 'Task: alpha' in first else 'beta'
            child_started.add(name); child_calls.append(payload)
            await release.wait()
            await on_text(f'{name} evidence')
            return response(f'{name} evidence')
        parent_calls.append(payload)
        if len(parent_calls) == 1:
            return response(tools=[tool(name, 'spawn', name=name, title=name, prompt=name) for name in ('alpha', 'beta')])
        if len(parent_calls) == 2:
            jobs = [json.loads(m['content'])['agent']['id'] for m in payload['messages'] if m['role'] == 'tool']
            return response(tools=[tool('join', 'wait', agent_ids=jobs),
                {'id': 'later', 'type': 'function', 'function': {'name': 'exec', 'arguments': '{"command":"should-not-execute"}'}}])
        assert 'alpha evidence' in payload['messages'][-1]['content'] and 'beta evidence' in payload['messages'][-1]['content']
        return response('Combined verified results.')
    async def shell(*args, **kwargs):
        forbidden.append(args)
        return {}
    monkeypatch.setattr('athena.engine.complete', provider)
    monkeypatch.setattr(engine.tools, 'shell', shell)
    run = await engine.submit(session, 'Compare two independent sources')
    async def waiting():
        return await state(store, run) == 'waiting_agents' and len(child_started) == 2
    await until(waiting)
    assert len(parent_calls) == 2 and len(child_calls) == 2 and session not in engine.active_runs
    later = await store.enqueue(session, 'A later user request')
    assert await store.claim(session) is None
    await store.execute("UPDATE runs SET status='cancelled' WHERE id=?", (later,))
    release.set()
    async def complete():
        return await state(store, run) == 'complete'
    await until(complete)
    assert len(parent_calls) == 3 and len(child_calls) == 2 and not forbidden
    history = await store.history(session)
    assert len([m for m in history if m['origin']]) == 1
    assert json.loads(next(m['content'] for m in history if m['tool_call_id'] == 'later'))['status'] == 'not_executed'
    assert (await store.rows('SELECT COUNT(*) AS n FROM requests WHERE run_id=?', (run,)))[0]['n'] == 3


async def test_final_answer_cannot_abandon_unobserved_work(engine, store):
    _, session, run = await parent(store)
    job = (await spawn(engine, session, run))['agent']
    assert await finish_or_wait(store, session, run) == 'waiting'
    assert await state(store, run) == 'waiting_agents' and await store.claim(session) is None
    await store.message(job['child_session_id'], job['child_run_id'], 'assistant', 'Final result.')
    await store.execute("UPDATE runs SET status='complete' WHERE id=?", (job['child_run_id'],))
    assert await store.call(resume_waits) == {session}
    assert await store.claim(session) == run
    assert await finish_or_wait(store, session, run) is None


async def test_stop_cascades_waiting_and_queued_children_and_fork_has_no_live_jobs(engine, store):
    _, session, run = await parent(store)
    job = (await spawn(engine, session, run))['agent']
    child_session, child_run = job['child_session_id'], job['child_run_id']
    await store.claim(child_session)
    grandchild = (await spawn(engine, child_session, child_run, name='grandchild'))['agent']
    await finish_or_wait(store, session, run)
    with pytest.raises(UserError) as error:
        await store.fork(session)
    assert error.value.code == 'fork_busy'
    # Simulate a committed waiting child, so the whole branch can be stopped
    # without an in-memory actor owning this synthetic run.
    await store.execute("UPDATE runs SET status='waiting_agents' WHERE id=?", (child_run,))
    await engine.stop(session)
    assert await state(store, run) == 'cancelled'
    assert await state(store, child_run) == 'cancelled'
    assert await state(store, grandchild['child_run_id']) == 'cancelled'
    assert not await store.call(resume_waits)
    fork = await store.fork(session)
    assert not await store.call(lambda db: branch(db, fork))
    assert await store.history(fork) == await store.history(session)
    assert len(await store.rows('SELECT * FROM agent_jobs')) == 2


async def test_restart_resumes_wait_but_never_replays_interrupted_child(config, monkeypatch):
    path = config.data_dir / 'delegation-restart.sqlite3'
    store = Store(path); await store.initialize()
    engine = Engine(config, store)
    child_calls = 0
    release = asyncio.Event()
    async def provider(client, base, key, payload, on_text, on_reasoning=None):
        nonlocal child_calls
        if payload['messages'][1]['content'].startswith('[Athena delegated subtask]'):
            child_calls += 1
            await on_text('Partial evidence before shutdown')
            await release.wait()
            return response('must not automatically replay')
        assert 'interrupted' in payload['messages'][-1]['content']
        return response('Subtask was interrupted; checking its recorded state before retrying.')
    monkeypatch.setattr('athena.engine.complete', provider)
    _, session, run = await parent(store)
    job = (await spawn(engine, session, run))['agent']
    await finish_or_wait(store, session, run)
    await engine.reconcile_agents()
    async def started():
        return child_calls == 1
    await until(started)
    await engine.close(); await store.close()
    restored = Store(path); await restored.initialize()
    resumed = Engine(config, restored)
    try:
        assert await state(restored, run) == 'waiting_agents'
        assert await state(restored, job['child_run_id']) == 'interrupted'
        await resumed.tick()
        async def complete():
            return await state(restored, run) == 'complete'
        await until(complete)
        assert child_calls == 1
        assert len([m for m in await restored.history(session) if m['origin']]) == 1
    finally:
        await resumed.close(); await restored.close()


async def test_depth_budget_and_orphan_cannot_start(engine, store):
    _, session, run = await parent(store)
    cursor_session, cursor_run = session, run
    for depth in range(1, 4):
        job = (await spawn(engine, cursor_session, cursor_run, name=f'level-{depth}'))['agent']
        assert job['depth'] == depth
        cursor_session, cursor_run = job['child_session_id'], job['child_run_id']
        await store.claim(cursor_session)
    with pytest.raises(UserError) as error:
        await spawn(engine, cursor_session, cursor_run, name='too-deep')
    assert error.value.code == 'agent_limit'
    await store.execute("UPDATE runs SET status='queued' WHERE id=?", (cursor_run,))
    parent_job = (await store.rows('SELECT parent_run_id FROM agent_jobs WHERE child_run_id=?', (cursor_run,)))[0]
    await store.execute("UPDATE runs SET status='interrupted' WHERE id=?", (parent_job['parent_run_id'],))
    assert await store.claim(cursor_session) is None


async def test_queued_input_is_admitted_after_prior_reply_without_rewriting_originals(engine, store):
    _, session, run = await parent(store)
    pending = await store.enqueue(session, 'Then answer this second question.')
    initial = (await store.history(session))[-1]
    await store.message(session, run, 'assistant', 'The first question is complete.')
    await store.execute("UPDATE runs SET status='complete' WHERE id=?", (run,))
    prefix = await engine.context(session)
    assert await store.claim(session) == pending
    after = await engine.context(session)
    assert after[:-1] == prefix
    assert after[-1]['content'] == 'Then answer this second question.'
    original = next(message for message in await store.history(session) if message['id'] == initial['id'])
    assert original['seq'] == initial['seq'] and original['content'] == initial['content']


async def test_main_conversation_routes_child_help_files_and_usage_without_extra_chats(config, monkeypatch):
    app = create_app(config)
    parent_calls, child_calls = [], []
    async def provider(client, base, key, payload, on_text, on_reasoning=None):
        if payload['messages'][1]['content'].startswith('[Athena delegated subtask]'):
            child_calls.append(payload)
            if len(child_calls) == 1:
                return response(tools=[{'id': 'question', 'type': 'function', 'function': {'name': 'runtime',
                    'arguments': json.dumps({'op': 'user_help', 'args': {'questions': [{'id': 'city', 'question': 'Which city should this report cover?'}]}})}}])
            assert 'Kyoto' in payload['messages'][-1]['content'] and '[Attached files' in payload['messages'][-1]['content']
            job = (await app.state.store.rows('SELECT j.*,s.user_id FROM agent_jobs j JOIN sessions s ON s.id=j.child_session_id'))[0]
            directory = config.data_dir / 'files' / job['user_id'] / job['child_session_id']
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'report.md').write_text('# Kyoto\nVerified fixture result.\n')
            return response('Kyoto report is ready in report.md.')
        parent_calls.append(payload)
        if len(parent_calls) == 1:
            return response(tools=[tool('report', 'spawn', name='report', title='Prepare the report', prompt='Prepare a city report.')])
        if len(parent_calls) == 2:
            return response('I am preparing the report.')  # Host automatically waits.
        assert 'Kyoto report is ready' in payload['messages'][-1]['content']
        return response('Your verified report is ready.')
    monkeypatch.setattr('athena.engine.complete', provider)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317') as client:
            await client.post('/api/auth/register', json={'username': 'report-owner', 'password': 'test-password'})
            session = (await client.post('/api/sessions', json={})).json()['id']
            run = (await client.post(f'/api/sessions/{session}/messages', json={'text': 'Prepare my report.'})).json()['run_id']
            async def waiting():
                current = (await client.get(f'/api/sessions/{session}')).json()
                return current['runs'][0]['status'] == 'waiting_agents' and bool(current['help_requests'])
            await until(waiting)
            snapshot = (await client.get(f'/api/sessions/{session}')).json()
            assert len(snapshot['agents']) == 1 and snapshot['agents'][0]['status'] == 'waiting_user'
            question = snapshot['help_requests'][0]
            assert question['delegated_title'] == 'Prepare the report' and question['session_id'] != session
            listed = (await client.get('/api/sessions')).json()
            assert [item['id'] for item in listed] == [session] and listed[0]['pending'] == 1
            upload = await client.post(f'/api/sessions/{session}/files?name=reference.txt', content=b'User-supplied reference')
            assert upload.status_code == 201
            file_id = upload.json()['id']
            answer_path = f'/api/sessions/{session}/help/{question["id"]}/answer'
            answer = {'answers': {'city': 'Kyoto'}, 'file_ids': [file_id]}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317') as outsider:
                await outsider.post('/api/auth/register', json={'username': 'report-outsider', 'password': 'test-password'})
                assert (await outsider.post(answer_path, json=answer)).status_code == 404
                assert (await outsider.get(f'/api/sessions/{question["session_id"]}')).status_code == 404
            answers = await asyncio.gather(client.post(answer_path, json=answer), client.post(answer_path, json=answer))
            assert all(item.status_code == 200 for item in answers)
            async def complete():
                return await state(app.state.store, run) == 'complete'
            await until(complete)
            final = (await client.get(f'/api/sessions/{session}')).json()
            assert final['help_requests'] == []
            assert len(parent_calls) == 3 and len(child_calls) == 2
            assert not any(m['content'] == 'Kyoto' for m in final['messages'])
            result = next(m for m in final['messages'] if m.get('origin', {}).get('kind') == 'agent_results')
            assert len(result['origin']['agents']) == 1
            output = next(file for file in final['files'] if file['name'] == 'report.md')
            assert (await client.get(output['content_url'])).text == '# Kyoto\nVerified fixture result.\n'
            usage = await app.state.engine.tools.introspection.usage(session)
            assert usage['total']['request_count'] == 5 and usage['total']['input_tokens'] == 50
            assert usage['last_context_from_request']['request_id'] == (await app.state.store.rows('SELECT id FROM requests WHERE run_id=? ORDER BY rowid DESC LIMIT 1', (run,)))[0]['id']
            await client.post('/api/settings/developer', json={'enabled': True})
            metrics = (await client.get('/api/developer/metrics', params={'session_id': session})).json()
            assert metrics['total']['request_count'] == 5 and metrics['includes_delegated_work']
            fork = (await client.post(f'/api/sessions/{session}/fork', json={})).json()['id']
            forked = (await client.get(f'/api/sessions/{fork}')).json()
            assert not forked['agents'] and not forked['help_requests']
            assert next(m for m in forked['messages'] if m['id'] == result['id'])['origin'] == result['origin']


async def test_stop_running_child_releases_stream_and_does_not_wake_cancelled_parent(engine, store, monkeypatch):
    _, session, run = await parent(store)
    job = (await spawn(engine, session, run))['agent']
    started, cancelled = asyncio.Event(), asyncio.Event()
    calls = 0
    async def provider(client, base, key, payload, on_text, on_reasoning=None):
        nonlocal calls
        calls += 1
        await on_text('Partial output')
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
    monkeypatch.setattr('athena.engine.complete', provider)
    await finish_or_wait(store, session, run)
    await engine.reconcile_agents()
    await asyncio.wait_for(started.wait(), 3)
    await asyncio.wait_for(engine.stop(session), 3)
    assert cancelled.is_set() and await state(store, run) == 'cancelled'
    assert await state(store, job['child_run_id']) == 'interrupted'
    await engine.tick()
    assert calls == 1 and not [m for m in await store.history(session) if m['origin']]
    child_messages = await store.history(job['child_session_id'])
    assert child_messages[-1]['content'] == 'Partial output' and child_messages[-1]['status'] == 'interrupted'


async def test_any_wait_keeps_other_results_pending_and_failure_is_not_success(engine, store):
    _, session, run = await parent(store)
    jobs = [(await spawn(engine, session, run, name=name))['agent'] for name in ('fast', 'slow')]
    await store.execute("UPDATE runs SET status='failed',error_code='provider_unavailable' WHERE id=?", (jobs[0]['child_run_id'],))
    received = await operation(engine, session, run, 'wait', agent_ids=[job['id'] for job in jobs], mode='any')
    assert received['status'] == 'ready' and len(received['results']) == 1
    assert received['results'][0]['agent']['status'] == 'failed' and received['results'][0]['partial']
    assert received['results'][0]['agent']['error_code'] == 'provider_unavailable'
    assert await finish_or_wait(store, session, run) == 'waiting'
    targets = json.loads((await store.rows("SELECT targets FROM agent_waits WHERE status='pending'"))[0]['targets'])
    assert targets == [jobs[1]['id']]


async def test_stop_during_spawn_commit_cannot_leave_an_unowned_child(engine, store, monkeypatch):
    user, _ = await Auth(store).register('commit-race', 'test-password')
    session = await store.session(user_id=user['id'])
    entered, release = threading.Event(), threading.Event()
    queued_stop = asyncio.Event()
    enqueue, call = Store._enqueue, store.call
    provider_calls = 0

    def delayed_enqueue(db, session_id, text, attachments=None, *, origin=None, frontend_source=None):
        run = enqueue(db, session_id, text, attachments, origin=origin, frontend_source=frontend_source)
        if origin and origin['kind'] == 'agent_task':
            entered.set()
            assert release.wait(5), 'Test did not release the database worker'
        return run

    async def observed_call(operation):
        if operation.__name__ == 'cancel_queue':
            queued_stop.set()
        return await call(operation)

    async def provider(client, base, key, payload, on_text, on_reasoning=None):
        nonlocal provider_calls
        provider_calls += 1
        return response(tools=[tool('race', 'spawn', name='racing-child', title='Racing child', prompt='Do not start after stop.')])

    monkeypatch.setattr(Store, '_enqueue', staticmethod(delayed_enqueue))
    monkeypatch.setattr(store, 'call', observed_call)
    monkeypatch.setattr('athena.engine.complete', provider)
    run = await engine.submit(session, 'Delegate this task.')
    stopping = None
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        stopping = asyncio.create_task(engine.stop(session))
        await asyncio.wait_for(queued_stop.wait(), 3)
    finally:
        release.set()
    await asyncio.wait_for(stopping, 5)
    await engine.reconcile_agents()
    jobs = await store.call(lambda db: branch(db, session))
    assert len(jobs) == 1 and jobs[0]['cancel_requested']
    assert jobs[0]['status'] == 'cancelled' and await state(store, run) == 'interrupted'
    assert provider_calls == 1 and not await store.call(resume_waits)
    assert await store.claim(jobs[0]['child_session_id']) is None
    slots = [m for m in await store.history(session) if m['tool_call_id'] == 'race']
    assert len(slots) == 1


async def test_completed_batches_still_obey_per_run_budget(engine, store):
    _, session, run = await parent(store)
    jobs = []
    for index in range(8):
        job = (await spawn(engine, session, run, name=f'batch-{index}'))['agent']
        jobs.append(job)
        await store.execute("UPDATE runs SET status='complete' WHERE id=?", (job['child_run_id'],))
    assert (await spawn(engine, session, run, name='batch-0'))['created'] is False
    with pytest.raises(UserError) as error:
        await spawn(engine, session, run, name='ninth')
    assert error.value.code == 'agent_limit'
    assert len(await store.rows('SELECT id FROM agent_jobs WHERE parent_run_id=?', (run,))) == 8


async def test_cancelled_child_unwinds_at_its_own_tool_boundary(engine, store, monkeypatch):
    _, session, run = await parent(store)
    job = (await spawn(engine, session, run))['agent']
    child_calls = 0

    async def provider(client, base, key, payload, on_text, on_reasoning=None):
        nonlocal child_calls
        if payload['messages'][1]['content'].startswith('[Athena delegated subtask]'):
            child_calls += 1
            # Cancellation arrives as the provider finishes a tool call, before
            # the periodic reconciler has an opportunity to cancel the actor.
            await store.execute('UPDATE agent_jobs SET cancel_requested=1 WHERE id=?', (job['id'],))
            return response(tools=[tool('status', 'list')])
        assert 'interrupted' in payload['messages'][-1]['content']
        return response('The subtask was stopped; no result is claimed.')

    monkeypatch.setattr('athena.engine.complete', provider)
    await finish_or_wait(store, session, run)
    await engine.reconcile_agents()

    async def complete():
        return await state(store, run) == 'complete'
    await until(complete)
    assert child_calls == 1 and await state(store, job['child_run_id']) == 'interrupted'
    slots = [m for m in await store.history(job['child_session_id']) if m['tool_call_id'] == 'status']
    assert len(slots) == 1
