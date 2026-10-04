"""Task recall must affect real provider payloads, without changing past inputs."""
import asyncio
import json
import shutil
import sys

import httpx
import pytest

from athena.auth import Auth
from athena.memory import (
    NOTE_BUDGET, NOTE_HEADER, RECALL_BUDGET, RECALL_LIMIT, TASK_BUDGET,
    current_memories, memory_operation, recall_candidates,
)
from athena.registry import Registry


async def remember(store, session, content, scope='session'):
    return await memory_operation(store, session, None, {'action': 'add', 'scope': scope, 'content': content})


async def revise(store, session, item, action='update', content=None):
    return await memory_operation(store, session, None, {
        'action': action, 'memory_id': item['memory_id'], 'version': item['version'], 'content': content})


async def age(store, *items):
    for item in items:
        await store.execute("UPDATE memory_revisions SET created_at='2020-01-01T00:00:00+00:00' WHERE memory_id=?",
                            (item['memory_id'],))


async def noise(store, session, count=110, size=0):
    for number in range(count):
        await remember(store, session, f'花园盆栽编号 {number} 喜欢陶盆。' + '土壤保持透气。' * size)


async def provider(engine):
    received = []
    def respond(request):
        received.append(json.loads(request.content))
        chunk = {'choices': [{'delta': {'content': 'Ready.'}, 'finish_reason': 'stop'}],
                 'usage': {'prompt_tokens': 100, 'completion_tokens': 2}}
        return httpx.Response(200, text='data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n')
    await engine.client.aclose()
    engine.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    return received


async def send(engine, session, text):
    await engine.submit(session, text)
    await engine.actors[session]


async def note_for_last_run(store, session):
    return (await store.rows("SELECT run_id,context_note FROM messages WHERE session_id=? AND role='user' ORDER BY seq DESC LIMIT 1",
                             (session,)))[0]


def entries(note):
    return [row for row in json.loads(note[len(NOTE_HEADER):]) if 'id' in row]


@pytest.fixture
async def recall_session(store):
    user, _ = await Auth(store).register('recall-owner', 'synthetic-recall-password')
    return await store.session(user_id=user['id'])


async def test_old_airport_and_corrected_budget_reach_actual_provider_amid_110_irrelevant_memories(engine, store, recall_session):
    session = recall_session
    airport = await remember(store, session, '常用出发机场是上海虹桥，尽量不用浦东。', 'global')
    budget = await remember(store, session, '这次旅行预算为每人 9000 元。', 'project')
    budget = await revise(store, session, budget, content='这次旅行预算改为每人 5000 元。')
    await age(store, airport, budget)
    await noise(store, session, size=80)
    assert airport['memory_id'] not in {row['id'] for row in await store.call(lambda db: current_memories(db, session))}
    task = '从我平时那个机场出发就行，预算按上次改过的。'
    payload = await store.call(lambda db: recall_candidates(db, session, task))
    candidates = {row['id']: row for row in payload['candidates']}
    assert {'机场', '预算'} <= set(payload['retrieval']['terms'])
    assert 'task_match' in candidates[airport['memory_id']]['recall_sources']
    assert candidates[budget['memory_id']]['version'] == 2
    assert len(payload['candidates']) <= RECALL_LIMIT
    assert len(json.dumps(payload['candidates'], ensure_ascii=False)) <= RECALL_BUDGET
    assert payload['retrieval']['more_available'] and payload['task'] == task
    received = await provider(engine)
    await send(engine, session, task)
    assert len(received) == 1
    user_input = [item for item in received[0]['messages'] if item['role'] == 'user'][-1]['content']
    assert '上海虹桥' in user_input and '5000 元' in user_input and '9000 元' not in user_input
    row = await note_for_last_run(store, session)
    assert len(row['context_note']) <= NOTE_BUDGET
    assert {airport['memory_id'], budget['memory_id']} <= {item['id'] for item in entries(row['context_note'])}
    originals = await store.call(lambda db: current_memories(db, session, limit=-1))
    by_id = {item['id']: item for item in originals}
    assert len(originals) == 112
    assert all(item['content'] == by_id[item['id']]['content'] for item in entries(row['context_note']))
    persisted = (await store.rows('SELECT input FROM requests WHERE run_id=?', (row['run_id'],)))[0]['input']
    assert json.loads(persisted) == received[0]


