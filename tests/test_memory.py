"""Memory promises are effects: correct, forget, recover, fork and keep scope."""
import asyncio
import json
import pytest

from athena.auth import Auth
from athena.memory import NOTE_BUDGET, NOTE_HEADER, PAGE_BUDGET, current_memories


async def memory(engine, session, action, run=None, **fields):
    return await engine.tools.execute('runtime', {'op': 'memory', 'args': {'action': action, **fields}}, session, run)


@pytest.fixture
async def sessions(store):
    user, _ = await Auth(store).register('memory-owner', 'synthetic-test-password')
    other, _ = await Auth(store).register('memory-outsider', 'synthetic-test-password')
    return (await store.session(user_id=user['id']), await store.session(user_id=user['id']),
            await store.session(user_id=other['id']))


async def test_corrections_have_versions_and_sources_without_rewriting_originals(engine, store, sessions):
    session, sibling, _ = sessions
    first_run = await store.enqueue(session, 'Remember: I prefer English replies.')
    added = await memory(engine, session, 'add', first_run, scope='global', content='Reply in English.')
    original = await store.rows('SELECT * FROM memories')
    second_run = await store.enqueue(sibling, 'Actually use Japanese from now on.')
    results = await asyncio.gather(*[
        memory(engine, sibling, 'update', second_run, memory_id=added['memory_id'], version=1, content=value)
        for value in ('Reply in Japanese.', 'Reply in Spanish.')], return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 1
    failure = next(result for result in results if isinstance(result, ValueError))
    assert failure.code == 'memory_changed'
    latest = await memory(engine, sibling, 'read', memory_id=added['memory_id'])
    assert latest['version'] == 2 and latest['source_run_id'] == second_run
    assert latest['source_session_id'] == sibling
    history = await memory(engine, sibling, 'history', memory_id=added['memory_id'])
    assert [(row['version'], row['source_run_id']) for row in history['items']] == [(2, second_run), (1, first_run)]
    assert history['items'][1]['content'] == 'Reply in English.'
    assert await store.rows('SELECT * FROM memories') == original
    assert (await memory(engine, sibling, 'list', query='English'))['items'] == []


async def test_forget_and_explicit_restore_have_honest_durable_effects(engine, store, sessions):
    session, sibling, _ = sessions
    added = await memory(engine, session, 'add', scope='global', content='A private preference.')
    forgotten = await memory(engine, sibling, 'forget', memory_id=added['memory_id'], version=1)
    assert forgotten['version'] == 2 and forgotten['historical_context_retained']
    assert (await memory(engine, sibling, 'list'))['items'] == []
    managed = (await memory(engine, sibling, 'list', include_forgotten=True))['items']
    assert managed[0]['content'] == '' and managed[0]['status'] == 'forgotten'
    run = await store.enqueue(sibling, 'Continue without this preference.')
    note = (await store.rows('SELECT context_note FROM messages WHERE run_id=?', (run,)))[0]['context_note']
    assert 'A private preference.' not in note and 'forgotten' in note
    with pytest.raises(ValueError, match='Restore'):
        await memory(engine, sibling, 'update', memory_id=added['memory_id'], version=2, content='An accidental resurrection.')
    repeated = await memory(engine, sibling, 'forget', memory_id=added['memory_id'], version=2)
    assert not repeated['changed'] and repeated['version'] == 2
    restored = await memory(engine, sibling, 'restore', memory_id=added['memory_id'], version=2, restore_version=1)
    assert restored['version'] == 3
    assert (await memory(engine, sibling, 'list'))['items'][0]['content'] == 'A private preference.'
    await store.initialize()
    history = await memory(engine, sibling, 'history', memory_id=added['memory_id'], limit=2)
    assert [row['version'] for row in history['items']] == [3, 2] and history['next_offset'] == 2
    assert (await memory(engine, sibling, 'history', memory_id=added['memory_id'], offset=2))['items'][0]['version'] == 1


async def test_exact_additions_are_atomic_and_dont_merge_distinct_scopes(engine, store, sessions):
    session, _, _ = sessions
    results = await asyncio.gather(*[memory(engine, session, 'add', scope='global', content='Prefer concise replies.') for _ in range(12)])
    assert len({row['memory_id'] for row in results}) == 1
    assert sum(row['changed'] for row in results) == 1
    await memory(engine, session, 'add', scope='session', content='Prefer concise replies.')
    assert len(await store.rows('SELECT * FROM memory_revisions')) == 2


async def test_scope_checks_cover_read_history_mutation_and_source_provenance(engine, store, sessions):
    session, sibling, outsider = sessions
    global_item = await memory(engine, session, 'add', scope='global', content='Visible in my other conversations.')
    local = await memory(engine, session, 'add', scope='session', content='Only this conversation.')
    assert len((await memory(engine, sibling, 'list'))['items']) == 1
    assert (await memory(engine, outsider, 'list', include_forgotten=True))['items'] == []
    for target, item in ((outsider, global_item), (sibling, local)):
        for action in ('read', 'history', 'update', 'forget', 'restore'):
            with pytest.raises(ValueError, match='not found'):
                await memory(engine, target, action, memory_id=item['memory_id'], version=1, restore_version=1, content='Injected.')
    unrelated_run = await store.enqueue(outsider, 'Unrelated source.')
    with pytest.raises(ValueError, match='source run'):
        await memory(engine, session, 'add', unrelated_run, content='Forged provenance.')
    with pytest.raises(ValueError, match='scope'):
        await memory(engine, session, 'add', scope='account', content='Invalid scope.')


async def test_fork_copies_current_active_session_memory_with_independent_lineage(engine, store, sessions):
    session, _, _ = sessions
    first = await memory(engine, session, 'add', content='Original preference.')
    await memory(engine, session, 'update', memory_id=first['memory_id'], version=1, content='Corrected preference.')
    inactive = await memory(engine, session, 'add', content='Do not inherit.')
    await memory(engine, session, 'forget', memory_id=inactive['memory_id'], version=1)
    child = await store.fork(session)
    copied = (await memory(engine, child, 'list', include_forgotten=True))['items']
    assert len(copied) == 1 and copied[0]['content'] == 'Corrected preference.'
    assert copied[0]['version'] == 1 and copied[0]['id'] != first['memory_id']
    assert copied[0]['source_memory_id'] == first['memory_id'] and copied[0]['source_version'] == 2
    await memory(engine, session, 'forget', memory_id=first['memory_id'], version=2)
    assert (await memory(engine, child, 'list'))['items'] == copied
    await memory(engine, child, 'update', memory_id=copied[0]['id'], version=1, content='Child direction.')
    assert (await memory(engine, session, 'list'))['items'] == []


async def test_recall_budget_preserves_complete_recoverable_entries_and_reports_more(engine, store, sessions):
    session, _, _ = sessions
    for number in range(35):
        await memory(engine, session, 'add', content=f'Entry {number}: ' + 'x' * 3800)
    run = await store.enqueue(session, 'Only bring useful memory into this task.')
    note = (await store.rows('SELECT context_note FROM messages WHERE run_id=?', (run,)))[0]['context_note']
    assert len(note) <= NOTE_BUDGET
    values = json.loads(note[len(NOTE_HEADER):])
    assert any(row.get('omitted_entries') for row in values) and any(row.get('more_available') for row in values)
    for item in values:
        if 'id' in item:
            original = await memory(engine, session, 'read', memory_id=item['id'])
            assert item['content'] == original['content']  # No invisible truncation of a preference.
    identifiers, offset = set(), 0
    while offset is not None:
        page = await memory(engine, session, 'list', limit=20, offset=offset)
        assert len(json.dumps(page['items'], ensure_ascii=False)) <= PAGE_BUDGET
        identifiers.update(row['id'] for row in page['items'])
        offset = page['next_offset']
    assert len(identifiers) == 35


async def test_policy_cannot_recall_stale_or_forgotten_candidates(engine, store, sessions, monkeypatch):
    session, _, _ = sessions
    corrected = await memory(engine, session, 'add', content='An outdated fact.')
    forgotten = await memory(engine, session, 'add', content='A withdrawn fact.')
    run = await store.enqueue(session, 'Use current facts.')

    async def slow_policy(self, name, sid, payload, validate):
        await memory(engine, session, 'update', memory_id=corrected['memory_id'], version=1, content='A verified correction.')
        await memory(engine, session, 'forget', memory_id=forgotten['memory_id'], version=1)
        return {'value': payload['default']}

    monkeypatch.setattr('athena.policies.PolicyRunner.apply', slow_policy)
    await engine.prepare_memory(session, run)
    note = (await store.rows('SELECT context_note FROM messages WHERE run_id=?', (run,)))[0]['context_note']
    assert 'A verified correction.' in note
    assert 'An outdated fact.' not in note and 'A withdrawn fact.' not in note
    assert 'forgotten' in note


async def test_model_requests_use_new_memory_versions_while_prior_context_stays_frozen(engine, store, sessions, monkeypatch):
    session, sibling, _ = sessions
    added = await memory(engine, session, 'add', scope='global', content='Reply in English.')
    payloads = []

    async def provider(client, base, key, payload, on_text, on_reasoning):
        payloads.append(payload)
        return {'message': {'content': 'Done', 'reasoning_content': ''}, 'usage': {}}

    monkeypatch.setattr('athena.engine.complete', provider)
    await engine.submit(session, 'Start.')
    await engine.actors[session]
    original = await engine.context(session)
    await memory(engine, sibling, 'update', memory_id=added['memory_id'], version=1, content='Reply in Japanese.')
    assert await engine.context(session) == original
    await engine.submit(sibling, 'Continue here.')
    await engine.actors[sibling]
    encoded = json.dumps(payloads[-1], ensure_ascii=False)
    assert 'Reply in Japanese.' in encoded and 'Reply in English.' not in encoded
    await memory(engine, sibling, 'forget', memory_id=added['memory_id'], version=2)
    await engine.submit(sibling, 'This preference no longer applies.')
    await engine.actors[sibling]
    latest_user = [row for row in payloads[-1]['messages'] if row['role'] == 'user'][-1]
    assert 'Reply in Japanese.' not in latest_user['content'] and 'forgotten' in latest_user['content']


async def test_legacy_migration_preserves_source_and_is_idempotent(engine, store, sessions):
    session, _, _ = sessions
    run = await store.enqueue(session, 'A legacy fact.')
    await store.execute("INSERT INTO memories VALUES ('legacy-memory','session',?,'Legacy original',?,datetime('now'))", (session, run))
    original = await store.rows('SELECT * FROM memories')
    await store.initialize()
    await store.initialize()
    assert await store.rows('SELECT * FROM memories') == original
    migrated = await store.rows('SELECT * FROM memory_revisions')
    assert len(migrated) == 1 and migrated[0]['content'] == 'Legacy original'
    assert migrated[0]['source_session_id'] == session and migrated[0]['source_run_id'] == run
    assert len(await store.call(lambda db: current_memories(db, session))) == 1
