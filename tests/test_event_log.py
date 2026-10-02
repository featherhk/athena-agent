"""Audit fidelity and reconnect must survive more efficient event storage."""
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import zlib

import httpx
import pytest

from athena.api import create_app
from athena.admin import archive_events
from athena.event_log import CHECKPOINT_INTERVAL, JSON, PREFIX, ZLIB, archive_legacy_batch, payload_text
from athena.store import Store, now
from athena.streaming import StreamingMessage


def payload(content='', reasoning='', message='message'):
    return {'id': message, 'role': 'assistant', 'content': content, 'reasoning': reasoning,
            'status': 'streaming', 'run_id': 'run'}


async def test_incremental_storage_restores_every_frame_after_arbitrary_cursors(store, monkeypatch):
    session, other = await store.session(), await store.session()
    text = ''.join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(50))
    reasoning, expected = '判断🙂𐍈\n', []
    for index in range(130):
        text += f'结果 {index} ' + hashlib.sha256(str(index + 100).encode()).hexdigest()
        reasoning += f' خطوة {index}。'
        value = payload(text, reasoning)
        expected.append(await store.event(session, 'message', value))
        if index % 11 == 0:
            await store.event(other, 'message', payload('Unrelated account data', message='other'))
    rows = await store.rows('SELECT * FROM events WHERE session_id=? ORDER BY seq', (session,))
    assert any(row['encoding'] == PREFIX for row in rows) and any(row['encoding'] == ZLIB for row in rows)
    raw_size = sum(len(json.dumps(event['payload'], ensure_ascii=False).encode()) for event in expected)
    saved_size = sum(len(row['payload'] if isinstance(row['payload'], bytes) else row['payload'].encode()) for row in rows)
    assert saved_size < raw_size / 10
    for row, event in zip(rows, expected):
        raw = await store.call(lambda db: payload_text(db, row))
        assert raw == json.dumps(event['payload'], ensure_ascii=False)
        steps, base = 0, row['base_seq']
        by_id = {item['seq']: item for item in rows}
        while base:
            steps += 1
            base = by_id[base]['base_seq']
        assert steps < CHECKPOINT_INTERVAL
    # A bounded page can end before its row-count limit; callers follow seq.
    monkeypatch.setattr('athena.event_log.PAGE_CHARS', 25000)
    for start in (0, expected[7]['seq'], expected[71]['seq']):
        restored, cursor = [], start
        while batch := await store.events(session, cursor, limit=17):
            assert all(item['session_id'] == session for item in batch)
            restored.extend(batch)
            cursor = batch[-1]['seq']
        assert restored == [item for item in expected if item['seq'] > start]


async def test_replacements_and_alternating_messages_create_valid_checkpoints(store):
    session = await store.session()
    values = [payload('long content' * 1000, '思考' * 500), payload('shortened', 'replacement'),
              payload('other', message='second'), payload('shortened + continuation', 'replacement')]
    records = [await store.event(session, 'message', value) for value in values]
    assert await store.events(session) == records
    assert (await store.rows('SELECT base_seq FROM events WHERE seq=?', (records[1]['seq'],)))[0]['base_seq'] is None
    # Application/control events keep JSON for the existing transactional SQL readers.
    await store.event(session, 'account_action', {'action': 'change_password'})
    row = (await store.rows("SELECT * FROM events WHERE type='account_action'"))[0]
    assert row['encoding'] == JSON and json.loads(row['payload']) == {'action': 'change_password'}


async def test_legacy_archive_is_resumable_byte_exact_and_does_not_change_originals(store):
    session = await store.session()
    message = await store.message(session, None, 'assistant', 'Untouched original')
    await store.execute('UPDATE messages SET reasoning=? WHERE id=?', ('Original reasoning', message))
    originals = []
    for index in range(9):
        raw = json.dumps(payload('原文 🙂\n' * 500, message=str(index)), ensure_ascii=True, indent=2)
        await store.execute('INSERT INTO events(session_id,type,payload,created_at) VALUES (?,?,?,?)', (session, 'message', raw, now()))
        originals.append(raw)
    before = await store.events(session)
    batch = await store.call(lambda db: archive_legacy_batch(db, 3))
    assert batch['events'] == 3 and batch['after_bytes'] < batch['before_bytes'] / 5
    assert await store.events(session) == before  # Mixed old/new encodings during maintenance.
    await store.initialize()
    while (await store.call(lambda db: archive_legacy_batch(db, 2)))['events']:
        pass
    rows = await store.rows('SELECT * FROM events WHERE session_id=? ORDER BY seq', (session,))
    for row, original in zip(rows, originals):
        assert row['encoding'] == ZLIB
        assert await store.call(lambda db: payload_text(db, row)) == original
    assert await store.events(session) == before
    record = (await store.rows('SELECT * FROM messages WHERE id=?', (message,)))[0]
    assert record['content'] == 'Untouched original' and record['reasoning'] == 'Original reasoning'