async def test_candidate_union_includes_each_scope_and_is_bounded_for_long_task_and_entries(store, recall_session):
    session = recall_session
    scoped = [await remember(store, session, f'Quiet replies for {scope}', scope) for scope in ('global', 'project', 'session')]
    await age(store, *scoped)
    for number in range(110):
        await remember(store, session, f'Airport preference {number}: ' + '完整条目' * 900)
    payload = await store.call(lambda db: recall_candidates(db, session, 'AIRPORT ' * 2000))
    assert len(payload['task']) == TASK_BUDGET and payload['retrieval']['task_truncated']
    assert len(payload['candidates']) <= RECALL_LIMIT
    assert len(json.dumps(payload['candidates'], ensure_ascii=False)) <= RECALL_BUDGET
    assert len(json.dumps(payload, ensure_ascii=False).encode()) < 1024 * 1024
    assert payload['retrieval']['more_available']
    assert all('airport' in row['matched_terms'] for row in payload['candidates'] if 'task_match' in row['recall_sources'])
    # A smaller task-result set leaves room for otherwise-unrelated scope anchors.
    blank = await store.call(lambda db: recall_candidates(db, session, '...'))
    selected = {row['id']: row for row in blank['candidates']}
    assert scoped[0]['memory_id'] in selected and scoped[1]['memory_id'] in selected
    for item in scoped[:2]:
        assert f"scope:{item['scope']}" in selected[item['memory_id']]['recall_sources']
    assert blank['retrieval']['terms'] == []


async def test_policy_wait_reloads_correction_and_selected_withdrawal_beyond_recent_receipt_window(engine, store, recall_session, monkeypatch):
    session = recall_session
    corrected = await remember(store, session, 'Airport budget is 9000.')
    withdrawn = await remember(store, session, 'Airport private preference withdrawn later.')
    await age(store, corrected, withdrawn)
    await noise(store, session)
    await Registry(engine.config, store).operate({'kind': 'policy', 'action': 'register', 'name': 'memory_recall',
                                               'description': 'Synthetic recall selector', 'source': 'print([])'}, session)
    entered, resume = asyncio.Event(), asyncio.Event()
    observed = []
    async def waiting(self, row, payload):
        observed.append(payload)
        entered.set()
        await resume.wait()
        # Existing plugins still return an ordered list of IDs.
        return [corrected['memory_id'], withdrawn['memory_id']]
    monkeypatch.setattr('athena.policies._supported', lambda: True)
    monkeypatch.setattr('athena.policies.PolicyRunner._run', waiting)
    received = await provider(engine)
    await engine.submit(session, 'Use my airport budget.')
    actor = engine.actors[session]
    await asyncio.wait_for(entered.wait(), timeout=5)
    await revise(store, session, corrected, content='Airport budget is now 5000.')
    await revise(store, session, withdrawn, action='forget')
    for number in range(35):
        item = await remember(store, session, f'Unrelated obsolete note {number}.')
        await revise(store, session, item, action='forget')
    # Make both selected entries older than the most recent 30 active/withdrawn.
    await age(store, corrected, withdrawn)
    resume.set()
    await actor
    assert observed[0]['task'] == 'Use my airport budget.'
    assert len(received) == 1
    note = (await note_for_last_run(store, session))['context_note']
    values = {item['id']: item for item in entries(note)}
    assert values[corrected['memory_id']]['content'] == 'Airport budget is now 5000.'
    assert values[corrected['memory_id']]['version'] == 2
    assert values[withdrawn['memory_id']]['status'] == 'forgotten'
    assert values[withdrawn['memory_id']]['content'] == ''
    assert 'Airport budget is 9000.' not in note and 'private preference' not in note
    assert note in [item['content'] for item in received[0]['messages'] if item['role'] == 'user'][-1]


async def test_fork_recall_keeps_owner_scope_and_independent_session_revision(engine, store, recall_session):
    parent = recall_session
    owner = (await store.rows('SELECT user_id FROM sessions WHERE id=?', (parent,)))[0]['user_id']
    sibling = await store.session(user_id=owner)
    other, _ = await Auth(store).register('recall-outsider', 'synthetic-recall-password')
    outsider = await store.session(user_id=other['id'])
    global_item = await remember(store, parent, 'Airport global shared.', 'global')
    project_item = await remember(store, parent, 'Airport project shared.', 'project')
    local = await remember(store, parent, 'Airport parent original.')
    await remember(store, sibling, 'Airport sibling private.')
    await remember(store, outsider, 'Airport foreign secret.', 'global')
    child = await store.fork(parent)
    child_rows = await store.call(lambda db: current_memories(db, child))
    copied = next(row for row in child_rows if row['scope'] == 'session')
    assert copied['source_memory_id'] == local['memory_id']
    await revise(store, parent, local, content='Airport parent corrected.')
    await age(store, global_item, project_item, {'memory_id': copied['id']})
    await noise(store, child)
    received = await provider(engine)
    await send(engine, child, 'Which airport do I prefer?')
    latest = [row['content'] for row in received[0]['messages'] if row['role'] == 'user'][-1]
    assert 'Airport global shared.' in latest and 'Airport project shared.' in latest
    assert 'Airport parent original.' in latest and 'Airport parent corrected.' not in latest
    assert 'sibling private' not in latest and 'foreign secret' not in latest


