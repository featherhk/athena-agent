"""User intent acceptance checks assert effects, not reassuring response text.

Real model intent routing is separately exercised by scripts/evaluate_requirements.py.
These tests make storage, replay, memory and recovery expectations deterministic.
"""
import asyncio
import json
import pytest

from athena.auth import Auth
from athena.preferences import read_preferences, update_preferences
from athena.profiles import Profile
from athena.registry import initialize_registry
from athena.store import now


async def test_runtime_context_edits_change_model_input_and_restore_originals(engine, store):
    user, _ = await Auth(store).register('context-editor', 'acceptance-test-password')
    session = await store.session(user_id=user['id'])
    old = await store.enqueue(session, 'An obsolete attempt to remove from context')
    await store.claim(session)
    await store.message(session, old, 'assistant', '', tool_calls=[{'id': 'paired-tool', 'type': 'function', 'function': {'name': 'exec', 'arguments': '{}'}}])
    await store.message(session, old, 'tool', 'Original tool result', tool_call_id='paired-tool')
    await store.execute("UPDATE runs SET status='complete' WHERE id=?", (old,))
    active = await store.enqueue(session, 'Please remove that attempt')
    await store.claim(session)
    original = await engine.context(session)
    await engine.tools.execute('runtime', {'op': 'sessions', 'args': {'action': 'exclude_run', 'run_id': old}}, session, active)
    projected = await engine.context(session)
    assert len(projected) == 2 and projected[-1]['content'] == 'Please remove that attempt'
    assert len(await store.history(session)) == 4
    state = await engine.tools.execute('runtime', {'op': 'context'}, session, active)
    assert state['excluded_runs'][0]['run_id'] == old
    await engine.tools.execute('runtime', {'op': 'sessions', 'args': {'action': 'restore_run', 'run_id': old}}, session, active)
    assert await engine.context(session) == original
    assert len(await store.rows('SELECT * FROM compactions WHERE session_id=?', (session,))) == 2


async def test_runtime_other_chat_originals_are_recoverable_and_account_scoped(engine, store):
    user, _ = await Auth(store).register('context-reader', 'acceptance-test-password')
    current = await store.session(user_id=user['id'])
    other = await store.session('Budget', user_id=user['id'])
    message = await store.message(other, None, 'assistant', 'The complete original budget')
    await store.execute('INSERT INTO context_clips VALUES (?,?,?,?)', (other, message, '[clipped]', now()))
    result = await engine.tools.execute('runtime', {'op': 'sessions', 'args': {'action': 'read', 'session_id': other}}, current, None)
    assert result['messages'][0]['content'] == '[clipped]'
    recovered = await engine.tools.execute('runtime', {'op': 'read_message', 'args': {'session_id': other, 'message_id': message}}, current, None)
    assert recovered['content'] == 'The complete original budget'
    stranger, _ = await Auth(store).register('different-reader', 'acceptance-test-password')
    private = await store.session(user_id=stranger['id'])
    with pytest.raises(ValueError, match='not found'):
        await engine.tools.execute('runtime', {'op': 'read_message', 'args': {'session_id': other, 'message_id': message}}, private, None)


async def test_reply_preference_follows_account_but_does_not_rewrite_ui_or_old_context(engine, store):
    user, _ = await Auth(store).register('reader', 'acceptance-test-password')
    session = await store.session(user_id=user['id'])
    run = await store.enqueue(session, 'Keep the interface English, but use concise Japanese replies.')
    await store.claim(session)
    before = await engine.context(session)
    await update_preferences(store, user['id'], {'ui_language': 'en'})
    await engine.tools.runtime({'op': 'remember', 'scope': 'global', 'content': 'Reply briefly in Japanese.'}, session, run)
    assert await engine.context(session) == before  # An appended memory cannot invalidate history.
    second = await store.session(user_id=user['id'])
    await store.enqueue(second, 'Tell me about rainbows.')
    await store.claim(second)
    assert 'Reply briefly in Japanese.' in (await engine.context(second))[-1]['content']
    assert (await read_preferences(store, user['id']))['ui_language'] == 'en'
    other, _ = await Auth(store).register('unrelated', 'acceptance-test-password')
    third = await store.session(user_id=other['id'])
    await store.enqueue(third, 'Hello')
    await store.claim(third)
    assert 'Japanese' not in (await engine.context(third))[-1]['content']


