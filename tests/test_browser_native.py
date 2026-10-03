"""Opt-in native integration: disposable profile and synthetic loopback pages only.

ATHENA_TEST_BROWSER=1 uv run pytest tests/test_browser_native.py -q
No provider calls, personal browser profile or external website is used.
"""
import asyncio
import hashlib
import json
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re
import sys
import threading
from urllib.parse import unquote

import pytest

from athena.auth import Auth
from athena.browser import BrowserHost
from athena.browser_driver import NativeBrowserDriver, BrowserDriverError
from athena.files import initialize_files, _open_file, discover_files, list_files

pytestmark = pytest.mark.skipif(sys.platform != 'darwin' or os.environ.get('ATHENA_TEST_BROWSER') != '1',
    reason='Opt-in local macOS native browser integration')


@pytest.fixture
def page_server():
    lock, receipts = threading.Lock(), []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == '/statement.csv':
                body = b'item,amount\ntest,2\n'
                self.send_response(200)
                self.send_header('Content-Type', 'text/csv')
                self.send_header('Content-Disposition', 'attachment; filename=statement.csv')
            else:
                remembered = 'Remembered account' if 'athena_fixture=1' in self.headers.get('Cookie', '') else 'Guest'
                body = f'''<!doctype html><title>Athena browser fixture</title>
                <h1>{remembered}</h1><label>Name<input id="name"></label>
                <button onclick="fetch('/submit',{{method:'POST',body:document.querySelector('#name').value}}).then(()=>document.querySelector('#result').textContent='Submitted once')">Submit</button>
                <a href="/statement.csv" download>Download statement</a><p id="result">Ready</p>
                <label>Password<input type="password"></label>'''.encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            data = self.rfile.read(int(self.headers['Content-Length'])).decode()
            with lock:
                receipts.append(data)
            self.send_response(200)
            self.send_header('Set-Cookie', 'athena_fixture=1; Max-Age=3600; Path=/; HttpOnly; SameSite=Lax')
            self.send_header('Content-Length', '0')
            self.end_headers()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}/', receipts, lock
    server.shutdown()
    thread.join()
    server.server_close()


def ref(page, name):
    line = next(line for line in page['text'].splitlines() if f'"{name}"' in line and '[ref=' in line)
    return '@' + re.search(r'\[ref=(e\d+)\]', line).group(1)


@pytest.fixture
def upload_page_server():
    lock, receipts = threading.Lock(), []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = '''<!doctype html><meta charset="utf-8"><title>File upload fixture</title>
            <h1>Upload documents</h1><label>Documents<input id="documents" type="file" multiple></label>
            <p id="result">No documents received</p><script>
            document.querySelector('#documents').addEventListener('change', async (event) => {
                const names = [];
                for (const file of event.target.files) {
                    const response = await fetch('/upload', {method:'POST', body:file,
                        headers:{'X-Fixture-Name':encodeURIComponent(file.name)}});
                    if (!response.ok) throw new Error('Upload failed');
                    names.push(file.name + ' (' + file.size + ' bytes)');
                }
                document.querySelector('#result').textContent = 'Received ' + names.join(', ');
            });
            </script>'''.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            data = self.rfile.read(int(self.headers['Content-Length']))
            with lock:
                receipts.append((unquote(self.headers['X-Fixture-Name']), data))
            self.send_response(200)
            self.send_header('Content-Length', '0')
            self.end_headers()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}/', receipts, lock
    server.shutdown()
    thread.join()
    server.server_close()


