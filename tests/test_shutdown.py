"""An open event stream must not make a local service update hang forever."""
import asyncio
import os
from pathlib import Path
import signal
import socket
import sys

import httpx


async def test_shutdown_drains_open_sse_then_exits(tmp_path):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    env = {key: value for key, value in os.environ.items()
           if key in {'PATH', 'HOME', 'USER', 'LANG', 'TMPDIR', 'SYSTEMROOT', 'COMSPEC'}}
    env.update(ATHENA_DATA_DIR=str(tmp_path / 'external-state'), ATHENA_PORT=str(port))
    process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'athena',
        cwd=Path(__file__).resolve().parents[1] / 'backend', env=env,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    try:
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', timeout=2) as client:
            for _ in range(100):
                try:
                    if (await client.get('/api/health')).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                await asyncio.sleep(.1)
            else:
                raise AssertionError('Isolated service did not start')
            response = await client.post('/api/auth/register', json={'username': 'shutdown-test', 'password': 'synthetic-shutdown-password'})
            assert response.status_code == 200
            response = await client.post('/api/sessions', json={})
            session = response.json()['id']
            async with client.stream('GET', f'/api/sessions/{session}/events') as stream:
                assert stream.status_code == 200
                process.terminate()
                # Uvicorn re-raises the captured signal after completing cleanup.
                assert await asyncio.wait_for(process.wait(), 10) in (0, -signal.SIGTERM)
                logs = await process.stderr.read()
                assert b'Application shutdown complete.' in logs
                assert b'timeout graceful shutdown exceeded' not in logs, logs.decode()
                assert b'ERROR' not in logs and b'Traceback' not in logs, logs.decode()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def test_shutdown_wakes_full_stream_queues_and_preserves_active_work_until_teardown(engine, store, monkeypatch):
    started = asyncio.Event()

    async def provider(client, base, key, payload, on_text, on_reasoning=None):
        await on_text('A partial answer that must survive restart.')
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr('athena.engine.complete', provider)
    session = await store.session()
    run = await engine.submit(session, 'A long-running task.')
    await asyncio.wait_for(started.wait(), 2)
    queue = asyncio.Queue(maxsize=1)
    queue.put_nowait({'notification': 'already durable'})
    engine.listeners[session] = {queue}
    engine.begin_shutdown()
    engine.begin_shutdown()  # A repeated exit signal or lifespan cleanup is harmless.
    assert queue.get_nowait() is None and not engine.listeners
    assert not engine.active_runs[session].done()  # Draining a view is not cancellation.
    await engine.close()
    persisted = (await store.rows('SELECT status FROM runs WHERE id=?', (run,)))[0]
    assert persisted['status'] == 'interrupted'
    assert (await store.history(session))[-1]['content'] == 'A partial answer that must survive restart.'
    assert (await store.history(session))[-1]['status'] == 'interrupted'
    assert (await store.rows('SELECT status FROM requests WHERE run_id=?', (run,)))[0]['status'] == 'interrupted'
    await store.initialize()
    assert await store.claim(session) is None  # An unknown interrupted action is never replayed.
