"""Real owned-file routes and snapshots, using only synthetic local content."""
import os
import struct
import zlib

import pytest

from athena.files import initialize_files, list_files
from test_files import file_client, upload


def png(rgb):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    header = struct.pack('>IIBBBBB', 2, 2, 8, 2, 0, 0, 0)
    # Stored DEFLATE blocks have equal size for different same-length pixels.
    pixels = zlib.compress((b'\0' + bytes(rgb) * 2) * 2, level=0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', pixels) + chunk(b'IEND', b'')


@pytest.mark.parametrize('name,before,after', [
    ('report.md', b'# First\n', b'# Other\n'),
    ('picture.png', png((130, 115, 146)), png((160, 114, 130))),
])
async def test_same_size_write_changes_snapshot_revision_without_changing_file_identity(file_client, config, name, before, after):
    client, app, session = file_client
    user = (await client.get('/api/auth/me')).json()['user']
    workspace = config.data_dir / 'files' / user['id'] / session
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / name
    path.write_bytes(before)
    first = (await client.get(f'/api/sessions/{session}')).json()['files'][0]
    assert first['content_revision'].startswith('s1-')
    assert (await client.get(first['content_url'], params={'revision': first['content_revision']})).content == before
    assert len(before) == len(after)
    original_stat = path.stat()
    path.write_bytes(after)
    # Editors may preserve timestamps. Size and mtime alone must not hide this.
    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert path.stat().st_mtime_ns == original_stat.st_mtime_ns
    # Even before a new snapshot updates the registry, FD-based validation
    # rejects reading the newly changed bytes under the old observed token.
    for url in (first['preview_url'], first['content_url']):
        changed = await client.get(url, params={'revision': first['content_revision']})
        assert changed.status_code == 409 and changed.json()['code'] == 'file_changed'
    second = (await client.get(f'/api/sessions/{session}')).json()['files'][0]
    assert second['id'] == first['id'] and second['size'] == first['size']
    assert second['created_at'] == first['created_at'] and second['source'] == 'output'
    assert second['content_revision'] != first['content_revision']
    assert second['content_url'] == first['content_url']
    actual = await client.get(second['content_url'], params={'revision': second['content_revision']})
    assert actual.status_code == 200 and actual.content == after
    preview = await client.get(second['preview_url'], params={'revision': second['content_revision']})
    assert preview.status_code == 200
    if name.endswith('.md'):
        assert preview.json()['content'] == after.decode()
    else:
        assert preview.json()['kind'] == 'image'
    third = (await client.get(f'/api/sessions/{session}')).json()['files'][0]
    assert third['content_revision'] == second['content_revision'], 'reading must not create a revision'
    assert (await list_files(app.state.store, user['id'], session))[0] == third
    assert (await client.get(second['content_url'], params={'download': 1})).content == after


async def test_atomic_replacement_changes_revision_and_retains_upload_origin(file_client, config):
    client, _, session = file_client
    original = (await upload(client, session, 'draft.md', b'# First')).json()
    user = (await client.get('/api/auth/me')).json()['user']
    path = config.data_dir / 'files' / user['id'] / session / original['path']
    old_stat = path.stat()
    replacement = path.with_name('replacement.md')
    replacement.write_bytes(b'# Other')
    os.utime(replacement, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    os.replace(replacement, path)
    latest = (await client.get(f'/api/sessions/{session}/files')).json()[0]
    assert latest['id'] == original['id'] and latest['path'] == original['path']
    assert latest['source'] == 'upload' and latest['size'] == original['size']
    assert latest['content_revision'] != original['content_revision']
    assert (await client.get(latest['content_url'], params={'revision': latest['content_revision']})).content == b'# Other'


async def test_additive_file_migration_preserves_owned_rows_and_backfills_on_discovery(file_client):
    client, app, session = file_client
    original = (await upload(client, session, 'saved.md', b'# Persistent')).json()
    # Reproduce the previous table, including a real existing registered file.
    await app.state.store.execute('ALTER TABLE file_entries DROP COLUMN content_revision')
    await initialize_files(app.state.store)
    await initialize_files(app.state.store)
    rows = await app.state.store.rows('SELECT * FROM file_entries')
    assert len(rows) == 1 and rows[0]['id'] == original['id']
    assert rows[0]['content_revision'] is None
    assert (await client.get(original['content_url'])).content == b'# Persistent'
    latest = (await client.get(f'/api/sessions/{session}/files')).json()[0]
    assert latest == original
    persisted = (await app.state.store.rows('SELECT content_revision FROM file_entries WHERE id=?', (original['id'],)))[0]
    assert persisted['content_revision'] == original['content_revision']


async def test_discovery_versions_only_stat_metadata_and_numeric_legacy_urls_still_work(file_client, monkeypatch):
    client, _, session = file_client
    original = (await upload(client, session, 'large.md', b'x' * 200_000)).json()
    assert (await client.get(original['content_url'], params={'revision': original['size']})).status_code == 200
    def forbid_content_read(*args, **kwargs):
        raise AssertionError('Discovery must not read every full file to identify changes')
    with monkeypatch.context() as patch:
        patch.setattr('athena.files._open_file', forbid_content_read)
        for _ in range(3):
            listing = (await client.get(f'/api/sessions/{session}/files')).json()
            assert listing == [original]


async def test_conditional_read_does_not_weaken_ownership_or_symlink_guards(file_client, config, tmp_path):
    client, _, session = file_client
    original = (await upload(client, session, 'owned.md', b'owned')).json()
    user = (await client.get('/api/auth/me')).json()['user']
    path = config.data_dir / 'files' / user['id'] / session / original['path']
    outside = tmp_path / 'secret.md'
    outside.write_bytes(b'outside')
    path.unlink()
    path.symlink_to(outside)
    for url in (original['content_url'], original['preview_url']):
        assert (await client.get(url, params={'revision': original['content_revision']})).status_code == 404
    await client.post('/api/auth/logout')
    await client.post('/api/auth/register', json={'username': 'another-revision-owner', 'password': 'test-only-other-password'})
    for url in (original['content_url'], original['preview_url']):
        assert (await client.get(url, params={'revision': original['content_revision']})).status_code == 404
