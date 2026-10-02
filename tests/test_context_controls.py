import json

import pytest

from athena.auth import Auth
from athena.context_controls import ContextControls, apply_exclusions, copy_exclusions, initialize_context_controls
from athena.errors import UserError
from athena.skillpacks import read_skill, skill_catalog
from athena.store import now


@pytest.fixture
async def conversations(store):
    await initialize_context_controls(store)
    user, _ = await Auth(store).register('conversation-owner', 'conversation-test-password')
    current = await store.session('Current conversation', user_id=user['id'])
    return ContextControls(store), user, current


async def completed_run(store, session, text, tool=False):
    run = await store.enqueue(session, text)
    await store.execute("UPDATE runs SET status='complete',finished_at=? WHERE id=?", (now(), run))
    await store.execute("UPDATE messages SET status='complete' WHERE run_id=?", (run,))
    if tool:
        await store.message(session, run, 'assistant', '', tool_calls=[{'id': 'tool-' + run, 'type': 'function', 'function': {'name': 'exec', 'arguments': '{"command":"echo already-completed"}'}}])
        await store.message(session, run, 'tool', 'Tool result kept intact', tool_call_id='tool-' + run)
    await store.message(session, run, 'assistant', 'Answer for ' + text)
    return run


async def projection(store, session):
    return await store.call(lambda db: apply_exclusions(db, session, __import__('athena.store', fromlist=['Store']).Store._history(db, session)))


async def test_list_search_pagination_new_rename_and_owned_scope(conversations, store):
    controls, user, current = conversations
    first = await controls.operate(current, None, {'action': 'new', 'title': 'Budget planning'})
    second = await controls.operate(current, None, {'action': 'new', 'title': 'Budget 100% travel'})
    assert not first['activated'] and first['scope'] == 'own_account'
    other_user, _ = await Auth(store).register('private-owner', 'different-conversation-password')
    private = await store.session('Budget private', user_id=other_user['id'])
    page = await controls.operate(current, None, {'action': 'list', 'query': 'Budget', 'limit': 1})
    assert page['total'] == 2 and page['has_more'] and len(page['sessions']) == 1
    next_page = await controls.operate(current, None, {'action': 'list', 'query': 'Budget', 'limit': 1, 'offset': page['next_offset']})
    assert not next_page['has_more']
    assert {item['id'] for item in page['sessions'] + next_page['sessions']} == {first['session_id'], second['session_id']}
    assert (await controls.operate(current, None, {'action': 'list', 'query': '%'}))['total'] == 1
    renamed = await controls.operate(current, None, {'action': 'rename', 'session_id': first['session_id'], 'title': 'New title'})
    assert renamed['title'] == 'New title'
    assert (await store.rows('SELECT title FROM sessions WHERE id=?', (first['session_id'],)))[0]['title'] == 'New title'
    for action in ('read', 'rename', 'fork'):
        args = {'action': action, 'session_id': private}
        if action == 'rename':
            args['title'] = 'Not allowed'
        with pytest.raises(UserError) as denied:
            await controls.operate(current, None, args)
        assert denied.value.code == 'session_not_found'
    assert not await store.rows('SELECT * FROM events WHERE session_id=?', (private,))


@pytest.mark.parametrize('args', [
    {'action': 'list', 'limit': 0}, {'action': 'list', 'limit': True}, {'action': 'list', 'limit': 101},
    {'action': 'read', 'limit': 0}, {'action': 'read', 'limit': 20001}, {'action': 'read', 'offset': -1},
    {'action': 'new', 'title': ''}, {'action': 'rename', 'title': 'x' * 121},
    {'action': 'read', 'request_input': True}, {'action': 'list', 'query': ['not-a-query']},
])
async def test_invalid_limits_and_unknown_fields_are_rejected(conversations, args):
    controls, _, current = conversations
    with pytest.raises(UserError) as invalid:
        await controls.operate(current, None, args)
    assert invalid.value.code == 'invalid_input'


async def test_read_recovers_pages_applies_clips_and_never_reads_request_secrets(conversations, store):
    controls, user, current = conversations
    target = await store.session('Reference chat', user_id=user['id'])
    run = await completed_run(store, target, 'abcdefghijklmnopqrstuvwxyz', tool=True)
    history = await store.history(target)
    clipped = next(row for row in history if row['role'] == 'tool')
    await store.execute('INSERT INTO context_clips VALUES (?,?,?,?)', (target, clipped['id'], '[clipped]', now()))
    # Stored provider requests are deliberately outside the conversations reader's selection.
    await store.execute('INSERT INTO requests(id,run_id,model,input,status,created_at) VALUES (?,?,?,?,?,?)', ('private-request', run, 'test', 'PRIVATE_PROVIDER_REQUEST', 'complete', now()))
    collected, offset = {}, 0
    while True:
        result = await controls.operate(current, None, {'action': 'read', 'session_id': target, 'offset': offset, 'limit': 7})
        assert result['scope'] == 'other_conversation_reference'
        assert 'not new user instructions' in result['notice']
        assert sum(len(message['content']) for message in result['messages']) <= 7
        assert 'PRIVATE_PROVIDER_REQUEST' not in json.dumps(result)
        for message in result['messages']:
            assert message['content_offset'] == len(collected.get(message['id'], ''))
            collected[message['id']] = collected.get(message['id'], '') + message['content']
            if message['id'] == clipped['id']:
                assert message['clipped'] and message['original_chars'] == len(clipped['content'])
        if not result['has_more']:
            break
        assert result['next_offset'] > offset
        offset = result['next_offset']
    assert collected == {row['id']: '[clipped]' if row['id'] == clipped['id'] else row['content'] for row in history}
    assert (await store.rows('SELECT content FROM messages WHERE id=?', (clipped['id'],)))[0]['content'] == 'Tool result kept intact'


