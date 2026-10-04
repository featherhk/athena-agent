import asyncio
import io
import os
import zipfile

import httpx
import pytest
from fastapi import HTTPException

from athena.api import create_app
from athena.files import (
    MAX_TEXT_BYTES, attachment_info, initialize_files, list_session_files,
    register_file_routes,
)


@pytest.fixture
async def file_client(config):
    app = create_app(config)
    if not any(getattr(route, "path", "") == "/api/files/{file_id}/content" for route in app.routes):
        register_file_routes(app)
    async with app.router.lifespan_context(app):
        await initialize_files(app.state.store)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:4317") as client:
            await client.post("/api/auth/register", json={"username": "file-owner", "password": "test-only-file-password"})
            session = (await client.post("/api/sessions", json={})).json()["id"]
            yield client, app, session


async def upload(client, session, name="notes.md", content=b"# Notes\n\n**Hello**"):
    return await client.post(f"/api/sessions/{session}/files", params={"name": name}, content=content)


async def test_upload_preview_download_and_owned_attachment(file_client, config):
    client, app, session = file_client
    result = await upload(client, session, "你好.md")
    assert result.status_code == 201
    record = result.json()
    assert record["name"] == "你好.md" and record["source"] == "upload"
    listing = (await client.get(f"/api/sessions/{session}/files")).json()
    assert [item["id"] for item in listing] == [record["id"]]
    content = await client.get(record["content_url"])
    assert content.content == b"# Notes\n\n**Hello**"
    assert content.headers["x-content-type-options"] == "nosniff"
    preview = (await client.get(record["preview_url"])).json()
    assert preview == {"kind": "markdown", "content": "# Notes\n\n**Hello**", "truncated": False}
    download = await client.get(record["content_url"], params={"download": 1})
    assert download.headers["content-disposition"].startswith("attachment;")
    user = (await client.get('/api/auth/me')).json()['user']
    attachment = (await attachment_info(app.state.store, config, user["id"], session, [record["id"]]))[0]
    assert attachment["workspace_path"].startswith(str(config.data_dir / "files" / user["id"] / session))
    assert "workspace_path" not in record
    with pytest.raises(HTTPException):
        await attachment_info(app.state.store, config, user["id"], session, [record["id"], record["id"]])


async def test_submit_accepts_owned_files_and_rejects_forged_ids(file_client, monkeypatch):
    client, app, session = file_client
    record = (await upload(client, session)).json()
    submitted = []
    async def capture(session_id, text, mode, attachments, *, frontend_client_id=None, frontend_auth_hash=None, _admission_epoch=None):
        assert frontend_client_id is None and frontend_auth_hash is None
        assert _admission_epoch == app.state.engine.submission_epoch(session_id)
        submitted.append((session_id, text, attachments))
        return 'captured-run'
    monkeypatch.setattr(app.state.engine, 'submit', capture)
    accepted = await client.post(f'/api/sessions/{session}/messages', json={'text': '', 'file_ids': [record['id']]})
    assert accepted.status_code == 200
    assert submitted[0][2][0]['id'] == record['id']
    assert submitted[0][2][0]['workspace_path'].endswith('/' + record['path'])
    rejected = await client.post(f'/api/sessions/{session}/messages', json={'text': 'Read it', 'file_ids': ['unknown-file-id']})
    assert rejected.status_code == 404
    assert len(submitted) == 1


async def test_files_require_ownership_and_login(file_client):
    client, app, session = file_client
    record = (await upload(client, session)).json()
    other_session = (await client.post("/api/sessions", json={})).json()["id"]
    user = (await client.get('/api/auth/me')).json()['user']
    with pytest.raises(HTTPException):
        await attachment_info(app.state.store, app.state.engine.config, user['id'], other_session, [record['id']])
    await client.post("/api/auth/logout")
    assert (await client.get(record["content_url"])).status_code == 401
    assert (await client.get(record["preview_url"])).status_code == 401
    await client.post("/api/auth/register", json={"username": "different-owner", "password": "test-only-other-password"})
    assert (await client.get(record["content_url"])).status_code == 404
    assert (await client.get(record["preview_url"])).status_code == 404
    assert (await client.get(f"/api/sessions/{session}/files")).status_code == 404
    assert (await upload(client, session)).status_code == 404
    assert (await client.post('/api/sessions/' + other_session + '/messages', json={"text": "read", "attachments": [record['id']]})).status_code == 404


