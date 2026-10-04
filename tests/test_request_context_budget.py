"""Budget the actual provider request, including newly registered schemas."""
from dataclasses import replace
import json

import httpx

from athena.auth import Auth
from athena.store import now, uid


async def seed_budget_case(engine, store):
    user, _ = await Auth(store).register('budget-test', 'synthetic-budget-password')
    session = await store.session(user_id=user['id'])
    profile, epoch = await engine.models.selected(session)
    profile = replace(profile, context_chars=30000)
    await store.execute('UPDATE model_profiles SET definition=? WHERE id=?', (json.dumps(profile.snapshot()), profile.id))
    old_run = await store.enqueue(session, 'Keep the itinerary and every completed action.')
    await store.claim(session)
    old_user = (await store.history(session))[0]['id']
    await store.execute('UPDATE messages SET context_note=?, attachments=? WHERE id=?', (
        'Remember the full specification: ' + 'M' * 7000,
        json.dumps([{'name': f'itinerary-{index}-' + 'N' * 160 + '.md',
                     'workspace_path': f'uploads/itinerary-{index}-' + 'N' * 160 + '.md'} for index in range(10)]), old_user))
    await store.message(session, old_run, 'assistant', 'Already inspected.', tool_calls=[{
        'id': 'old-call', 'type': 'function', 'function': {'name': 'exec', 'arguments': '{"command":"already-completed"}'}}])
    original = 'Retained beginning\n' + 'O' * 13000 + '\nRetained ending'
    old_tool = await store.message(session, old_run, 'tool', original, tool_call_id='old-call')
    fresh_assistant = await store.message(session, old_run, 'assistant', 'Recovered fresh evidence.', tool_calls=[{
        'id': 'fresh-call', 'type': 'function', 'function': {'name': 'runtime', 'arguments': '{"op":"read_message"}'}}])
    await store.execute('UPDATE messages SET reasoning=? WHERE id=?', ('Preserve this reasoning', fresh_assistant))
    fresh_tool = await store.message(session, old_run, 'tool', 'Fresh evidence must reach the model in full.', tool_call_id='fresh-call')
    # The previous request knew a much smaller schema. Its tokenizer ratio must
    # not make the next request ignore a smaller application character guard.
    previous = profile.payload([{'role': 'user', 'content': 'Historical calibration ' * 30}], [])
    encoded = json.dumps(previous, ensure_ascii=False)
    await store.execute('INSERT INTO requests(id,run_id,model,input,status,usage,created_at,profile_snapshot,context_epoch) VALUES (?,?,?,?,?,?,?,?,?)', (
        uid(), old_run, profile.model, encoded, 'complete', json.dumps({'prompt_tokens': len(encoded) // 4}), now(), json.dumps(profile.snapshot()), epoch))
    await store.execute("UPDATE runs SET status='complete' WHERE id=?", (old_run,))
    await engine.tools.runtime({'op': 'register_tool', 'name': 'user_budget_probe', 'description': 'Inspect structured details.',
        'parameters': {'type': 'object', 'properties': {'details': {'type': 'string', 'description': 'D' * 8000}}},
        'source': 'print("This tool must not replay")\n'}, session, None)
    return session, profile, old_tool, fresh_tool, original


async def install_provider(engine):
    received = []
    def respond(request):
        received.append(json.loads(request.content))
        chunk = {'choices': [{'delta': {'content': 'The itinerary is ready.'}, 'finish_reason': 'stop'}],
                 'usage': {'prompt_tokens': 100, 'completion_tokens': 6}}
        return httpx.Response(200, text='data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n')
    await engine.client.aclose()
    engine.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    return received


async def test_actual_request_compacts_new_schema_notes_and_attachments_without_replaying(engine, store, monkeypatch):
    session, profile, old_tool, fresh_tool, original = await seed_budget_case(engine, store)
    before = await store.history(session)
    received = await install_provider(engine)
    executions = []
    async def reject_replay(*args, **kwargs):
        executions.append((args, kwargs))
        raise AssertionError('No new action was requested; completed actions must not replay.')
    monkeypatch.setattr(engine.tools, 'execute', reject_replay)
    run = await engine.submit(session, 'Please continue from the saved observations.')
    await engine.actors[session]
    state = (await store.rows('SELECT status,error_code FROM runs WHERE id=?', (run,)))[0]
    assert state == {'status': 'complete', 'error_code': None}
    assert len(received) == 1
    assert executions == []
    sent = received[0]
    assert len(json.dumps(sent, ensure_ascii=False)) <= profile.context_chars
    assert sent['tools'][-1]['function']['name'] == 'user_budget_probe'
    assert len(sent['tools'][-1]['function']['parameters']['properties']['details']['description']) == 8000
    assert 'M' * 7000 in sent['messages'][1]['content'] and 'N' * 160 in sent['messages'][1]['content']
    old = next(item for item in sent['messages'] if item.get('tool_call_id') == 'old-call')
    fresh = next(item for item in sent['messages'] if item.get('tool_call_id') == 'fresh-call')
    assert f'message_id={old_tool}' in old['content'] and len(old['content']) < len(original)
    assert fresh['content'] == 'Fresh evidence must reach the model in full.'
    assistant = next(item for item in sent['messages'] if item.get('tool_calls', [{}])[0].get('id') == 'fresh-call')
    assert assistant['reasoning_content'] == 'Preserve this reasoning'
    assert await store.rows('SELECT * FROM context_clips WHERE message_id=?', (fresh_tool,)) == []
    assert (await store.history(session))[:len(before)] == before
    restored = await engine.tools.runtime({'op': 'read_message', 'message_id': old_tool, 'limit': 20000}, session, run)
    assert restored['content'] == original
    saved = (await store.rows('SELECT input,tool_versions FROM requests WHERE run_id=?', (run,)))[0]
    assert json.loads(saved['input']) == sent
    assert json.loads(saved['tool_versions']) == {'user_budget_probe': 1}
    assert len(await store.rows('SELECT * FROM compactions WHERE session_id=?', (session,))) == 1


async def test_tools_and_bindings_remain_frozen_if_registration_changes_during_compaction(engine, store, monkeypatch):
    session, _, _, _, _ = await seed_budget_case(engine, store)
    received = await install_provider(engine)
    compact = engine.tools.compact
    changed = False
    async def compact_then_register(*args, **kwargs):
        nonlocal changed
        result = await compact(*args, **kwargs)
        if not changed:
            changed = True
            await engine.tools.runtime({'op': 'register_tool', 'name': 'user_budget_probe', 'description': 'The next schema.',
                'parameters': {'type': 'object', 'properties': {'new_field': {'type': 'boolean'}}},
                'source': 'print("Version two")\n'}, session, None)
        return result
    monkeypatch.setattr(engine.tools, 'compact', compact_then_register)
    run = await engine.submit(session, 'Use the existing observations.')
    await engine.actors[session]
    assert (await store.rows('SELECT status FROM runs WHERE id=?', (run,)))[0]['status'] == 'complete'
    first = received[0]
    assert 'details' in first['tools'][-1]['function']['parameters']['properties']
    assert json.loads((await store.rows('SELECT tool_versions FROM requests WHERE run_id=?', (run,)))[0]['tool_versions']) == {'user_budget_probe': 1}
    next_run = await engine.submit(session, 'Continue with the newly registered tool.')
    await engine.actors[session]
    assert 'new_field' in received[1]['tools'][-1]['function']['parameters']['properties']
    assert json.loads((await store.rows('SELECT tool_versions FROM requests WHERE run_id=?', (next_run,)))[0]['tool_versions']) == {'user_budget_probe': 2}


async def test_unshrinkable_payload_never_reaches_provider_or_discards_latest_evidence(engine, store):
    session, profile, old_tool, fresh_tool, original = await seed_budget_case(engine, store)
    profile = replace(profile, context_chars=12000)
    await store.execute('UPDATE model_profiles SET definition=? WHERE id=?', (json.dumps(profile.snapshot()), profile.id))
    before = await store.history(session)
    received = await install_provider(engine)
    run = await engine.submit(session, 'Continue without dropping my instructions.')
    await engine.actors[session]
    assert (await store.rows('SELECT status,error_code FROM runs WHERE id=?', (run,)))[0] == {'status': 'failed', 'error_code': 'context_limit'}
    assert received == []
    assert await store.rows('SELECT id FROM requests WHERE run_id=?', (run,)) == []
    assert await store.rows('SELECT * FROM context_clips WHERE message_id=?', (fresh_tool,)) == []
    assert (await store.history(session))[:len(before)] == before
    assert (await engine.tools.runtime({'op': 'read_message', 'message_id': old_tool, 'limit': 20000}, session, run))['content'] == original
    assert len(await store.rows('SELECT * FROM compactions WHERE session_id=?', (session,))) == 1


async def test_prefix_refresh_only_at_boundary_is_included_in_second_budget_check(engine, store):
    user, _ = await Auth(store).register('prefix-test', 'synthetic-prefix-password')
    session = await store.session(user_id=user['id'])
    profile, _ = await engine.models.selected(session)
    profile = replace(profile, context_chars=15000)
    await store.execute('UPDATE model_profiles SET definition=? WHERE id=?', (json.dumps(profile.snapshot()), profile.id))
    initial = await engine.context(session)
    for index in range(16):
        await engine.tools.runtime({'op': 'registry', 'kind': 'skill', 'action': 'register', 'name': f'user_budget{index}',
            'description': f'Catalog entry {index}: ' + 'S' * 270, 'source': 'Use this skill only when needed.'}, session, None)
    assert await engine.context(session) == initial
    assert len(await store.rows('SELECT * FROM context_prefixes WHERE session_id=?', (session,))) == 1
    old_run = await store.enqueue(session, 'Preserve my original task.')
    await store.claim(session)
    await store.message(session, old_run, 'assistant', '', tool_calls=[{
        'id': 'prefix-old', 'type': 'function', 'function': {'name': 'exec', 'arguments': '{}'}}])
    original = 'Unchanged archival observation. ' * 430
    old_tool = await store.message(session, old_run, 'tool', original, tool_call_id='prefix-old')
    await store.message(session, old_run, 'assistant', '', tool_calls=[{
        'id': 'prefix-fresh', 'type': 'function', 'function': {'name': 'exec', 'arguments': '{}'}}])
    await store.message(session, old_run, 'tool', 'An important fresh observation.', tool_call_id='prefix-fresh')
    await store.execute("UPDATE runs SET status='complete' WHERE id=?", (old_run,))
    received = await install_provider(engine)
    run = await engine.submit(session, 'Continue from the fresh observation.')
    await engine.actors[session]
    assert (await store.rows('SELECT status FROM runs WHERE id=?', (run,)))[0]['status'] == 'complete'
    assert len(received) == 1
    assert len(json.dumps(received[0], ensure_ascii=False)) <= profile.context_chars
    assert 'user_budget15' in received[0]['messages'][0]['content']
    prefixes = await store.rows('SELECT prompt FROM context_prefixes WHERE session_id=? ORDER BY boundary', (session,))
    assert prefixes[0]['prompt'] == initial[0]['content']
    assert len(await store.rows('SELECT * FROM compactions WHERE session_id=?', (session,))) == 2
    assert len(prefixes) == 3 and prefixes[1] == prefixes[2]
    assert (await engine.tools.runtime({'op': 'read_message', 'message_id': old_tool, 'limit': 20000}, session, run))['content'] == original


async def test_failed_compaction_transaction_cannot_send_partially_changed_history(engine, store):
    session, _, _, _, _ = await seed_budget_case(engine, store)
    before = await store.history(session)
    received = await install_provider(engine)
    await store.execute("CREATE TRIGGER reject_budget_compaction BEFORE INSERT ON compactions BEGIN SELECT RAISE(ABORT,'synthetic budget transaction failure'); END")
    run = await engine.submit(session, 'Continue after a recoverable failure.')
    await engine.actors[session]
    assert (await store.rows('SELECT status FROM runs WHERE id=?', (run,)))[0]['status'] == 'failed'
    assert received == []
    assert await store.rows('SELECT * FROM context_clips WHERE session_id=?', (session,)) == []
    assert await store.rows('SELECT * FROM compactions WHERE session_id=?', (session,)) == []
    assert (await store.history(session))[:len(before)] == before


async def test_model_switch_during_compaction_applies_only_to_next_request(engine, store, monkeypatch):
    session, profile, _, _, _ = await seed_budget_case(engine, store)
    alternative = replace(profile, id='budget-next', model='next-model', base_model='Next Model', preserve_reasoning=False)
    await store.execute('INSERT INTO model_profiles VALUES (?,?,1,?)', (alternative.id, json.dumps(alternative.snapshot()), now()))
    await store.execute('INSERT INTO settings VALUES (?,?)', ('provider_key:budget-next', 'synthetic-next-key'))
    received = await install_provider(engine)
    compact = engine.tools.compact
    changed = False
    async def compact_then_switch(*args, **kwargs):
        nonlocal changed
        result = await compact(*args, **kwargs)
        if not changed:
            changed = True
            await engine.models.switch(session, alternative.id, 'Use a different model next request.', None)
        return result
    monkeypatch.setattr(engine.tools, 'compact', compact_then_switch)
    first = await engine.submit(session, 'Continue with the frozen request profile.')
    await engine.actors[session]
    assert (await store.rows('SELECT status FROM runs WHERE id=?', (first,)))[0]['status'] == 'complete'
    assert received[0]['model'] == profile.model
    assert profile.base_model in received[0]['messages'][0]['content']
    assert any(item.get('reasoning_content') == 'Preserve this reasoning' for item in received[0]['messages'])
    first_snapshot = (await store.rows('SELECT context_epoch,profile_snapshot FROM requests WHERE run_id=?', (first,)))[0]
    assert first_snapshot['context_epoch'] == 0 and json.loads(first_snapshot['profile_snapshot'])['id'] == profile.id
    second = await engine.submit(session, 'Now use the selected model.')
    await engine.actors[session]
    assert (await store.rows('SELECT status FROM runs WHERE id=?', (second,)))[0]['status'] == 'complete'
    assert received[1]['model'] == alternative.model
    assert alternative.base_model in received[1]['messages'][0]['content']
    assert all('reasoning_content' not in item for item in received[1]['messages'])
    assert (await store.rows('SELECT context_epoch FROM requests WHERE run_id=?', (second,)))[0]['context_epoch'] == 1