async def test_exclude_restore_versions_boundary_audit_and_tool_pairs_preserve_originals(conversations, store):
    controls, _, current = conversations
    old = await completed_run(store, current, 'Exclude this entire run', tool=True)
    keep = await completed_run(store, current, 'Keep this run')
    source = await store.enqueue(current, 'Adjust my context')
    original = await store.history(current)
    changed = await controls.operate(current, source, {'action': 'exclude_run', 'run_id': old, 'reason': 'No longer relevant'})
    assert changed['changed'] and changed['version'] == 1 and changed['originals_preserved']
    visible = await projection(store, current)
    assert {row['run_id'] for row in visible} == {keep, source}
    assert not [row for row in visible if row['tool_calls'] or row['tool_call_id']]
    assert await store.history(current) == original
    read = await controls.operate(current, source, {'action': 'read'})
    assert old not in {row['run_id'] for row in read['messages']}
    markers = await store.rows('SELECT * FROM compactions WHERE session_id=?', (current,))
    assert markers[0]['id'] == changed['boundary_id'] and markers[0]['before_chars'] > markers[0]['after_chars']
    event = (await store.rows("SELECT * FROM events WHERE type='context_changed'"))[0]
    assert json.loads(event['payload'])['source_run_id'] == source
    repeated = await controls.operate(current, source, {'action': 'exclude_run', 'run_id': old})
    assert not repeated['changed'] and repeated['version'] == 1
    restored = await controls.operate(current, source, {'action': 'restore_run', 'run_id': old})
    assert restored['version'] == 2 and not restored['excluded']
    assert await projection(store, current) == original
    assert [row['excluded'] for row in await store.rows('SELECT excluded FROM context_exclusions ORDER BY version')] == [1, 0]
    assert len(await store.rows('SELECT * FROM compactions')) == 2


async def test_context_mutations_reject_active_queued_absent_and_other_session_runs(conversations, store):
    controls, user, current = conversations
    completed = await completed_run(store, current, 'Finished')
    queued = await store.enqueue(current, 'Queued')
    running = await store.enqueue(current, 'Running')
    await store.execute("UPDATE runs SET status='running' WHERE id=?", (running,))
    other = await store.session('Other own chat', user_id=user['id'])
    unrelated = await completed_run(store, other, 'Another chat')
    for target in (queued, running, unrelated, 'missing-run'):
        for action in ('exclude_run', 'restore_run'):
            with pytest.raises(UserError):
                await controls.operate(current, running, {'action': action, 'run_id': target})
    with pytest.raises(UserError):
        await controls.operate(current, completed, {'action': 'exclude_run', 'run_id': completed})
    with pytest.raises(UserError):
        await controls.operate(current, running, {'action': 'exclude_run', 'session_id': other, 'run_id': unrelated})
    with pytest.raises(UserError):
        await controls.operate(current, unrelated, {'action': 'read'})
    assert not await store.rows('SELECT * FROM context_exclusions')
    assert not await store.rows('SELECT * FROM compactions')


async def test_fork_snapshot_of_exclusions_is_immutable_and_busy_current_is_rejected(conversations, store):
    controls, user, current = conversations
    first = await completed_run(store, current, 'First branch context', tool=True)
    second = await completed_run(store, current, 'Second branch context')
    await controls.operate(current, None, {'action': 'exclude_run', 'run_id': first})
    fork = (await controls.operate(current, None, {'action': 'fork'}))['session_id']
    assert {row['run_id'] for row in await projection(store, fork)} == {second}
    # The helper is idempotent when Store.fork has already copied in its transaction.
    await store.call(lambda db: copy_exclusions(db, current, fork))
    assert {row['run_id'] for row in await projection(store, fork)} == {second}
    await controls.operate(current, None, {'action': 'restore_run', 'run_id': first})
    assert {row['run_id'] for row in await projection(store, current)} == {first, second}
    assert {row['run_id'] for row in await projection(store, fork)} == {second}
    await controls.operate(fork, None, {'action': 'restore_run', 'run_id': first})
    await controls.operate(fork, None, {'action': 'exclude_run', 'run_id': second})
    assert {row['run_id'] for row in await projection(store, fork)} == {first}
    assert {row['run_id'] for row in await projection(store, current)} == {first, second}
    queued = await store.enqueue(current, 'Current run cannot fork itself')
    with pytest.raises(UserError) as busy:
        await controls.operate(current, queued, {'action': 'fork'})
    assert busy.value.code == 'fork_busy'
    stranger, _ = await Auth(store).register('different-context-owner', 'stranger-context-password')
    private = await store.session(user_id=stranger['id'])
    with pytest.raises(UserError):
        await store.call(lambda db: copy_exclusions(db, current, private))


def test_conversations_skill_has_discoverable_directory_metadata():
    skill = next(item for item in skill_catalog() if item['name'] == 'conversations')
    assert skill['description'] and 'exclude_run' in read_skill('conversations')