@pytest.mark.parametrize("name", ["../secret.txt", "/tmp/test.txt", "a\\b.txt", ".", "..", ".hidden", "a\x00b.txt", "line\nname.txt"])
async def test_invalid_upload_names_cannot_traverse(file_client, name):
    client, app, session = file_client
    result = await upload(client, session, name)
    assert result.status_code == 400
    assert (await app.state.store.rows("SELECT * FROM file_entries")) == []


async def test_upload_limit_removes_partial_file(file_client, monkeypatch, config):
    client, app, session = file_client
    monkeypatch.setattr("athena.files.MAX_FILE_BYTES", 8)
    response = await upload(client, session, "big.txt", b"123456789")
    assert response.status_code == 413
    async def chunks():
        yield b"1234"
        yield b"56789"
    streamed = await client.post(f"/api/sessions/{session}/files", params={"name": "chunked.txt"}, content=chunks())
    assert streamed.status_code == 413
    assert await app.state.store.rows("SELECT * FROM file_entries") == []
    assert not [path for path in (config.data_dir / "files").rglob("*") if path.is_file()]


async def test_html_is_data_and_download_is_sandboxed(file_client):
    client, _, session = file_client
    payload = b'<script>fetch("/api/auth/logout",{method:"POST"})</script><h1>Report</h1>'
    record = (await upload(client, session, "report.html", payload)).json()
    preview = await client.get(record["preview_url"])
    assert preview.json()["kind"] == "html"
    assert preview.headers["content-type"].startswith("application/json")
    content = await client.get(record["content_url"])
    assert content.headers["content-disposition"].startswith("attachment;")
    assert content.headers["content-type"].startswith("text/plain")
    assert "sandbox; default-src 'none'" in content.headers["content-security-policy"]
    assert (await client.get('/api/auth/me')).json()['user']


async def test_generated_file_discovery_ignores_symlinks_and_hardlinks(file_client, config, tmp_path):
    client, app, session = file_client
    user = (await client.get('/api/auth/me')).json()['user']
    workspace = config.data_dir / "files" / user['id'] / session
    workspace.mkdir(parents=True)
    (workspace / "report.md").write_text("# Generated")
    outside = tmp_path / "outside.txt"
    outside.write_text("Private material")
    (workspace / "leak.txt").symlink_to(outside)
    (workspace / "outside-dir").symlink_to(tmp_path, target_is_directory=True)
    os.link(outside, workspace / "hardlink.txt")
    records = await list_session_files(app.state.store, config, user['id'], session)
    assert [record['name'] for record in records] == ['report.md']
    assert records[0]['source'] == 'output'
    (workspace / "report.md").unlink()
    (workspace / "report.md").symlink_to(outside)
    assert (await client.get(records[0]['content_url'])).status_code == 404
    assert (await client.get(records[0]['preview_url'])).status_code == 404