async def test_legacy_prefix_archive_resumes_without_changing_a_live_stream_head(store):
    session = await store.session()
    text = ''.join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(50))
    reasoning = '原始思考'
    for index in range(120):
        text += hashlib.sha256(str(index + 50).encode()).hexdigest() + '🙂'
        reasoning += ' ' + str(index)
        raw = json.dumps(payload(text, reasoning), ensure_ascii=False)
        await store.execute('INSERT INTO events(session_id,type,payload,created_at) VALUES (?,?,?,?)', (session, 'message', raw, now()))
    before = await store.events(session)
    # Already-upgraded streams can coexist with unfinished archival of old data.
    current = await store.event(session, 'message', payload(text + ' new', 'new reasoning'))
    head = await store.rows('SELECT * FROM message_event_heads')
    total_before = total_after = 0
    while batch := await store.call(lambda db: archive_legacy_batch(db, 17)):
        total_before += batch['before_bytes']
        total_after += batch['after_bytes']
        if not batch['events']:
            break
    assert total_after < total_before / 8
    assert await store.rows('SELECT * FROM message_event_heads') == head
    rows = await store.rows('SELECT * FROM events ORDER BY seq')
    assert any(row['encoding'] == PREFIX for row in rows)
    assert await store.events(session) == before + [current]
    # Random cursors must not require earlier client state or another batch.
    for row in rows:
        await store.call(lambda db: payload_text(db, row))
    later = await store.event(session, 'message', payload(text + ' new continuation', 'new reasoning continued'))
    assert (await store.events(session, current['seq'])) == [later]


async def test_invalid_checkpoint_cannot_read_another_conversation_or_guess_content(store):
    session, other = await store.session(), await store.session()
    base = await store.event(other, 'message', payload('private', 'private'))
    patch = json.dumps({'content_start': 7, 'reasoning_start': 7, 'value': payload('!', '!')})
    await store.execute('INSERT INTO events(session_id,type,payload,created_at,encoding,base_seq) VALUES (?,?,?,?,?,?)',
                        (session, 'message', patch, now(), PREFIX, base['seq']))
    with pytest.raises(ValueError, match='this conversation'):
        await store.events(session)
    await store.execute("UPDATE events SET encoding='unsupported' WHERE session_id=?", (session,))
    with pytest.raises(ValueError, match='Unsupported'):
        await store.events(session)


async def test_bounded_decoder_rejects_inflation_and_corrupt_bytes(store, monkeypatch):
    session = await store.session()
    await store.execute('INSERT INTO events(session_id,type,payload,created_at,encoding) VALUES (?,?,?,?,?)',
                        (session, 'message', zlib.compress(b'x' * 10000), now(), ZLIB))
    monkeypatch.setattr('athena.event_log.MAX_DECODE_BYTES', 1000)
    with pytest.raises(ValueError, match='oversized'):
        await store.events(session)