async def test_ui_change_is_a_durable_event_before_later_progress(engine, store):
    user, _ = await Auth(store).register('stylist', 'acceptance-test-password')
    first = await store.session(user_id=user['id'])
    second = await store.session(user_id=user['id'])
    await update_preferences(store, user['id'], {'ui_language': 'zh-Hans', 'ui_font': 'serif', 'ui_palette': 'ocean'})
    await engine.emit(first, 'refresh', {})
    for session in (first, second):
        rows = await store.rows('SELECT * FROM events WHERE session_id=? ORDER BY seq', (session,))
        assert rows[0]['type'] == 'preferences_changed'
        assert json.loads(rows[0]['payload'])['ui_palette'] == 'ocean'
    assert (await read_preferences(store, user['id']))['ui_font'] == 'serif'


async def test_reasoning_and_reply_have_distinct_persisted_states_and_survive_stop(engine, store, monkeypatch):
    reasoning_arrived, reply_arrived, resume = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def provider(client, base, key, payload, on_text, on_reasoning):
        await on_reasoning('Provider supplied reasoning.')
        reasoning_arrived.set()
        await resume.wait()
        await on_text('**A partial answer**')
        reply_arrived.set()
        await asyncio.Event().wait()
    monkeypatch.setattr('athena.engine.complete', provider)
    session = await store.session()
    await engine.submit(session, 'Work on this slowly.')
    await asyncio.wait_for(reasoning_arrived.wait(), 3)
    assert (await store.rows('SELECT phase FROM runs'))[0]['phase'] == 'thinking'
    assert (await store.history(session))[-1]['reasoning'] == 'Provider supplied reasoning.'
    resume.set()
    await asyncio.wait_for(reply_arrived.wait(), 3)
    assert (await store.rows('SELECT phase FROM runs'))[0]['phase'] == 'responding'
    await engine.stop(session)
    message = (await store.history(session))[-1]
    assert message['content'] == '**A partial answer**'
    assert message['reasoning'] == 'Provider supplied reasoning.'
    assert message['status'] == 'interrupted'
    assert (await store.rows('SELECT phase FROM runs'))[0]['phase'] == 'stopped'


async def test_fixed_reset_recovers_bad_model_without_a_model_call_and_keeps_content(engine, store):
    await initialize_registry(store)
    user, _ = await Auth(store).register('resetter', 'acceptance-test-password')
    session = await store.session(user_id=user['id'])
    await engine.models.ensure_default()
    profile = Profile(id='broken', provider='Test', model='missing', base_model='Unavailable test model', api_base='https://invalid.example')
    await store.execute('INSERT INTO model_profiles VALUES (?,?,1,?)', ('broken', json.dumps(profile.snapshot()), now()))
    await store.execute('INSERT INTO settings VALUES (?,?)', ('provider_key:broken', 'fake-key'))
    await engine.models.switch(session, 'broken', 'User asks to try another model', None)
    message = await store.message(session, None, 'user', 'Please keep this message.')
    await update_preferences(store, user['id'], {'ui_palette': 'forest', 'ui_font': 'mono'})
    await engine.reset_user(user['id'])
    selected, epoch = await engine.models.selected(session)
    assert selected.id == 'default' and epoch == 2
    assert (await store.history(session))[0]['id'] == message
    assert (await read_preferences(store, user['id']))['ui_palette'] == 'dream'
    assert not await store.rows('SELECT * FROM requests')