async def test_generated_png_is_an_owned_inline_result_without_workspace_or_navigation(file_client, config):
    import struct
    import zlib
    client, app, session = file_client
    # A complete 2 x 2 RGB PNG, built from standard chunks without image-library
    # or network dependencies. This is test data, never a user artifact.
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 2, 8, 2, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress(b'\x00' + b'\x8f\x7b\x9f' * 2 + b'\x00' + b'\xd6\xc6\xbf' * 2)) + chunk(b'IEND', b'')
    uploaded = (await upload(client, session, 'input.png', png)).json()
    user = (await client.get('/api/auth/me')).json()['user']
    workspace = config.data_dir / 'files' / user['id'] / session
    (workspace / 'finished.png').write_bytes(png)
    snapshot = (await client.get(f'/api/sessions/{session}')).json()
    generated = next(item for item in snapshot['files'] if item['name'] == 'finished.png')
    assert generated['source'] == 'output' and generated['media_type'] == 'image/png'
    assert next(item for item in snapshot['files'] if item['id'] == uploaded['id'])['source'] == 'upload'
    assert snapshot['components'] == [] and snapshot['app_actions'] == []
    preview = (await client.get(generated['preview_url'])).json()
    assert preview == {'kind': 'image', 'url': generated['content_url']}
    content = await client.get(generated['content_url'], params={'revision': len(png)})
    assert content.status_code == 200 and content.content == png
    assert content.headers['content-type'].startswith('image/png')
    assert content.headers['content-disposition'].startswith('inline;')
    assert content.headers['cache-control'] == 'no-store'
    download = await client.get(generated['content_url'], params={'download': 1})
    assert download.content == png and download.headers['content-disposition'].startswith('attachment;')


async def test_symlink_workspace_blocks_upload(file_client, config, tmp_path):
    client, _, session = file_client
    user = (await client.get('/api/auth/me')).json()['user']
    root = config.data_dir / "files" / user['id']
    root.mkdir()
    (root / session).symlink_to(tmp_path, target_is_directory=True)
    response = await upload(client, session)
    assert response.status_code == 400
    assert not (tmp_path / "uploads").exists()


def workbook(sheet, extra=None):
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('xl/workbook.xml', '<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Example" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', sheet)
        for name, value in (extra or {}).items():
            archive.writestr(name, value)
    return result.getvalue()


async def test_xlsx_preview_displays_cells_without_formula_execution(file_client):
    client, _, session = file_client
    data = workbook('<worksheet><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Revenue</t></is></c><c r="C1"><v>42</v></c><c r="D1"><f>1+2</f><v>3</v></c></row><row r="2"><c r="A2" t="s"><v>0</v></c></row></sheetData></worksheet>', {'xl/sharedStrings.xml': '<sst><si><t>Shared value</t></si></sst>'})
    record = (await upload(client, session, 'report.xlsx', data)).json()
    preview = (await client.get(record['preview_url'])).json()
    assert preview['kind'] == 'spreadsheet'
    assert preview['sheets'][0]['rows'] == [['Revenue', '', '42', '3'], ['Shared value']]
    assert (await client.get(record['content_url'])).content == data


async def test_xml_entities_and_zip_bombs_are_not_parsed(file_client):
    client, _, session = file_client
    for data in [workbook('<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><worksheet>&e;</worksheet>'), workbook('a' * (33 * 1024 * 1024)), b'not a zip']:
        record = (await upload(client, session, 'bad.xlsx', data)).json()
        assert (await client.get(record['preview_url'])).json() == {'kind': 'unsupported'}


async def test_text_preview_is_bounded_and_original_is_downloadable(file_client):
    client, _, session = file_client
    text = b'x' * (MAX_TEXT_BYTES + 10)
    record = (await upload(client, session, 'large.txt', text)).json()
    preview = (await client.get(record['preview_url'])).json()
    assert preview['truncated'] and len(preview['content']) == MAX_TEXT_BYTES
    assert (await client.get(record['content_url'])).content == text


async def test_concurrent_same_name_uploads_keep_independent_originals(file_client):
    client, _, session = file_client
    responses = await asyncio.gather(*(upload(client, session, 'same.md', f'Original {index}'.encode()) for index in range(8)))
    assert all(response.status_code == 201 for response in responses)
    records = [response.json() for response in responses]
    assert len({record['id'] for record in records}) == 8
    assert len({record['path'] for record in records}) == 8
    for index, record in enumerate(records):
        assert (await client.get(record['content_url'])).text == f'Original {index}'