async def test_message_and_event_update_are_one_transaction(store):
    session = await store.session()
    message = await store.message(session, None, 'assistant', status='streaming')
    await store.execute("CREATE TRIGGER reject_message_event BEFORE INSERT ON events WHEN NEW.type='message' BEGIN SELECT RAISE(ABORT,'synthetic disk failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='synthetic disk failure'):
        await store.stream_message(session, payload('must not be half-saved', message=message))
    assert (await store.rows('SELECT content FROM messages WHERE id=?', (message,)))[0]['content'] == ''
    assert not await store.rows('SELECT * FROM events')
    assert not await store.rows('SELECT * FROM message_event_heads')


async def test_stream_batches_fast_chunks_but_exposes_live_text_and_flushes_the_tail(store):
    session = await store.session()
    message = await store.message(session, None, 'assistant', status='streaming')
    events, observed = [], asyncio.Event()
    def broadcast(event):
        events.append(event)
        observed.set()
    stream = StreamingMessage(store, session, None, message, broadcast)
    try:
        await stream.append('reasoning', '我在检查。')
        assert events[-1]['payload']['reasoning'] == '我在检查。'
        for _ in range(1000):
            await stream.append('content', '🙂')
        observed.clear()
        if events[-1]['payload']['content'] != '🙂' * 1000:
            await asyncio.wait_for(observed.wait(), 1)
        assert events[-1]['payload']['content'] == '🙂' * 1000
        assert not stream.closed  # A live update before completion, not a final-only reply.
        for _ in range(100):
            await stream.append('reasoning', ' العربية')
    finally:
        await stream.close()
    assert 2 <= len(events) <= 4
    assert events == await store.events(session)
    final = (await store.rows('SELECT content,reasoning FROM messages WHERE id=?', (message,)))[0]
    assert final == {'content': '🙂' * 1000, 'reasoning': '我在检查。' + ' العربية' * 100}


async def test_repeated_cancel_during_final_write_cannot_drop_pending_text(store, monkeypatch):
    session = await store.session()
    message = await store.message(session, None, 'assistant', status='streaming')
    stream = StreamingMessage(store, session, None, message, lambda event: None, interval=3600)
    await stream.append('content', 'first')
    await stream.append('content', ' last bytes')
    entered, release = asyncio.Event(), asyncio.Event()
    write = store.stream_message
    async def delayed(*args):
        entered.set()
        await release.wait()
        return await write(*args)
    monkeypatch.setattr(store, 'stream_message', delayed)
    close = asyncio.create_task(stream.close())
    await asyncio.wait_for(entered.wait(), 1)
    close.cancel()
    await asyncio.sleep(0)
    close.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(close, 1)
    assert stream.worker.done() and not stream.worker.cancelled()
    assert (await store.rows('SELECT content FROM messages WHERE id=?', (message,)))[0]['content'] == 'first last bytes'


@pytest.mark.parametrize('stop', [True, False])
async def test_run_stop_and_provider_failure_flush_both_pending_fields(engine, store, monkeypatch, stop):
    received, fail = asyncio.Event(), asyncio.Event()
    async def provider(client, base, key, data, on_text, on_reasoning):
        await on_reasoning('First thought.')
        await on_text('First answer.')
        await on_text(' The pending answer.')
        await on_reasoning(' The pending thought.')
        received.set()
        await fail.wait()
        raise ConnectionError('Synthetic lost provider connection')
    monkeypatch.setattr('athena.engine.complete', provider)
    session = await store.session()
    await engine.submit(session, 'Keep the partial result if this work stops.')
    await asyncio.wait_for(received.wait(), 3)
    if stop:
        await engine.stop(session)
    else:
        actors = list(engine.actors.values())
        fail.set()
        await asyncio.wait_for(asyncio.gather(*actors), 3)
    final = (await store.history(session))[-1]
    assert final['content'] == 'First answer. The pending answer.'
    assert final['reasoning'] == 'First thought. The pending thought.'
    assert final['status'] == ('interrupted' if stop else 'failed')
    frames = [event['payload'] for event in await store.events(session) if event['type'] == 'message']
    assert frames[-1]['content'] == final['content'] and frames[-1]['reasoning'] == final['reasoning']
    assert not [task for task in asyncio.all_tasks() if task.get_name().startswith('athena-stream-')]


async def test_archive_uses_server_lock_and_preserves_a_restorable_backup(config, monkeypatch, capsys):
    from athena.admin import main
    from athena.lock import InstanceLock
    db_path = config.data_dir / 'athena.sqlite3'
    original = json.dumps(payload('原文🙂' * 1000), indent=2)
    database = Store(db_path)
    await database.initialize()
    session = await database.session()
    await database.execute('INSERT INTO events(session_id,type,payload,created_at) VALUES (?,?,?,?)', (session, 'message', original, now()))
    before = await database.events(session)
    await database.close()
    lock = InstanceLock(config.data_dir / 'instance.lock')
    try:
        with pytest.raises(RuntimeError, match='已在运行'):
            archive_events(config, vacuum=True)
        assert not (config.data_dir / 'backups').exists()
    finally:
        lock.close()
    monkeypatch.setattr('sys.argv', ['athena.admin', 'archive-events', '--vacuum'])
    monkeypatch.setattr('athena.admin.Config.from_env', lambda: config)
    main()
    result = json.loads(capsys.readouterr().out)
    assert result['events'] == 1 and result['vacuumed'] is True
    backup = Path(result['backup'])
    assert backup.stat().st_mode & 0o777 == 0o600
    for path in (db_path, backup):
        recovered = Store(path)
        try:
            await recovered.initialize()
            assert await recovered.events(session) == before
            raw = await recovered.call(lambda db: payload_text(db, db.execute('SELECT * FROM events').fetchone()))
            assert raw == original
        finally:
            await recovered.close()


async def test_sse_reconnect_keeps_public_frames_and_cursor_format(config):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317') as client:
            await client.post('/api/auth/register', json={'username': 'event-reader', 'password': 'synthetic-password'})
            session = (await client.post('/api/sessions', json={})).json()['id']
            originals = []
            text = ''.join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(50))
            for index in range(50):
                text += str(index)
                originals.append(await app.state.store.event(session, 'message', payload(text)))
            start = originals[17]['seq']
            request = asyncio.create_task(client.get(f'/api/sessions/{session}/events?after=1', headers={'Last-Event-ID': str(start)}))
            for _ in range(100):
                if app.state.engine.listeners.get(session):
                    break
                await asyncio.sleep(.01)
            else:
                raise AssertionError('SSE reader did not attach')
            await asyncio.sleep(.05)
            app.state.engine.begin_shutdown()
            response = await asyncio.wait_for(request, 2)
            assert response.status_code == 200
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            assert events == originals[18:]
            assert f'id: {originals[-1]["seq"]}\n' in response.text