@pytest.mark.parametrize('lose_reply', [False, True])
async def test_native_upload_exact_files_once_even_when_reply_is_lost(config, store, upload_page_server, monkeypatch, lose_reply):
    root = Path(os.environ.get('ATHENA_TEST_BROWSER_ROOT', Path(__file__).resolve().parents[1]))
    config = replace(config, repo_dir=root.resolve())
    driver = NativeBrowserDriver(config, headed=False)
    assert driver.available(), 'npm ci must install the pinned native driver'
    host = BrowserHost(config, store, driver)
    await initialize_files(store)
    user, _ = await Auth(store).register('native-upload', 'synthetic-password')
    session = await store.session(user_id=user['id'])
    directory = config.data_dir / 'files' / user['id'] / session
    directory.mkdir(parents=True)
    fixtures = {'报价 单.pdf': b'%PDF-1.4\nsynthetic document\n%%EOF\n', '--profile.xlsx': bytes(range(256))}
    for name, data in fixtures.items():
        (directory / name).write_bytes(data)
    await discover_files(config, store, user['id'], session)
    records = await list_files(store, user['id'], session)
    url, receipts, lock = upload_page_server
    dispatches = []
    real_run = driver.run
    async def run(owner, command, **kwargs):
        result = await real_run(owner, command, **kwargs)
        if command[0] == 'upload':
            dispatches.append(command)
            if lose_reply:
                raise BrowserDriverError('browser_reply_lost', 'The response was lost after dispatch.')
        return result
    monkeypatch.setattr(driver, 'run', run)
    try:
        page = await host.operate({'action': 'open', 'url': url}, session, None)
        assert page['ok'], page
        outcome = await host.operate({'action': 'upload', 'tab_id': page['tab_id'],
            'observation_id': page['observation_id'], 'ref': ref(page, 'Documents'),
            'file_ids': [record['id'] for record in records]}, session, None)
        if lose_reply:
            assert not outcome['ok'] and outcome['effect_known'] is False
        else:
            assert outcome['ok'] and outcome['input_selected'] and not outcome['submission_verified'], outcome
            assert {entry['name']: entry['sha256'] for entry in outcome['files']} == {
                name: hashlib.sha256(data).hexdigest() for name, data in fixtures.items()}
        await store.initialize()
        for _ in range(25):
            page = await host.operate({'action': 'snapshot', 'tab_id': page['tab_id']}, session, None)
            assert page['ok'], page
            if 'Received ' in page['text']:
                break
            await asyncio.sleep(0.05)
        assert all(name in page['text'] for name in fixtures), page
        with lock:
            assert len(receipts) == 2 and dict(receipts) == fixtures
        assert len(dispatches) == 1
        staged = [Path(path) for path in dispatches[0][2:]]
        assert all(path.is_file() for path in staged)
        closed = await host.operate({'action': 'close', 'tab_id': page['tab_id'],
            'observation_id': page['observation_id']}, session, None)
        assert closed['ok'] and not any(path.exists() for path in staged)
        assert {name: (directory / name).read_bytes() for name in fixtures} == fixtures
    finally:
        await host.close()


async def test_native_form_download_screenshot_and_persistent_login(config, store, page_server):
    # Exercise the exact same task with a released bundle's native driver, too.
    root = Path(os.environ.get('ATHENA_TEST_BROWSER_ROOT', Path(__file__).resolve().parents[1]))
    config = replace(config, repo_dir=root.resolve())
    driver = NativeBrowserDriver(config, headed=False)
    assert driver.available(), 'npm ci must install the pinned native driver'
    host = BrowserHost(config, store, driver)
    await initialize_files(store)
    user, _ = await Auth(store).register('native-fixture', 'synthetic-password')
    session = await store.session(user_id=user['id'])
    url, receipts, lock = page_server
    page = None

    async def operate(action, **fields):
        nonlocal page
        args = {'action': action, **fields}
        if page and action != 'open':
            args.update(tab_id=page['tab_id'], observation_id=page['observation_id'])
        value = await host.operate(args, session, None)
        assert value.get('ok'), json.dumps(value, ensure_ascii=False)
        page = value
        return value

    try:
        await operate('open', url=url)
        assert 'Guest' in page['text']
        await operate('fill', ref=ref(page, 'Name'), text='--profile is literal\nUnicode: 雅典娜')
        await operate('click', ref=ref(page, 'Submit'))
        for _ in range(15):
            if 'Submitted once' in page['text']:
                break
            await asyncio.sleep(0.05)
            await operate('snapshot')
        assert 'Submitted once' in page['text']
        with lock:
            assert len(receipts) == 1 and receipts[0].startswith('--profile is literal')
        downloaded = await operate('download', ref=ref(page, 'Download statement'), name='statement.csv')
        record = (await store.rows('SELECT * FROM file_entries WHERE id=?', (downloaded['file']['id'],)))[0]
        handle, _ = _open_file(config, record)
        with handle:
            assert handle.read() == b'item,amount\ntest,2\n'
        captured = await operate('screenshot')
        record = (await store.rows('SELECT * FROM file_entries WHERE id=?', (captured['file']['id'],)))[0]
        handle, _ = _open_file(config, record)
        with handle:
            assert handle.read(8) == b'\x89PNG\r\n\x1a\n'
        await driver.close(user['id'])
        page = None
        # Wait for the owned daemon to leave, never repeat a webpage operation.
        for _ in range(30):
            if not await driver.identity(user['id']):
                break
            await asyncio.sleep(0.05)
        await operate('open', url=url)
        assert 'Remembered account' in page['text']
        tabs = await host.operate({'action': 'list'}, session, None)
        assert [tab['status'] for tab in tabs['tabs']] == ['closed', 'open']
        with lock:
            assert len(receipts) == 1
    finally:
        await host.close()
