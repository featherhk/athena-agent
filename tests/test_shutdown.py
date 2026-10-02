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
                assert b'Application shutdown complete.' in await process.stderr.read()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