@pytest.mark.parametrize('selection', ['duplicate', 'foreign', 'unknown'])
async def test_untrusted_policy_cannot_expand_candidate_scope_and_falls_back(engine, store, recall_session, monkeypatch, selection):
    session = recall_session
    safe = await remember(store, session, 'Airport safe preference.')
    other, _ = await Auth(store).register('policy-outsider', 'synthetic-recall-password')
    outsider = await store.session(user_id=other['id'])
    foreign = await remember(store, outsider, 'Airport private foreign preference.', 'global')
    await Registry(engine.config, store).operate({'kind': 'policy', 'action': 'register', 'name': 'memory_recall',
                                               'description': 'Synthetic recall selector', 'source': 'print([])'}, session)
    async def invalid(self, row, payload):
        assert foreign['memory_id'] not in payload['default']
        return {'duplicate': [safe['memory_id'], safe['memory_id']], 'foreign': [foreign['memory_id']], 'unknown': ['made-up-id']}[selection]
    monkeypatch.setattr('athena.policies._supported', lambda: True)
    monkeypatch.setattr('athena.policies.PolicyRunner._run', invalid)
    received = await provider(engine)
    await send(engine, session, 'Use my airport preference.')
    note = (await note_for_last_run(store, session))['context_note']
    assert 'Airport safe preference.' in note and 'private foreign' not in note
    assert len(received) == 1
    assert (await store.rows('SELECT error_code FROM policy_failures')) == [{'error_code': 'validation_error'}]


@pytest.mark.skipif(sys.platform != 'darwin' or not shutil.which('sandbox-exec'), reason='Real policy isolation requires macOS Seatbelt')
async def test_legacy_default_only_plugin_selects_old_memory_with_actual_request(engine, store, recall_session):
    session = recall_session
    old = await remember(store, session, 'Airport home is Shanghai Hongqiao.')
    await age(store, old)
    await noise(store, session)
    await Registry(engine.config, store).operate({'kind': 'policy', 'action': 'register', 'name': 'memory_recall',
        'description': 'Legacy recall selector', 'source': 'import json,sys\np=json.load(sys.stdin)\nprint(json.dumps(p["default"]))\n'}, session)
    received = await provider(engine)
    await send(engine, session, 'Use my AIRPORT.')
    assert 'Shanghai Hongqiao' in json.dumps(received)
    assert await store.rows('SELECT * FROM policy_failures') == []


async def test_new_recall_never_rewrites_sent_notes_requests_or_stable_prefix(engine, store, recall_session):
    session = recall_session
    item = await remember(store, session, 'Airport budget is 9000.')
    received = await provider(engine)
    await send(engine, session, 'Use my airport budget.')
    first = await note_for_last_run(store, session)
    previous_request = await store.rows('SELECT * FROM requests WHERE run_id=?', (first['run_id'],))
    prefixes = await store.rows('SELECT * FROM context_prefixes')
    await revise(store, session, item, content='Airport budget is now 5000.')
    await noise(store, session)
    # A repeated preflight is not permission to edit already-sent context.
    await engine.prepare_memory(session, first['run_id'])
    assert (await note_for_last_run(store, session)) == first
    await send(engine, session, 'Use my airport budget again.')
    assert await store.rows('SELECT * FROM requests WHERE run_id=?', (first['run_id'],)) == previous_request
    assert await store.rows('SELECT * FROM context_prefixes') == prefixes
    latest_user = [row for row in received[-1]['messages'] if row['role'] == 'user'][-1]['content']
    assert 'Airport budget is now 5000.' in latest_user and 'Airport budget is 9000.' not in latest_user
    first_user = next(row['content'] for row in received[-1]['messages'] if row['role'] == 'user')
    assert 'Airport budget is 9000.' in first_user


async def test_request_persisted_during_policy_wait_freezes_original_admitted_note(engine, store, recall_session, monkeypatch):
    session = recall_session
    item = await remember(store, session, 'Airport original preference.')
    run = await store.enqueue(session, 'Use my airport preference.')
    original = await note_for_last_run(store, session)
    entered, resume = asyncio.Event(), asyncio.Event()
    async def waiting(self, name, sid, payload, validate):
        entered.set()
        await resume.wait()
        validate(payload['default'])
        return {'value': payload['default']}
    monkeypatch.setattr('athena.policies.PolicyRunner.apply', waiting)
    pending = asyncio.create_task(engine.prepare_memory(session, run))
    await asyncio.wait_for(entered.wait(), timeout=5)
    # A concurrent request admission owns the already-projected context now.
    await store.execute("INSERT INTO requests(id,run_id,model,input,status,created_at) VALUES ('synthetic-inflight',?,'synthetic','{}','running',datetime('now'))", (run,))
    await revise(store, session, item, content='Airport updated while selection waited.')
    resume.set()
    await pending
    assert await note_for_last_run(store, session) == original


async def test_run_from_another_session_cannot_have_its_memory_note_rewritten(engine, store, recall_session):
    first = recall_session
    second = await store.session()
    run = await store.enqueue(first, 'Keep this admitted input.')
    before = await store.rows('SELECT context_note FROM messages WHERE run_id=?', (run,))
    with pytest.raises(ValueError, match='admitted input'):
        await engine.prepare_memory(second, run)
    assert await store.rows('SELECT context_note FROM messages WHERE run_id=?', (run,)) == before
