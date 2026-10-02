import json
import pytest

from athena.context_projection import project_messages
from athena.introspection import Introspection, default_compaction_plan
from athena.store import now, uid


def row(identifier, content, **changes):
    return {'id': identifier, 'content': content, 'role': 'tool', 'status': 'complete', **changes}


def test_compaction_folds_only_exact_duplicates_before_touching_unique_evidence():
    repeated = 'Observed result: ' + 'A' * 8000
    history = [row('unique', 'An important unique result: ' + 'B' * 8000), row('old', repeated),
               row('different', repeated + '\nChecked again at a different time.'), row('latest', repeated)]
    plan = default_compaction_plan(history, {}, {'payload_chars': 35000, 'chars_per_token': 1}, 28000)
    assert set(plan) == {'old'}
    assert 'message_id=latest' in plan['old'] and 'message_id=old' in plan['old']
    assert default_compaction_plan(history, {}, {'payload_chars': 35000, 'chars_per_token': 1}, 40000) == {}


def test_clipped_and_incomplete_results_cannot_become_a_duplicate_representative():
    repeated = 'A' * 8000
    history = [row('original', repeated), row('already_clipped', repeated), row('pending', repeated, status='streaming')]
    plan = default_compaction_plan(history, {'already_clipped': 'read_message message_id=already_clipped'}, {'payload_chars': 17000, 'chars_per_token': 1}, 10000)
    assert set(plan) == {'original'}
    assert 'message_id=already_clipped' not in plan['original'] and 'message_id=pending' not in plan['original']


async def test_automatic_compaction_and_introspection_share_the_exact_provider_projection(engine, store):
    session = await store.session()
    run = await store.enqueue(session, 'Keep the only unique observation and my attached itinerary.')
    await store.claim(session)
    user_message = (await store.history(session))[0]['id']
    await store.execute('UPDATE messages SET context_note=?,attachments=?,help_reply=? WHERE id=?', (
        'Persisted memory context', json.dumps([{'id': 'file', 'name': 'itinerary.md', 'workspace_path': 'uploads/itinerary.md'}]),
        json.dumps({'id': 'receipt', 'items': []}), user_message))
    initial = await engine.context(session)
    repeated = 'An unchanged observation\n' + 'A' * 25000
    unique = 'The unique finding\n' + 'B' * 10000
    identifiers = []
    for index, content in enumerate([unique, *([repeated] * 7)]):
        assistant = await store.message(session, run, 'assistant', '', tool_calls=[
            {'id': f'call-{index}', 'type': 'function', 'function': {'name': 'exec', 'arguments': json.dumps({'command': f'observe-{index}'})}}])
        await store.execute('UPDATE messages SET reasoning=? WHERE id=?', (f'Observation {index}', assistant))
        identifiers.append(await store.message(session, run, 'tool', content, tool_call_id=f'call-{index}'))
    profile, epoch = await engine.models.selected(session)
    before = await store.history(session)
    raw_messages = project_messages(before, {}, profile, initial[0]['content'])
    tools = [{'type': 'function', 'function': {'name': 'exec', 'description': 'Read the observation', 'parameters': {'type': 'object'}}}]
    payload = profile.payload(raw_messages, tools)
    encoded = json.dumps(payload, ensure_ascii=False)
    await store.execute('INSERT INTO requests(id,run_id,model,input,status,usage,created_at,profile_snapshot,context_epoch) VALUES (?,?,?,?,?,?,?,?,?)',
        (uid(), run, profile.model, encoded, 'complete', json.dumps({'prompt_tokens': len(encoded) // 4}), now(), json.dumps(profile.snapshot()), epoch))
    projected = await engine.context(session)
    status = await Introspection(store, engine.models).context_status(session)
    assert status['estimated_payload_chars'] == len(json.dumps(profile.payload(projected, tools), ensure_ascii=False))
    assert status['estimated_input_tokens'] <= 20000
    assert projected[1] == initial[1]  # Memory and attachment provenance remain intact.
    assert 'help_reply' not in json.dumps(projected)
    assert await store.history(session) == before
    clips = {item['message_id']: item['content'] for item in await store.rows('SELECT * FROM context_clips WHERE session_id=?', (session,))}
    assert identifiers[0] not in clips and identifiers[-1] not in clips
    assert clips and all(f'message_id={identifiers[-1]}' in value for value in clips.values())
    for index in range(8):
        assert projected[2 + index * 2]['reasoning_content'] == f'Observation {index}'
        assert projected[3 + index * 2]['tool_call_id'] == f'call-{index}'
    restored = await engine.tools.runtime({'op': 'read_message', 'message_id': identifiers[1], 'offset': 0, 'limit': 20000}, session, run)
    assert restored['content'] == repeated[:20000]
    boundaries = await store.rows('SELECT * FROM compactions WHERE session_id=?', (session,))
    assert len(boundaries) == 1
    assert await engine.context(session) == projected
    assert await store.rows('SELECT * FROM compactions WHERE session_id=?', (session,)) == boundaries


async def test_large_preserved_history_never_immediately_reclips_fresh_recovery_results(engine, store):
    session = await store.session()
    run = await store.enqueue(session, 'A long user-owned specification. ' * 7000)
    await store.claim(session)
    await store.message(session, run, 'assistant', '', tool_calls=[{'id': 'old', 'type': 'function', 'function': {'name': 'exec', 'arguments': '{}'}}])
    await store.message(session, run, 'tool', 'An old observation. ' * 500, tool_call_id='old')
    await store.message(session, run, 'assistant', '', tool_calls=[
        {'id': 'recover', 'type': 'function', 'function': {'name': 'runtime', 'arguments': '{"op":"read_message"}'}},
        {'id': 'inspect', 'type': 'function', 'function': {'name': 'exec', 'arguments': '{}'}}])
    recovered = 'Critical detail from the middle of the archived file. ' * 150
    fresh = 'A fresh observation. ' * 150
    newest = await store.message(session, run, 'tool', recovered, tool_call_id='recover')
    await store.message(session, run, 'tool', fresh, tool_call_id='inspect')
    projected = await engine.context(session)
    assert projected[-2]['content'] == recovered and projected[-1]['content'] == fresh
    assert len(await store.rows('SELECT * FROM compactions WHERE session_id=?', (session,))) == 1
    assert await engine.context(session) == projected
    assert len(await store.rows('SELECT * FROM compactions WHERE session_id=?', (session,))) == 1
    before = await store.rows('SELECT * FROM context_clips WHERE session_id=?', (session,))
    with pytest.raises(ValueError, match='latest tool round'):
        await Introspection(store, engine.models).compact(session, 1000, {newest: f'read_message message_id={newest}'}, protect_latest=True)
    assert await store.rows('SELECT * FROM context_clips WHERE session_id=?', (session,)) == before