@pytest.mark.parametrize('registration_fails', [False, True])
async def test_discovery_during_upload_registration_keeps_upload_authoritative(file_client, monkeypatch, config, registration_fails):
    client, app, session = file_client
    user = (await client.get('/api/auth/me')).json()['user']
    published = asyncio.Event()
    proceed = asyncio.Event()
    original_execute = app.state.store.execute

    async def paused_execute(sql, args=()):
        if sql.startswith('INSERT INTO file_entries(') and args[7] == 'upload':
            published.set()
            await proceed.wait()
            if registration_fails:
                raise RuntimeError('registration failed')
        return await original_execute(sql, args)

    monkeypatch.setattr(app.state.store, 'execute', paused_execute)
    payload = b'The original uploaded bytes'
    pending = asyncio.create_task(upload(client, session, 'original.md', payload))
    try:
        await asyncio.wait_for(published.wait(), timeout=5)
        # The final filename exists, but the upload transaction has not registered
        # it yet. A concurrent snapshot/list must not adopt it as generated output.
        listing = await client.get(f'/api/sessions/{session}/files')
        assert listing.status_code == 200 and listing.json() == []
        assert await app.state.store.rows('SELECT * FROM file_entries') == []
    finally:
        proceed.set()
        if registration_fails:
            with pytest.raises(RuntimeError, match='registration failed'):
                await pending
        else:
            response = await pending

    if registration_fails:
        assert await app.state.store.rows('SELECT * FROM file_entries') == []
        assert not [path for path in (config.data_dir / 'files' / user['id'] / session).rglob('*') if path.is_file()]
        return
    assert response.status_code == 201
    record = response.json()
    assert record['source'] == 'upload'
    rows = await app.state.store.rows('SELECT * FROM file_entries')
    assert len(rows) == 1 and rows[0]['id'] == record['id'] and rows[0]['source'] == 'upload'
    listing = (await client.get(f'/api/sessions/{session}/files')).json()
    assert len(listing) == 1 and listing[0]['id'] == record['id']
    assert (await client.get(record['content_url'])).content == payload


async def test_file_registry_and_original_survive_restart(config):
    cookies = None
    record = None
    for iteration in range(2):
        app = create_app(config)
        if not any(getattr(route, 'path', '') == '/api/files/{file_id}/content' for route in app.routes):
            register_file_routes(app)
        async with app.router.lifespan_context(app):
            await initialize_files(app.state.store)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://127.0.0.1:4317', cookies=cookies) as client:
                if iteration == 0:
                    await client.post('/api/auth/register', json={'username': 'persistent-files', 'password': 'test-only-password'})
                    session = (await client.post('/api/sessions', json={})).json()['id']
                    record = (await upload(client, session, 'persistent.md', b'# Keep me')).json()
                    cookies = client.cookies
                else:
                    assert (await client.get(record['content_url'])).text == '# Keep me'
                    listing = (await client.get(f'/api/sessions/{session}/files')).json()
                    assert listing[0]['id'] == record['id']
                    assert listing[0]['source'] == 'upload'
                    assert listing[0]['content_revision'] == record['content_revision']


async def test_replaced_ancestor_directory_cannot_escape_owned_area(file_client, config, tmp_path):
    client, _, session = file_client
    record = (await upload(client, session, 'private.md', b'Original')).json()
    user = (await client.get('/api/auth/me')).json()['user']
    workspace = config.data_dir / 'files' / user['id'] / session
    uploads = workspace / 'uploads'
    uploads.rename(workspace / 'former-uploads')
    (tmp_path / record['id']).mkdir()
    (tmp_path / record['id'] / 'private.md').write_text('Outside data')
    uploads.symlink_to(tmp_path, target_is_directory=True)
    assert (await client.get(record['content_url'])).status_code == 404
    assert (await client.get(record['preview_url'])).status_code == 404
    assert (await upload(client, session)).status_code == 400
