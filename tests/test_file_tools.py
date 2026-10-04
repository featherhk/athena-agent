"""File work must use the UI's semantics and preserve the user's original."""
import asyncio
import hashlib
import json
import os

import httpx
import pytest

from athena.errors import UserError
from athena.file_tools import DELIVERY_NOTE
from athena.files import _preview
from athena.prompt import SYSTEM
from athena.tools import TOOLS
from test_files import file_client, upload
from test_document_preview import document, paragraph
from test_spreadsheet_preview import workbook


async def operation(app, session, **args):
    return await app.state.engine.tools.execute('runtime', {'op': 'files', 'args': args}, session, None)


async def test_runtime_reads_identical_canonical_values_and_returns_existing_file_reference(file_client):
    client, app, session = file_client
    data = workbook('<row r="3"><c r="A3"><v>1234.5</v></c><c r="B3" s="1"><v>0.08</v></c><c r="C3"><f>A3*B3</f></c><c r="D3" t="str"><f>IF(TRUE,"","x")</f><v/></c></row>', formats=['"USD "#,##0.00', '0%'])
    entry = (await upload(client, session, '报价.xlsx', data)).json()
    expected = (await client.get(entry['preview_url'])).json()
    before = await app.state.store.rows('SELECT * FROM file_entries')
    result = await operation(app, session, action='read', file_id=entry['id'], content_revision=entry['content_revision'])
    assert result['preview'] == {**expected, 'sheets': [{**expected['sheets'][0], 'cells': [[
        {**cell, 'address': address} for cell, address in zip(expected['sheets'][0]['cells'][0], ['A3', 'B3', 'C3', 'D3'])]]}]}
    assert (await client.get(entry['preview_url'])).json() == expected
    assert result['file'] == entry
    assert result['delivery'] == DELIVERY_NOTE
    cells = result['preview']['sheets'][0]['cells'][0]
    assert cells[0]['display'] == 'USD 1,234.50' and cells[1]['display'] == '8%'
    assert cells[2]['formula_status'] == 'missing' and cells[3]['formula_status'] == 'saved'
    assert result['preview']['formula_results'] == 'not_recalculated'
    assert result['range']['next_offset'] is None and result['range']['next_column_offset'] is None
    assert await app.state.store.rows('SELECT * FROM file_entries') == before
    assert (await client.get(result['file']['content_url'])).content == data
    assert await app.state.store.components(session) == []


async def test_lightweight_listing_discovers_outputs_once_and_preserves_registered_identity(file_client, config):
    client, app, session = file_client
    user = (await client.get('/api/auth/me')).json()['user']
    directory = config.data_dir / 'files' / user['id'] / session
    directory.mkdir(parents=True)
    path = directory / 'finished.md'
    path.write_text('# Already finished')
    before = path.stat()
    first = await operation(app, session, action='list')
    again = await operation(app, session, action='list')
    assert first == again and first['total'] == 1
    assert first['files'][0]['source'] == 'output'
    assert set(first) == {'files', 'session_id', 'total', 'offset', 'next_offset', 'delivery'}
    assert first['delivery'] == DELIVERY_NOTE
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert len(await app.state.store.rows('SELECT id FROM file_entries')) == 1
    assert list(directory.iterdir()) == [path]


async def test_existing_file_delivery_does_not_forbid_requested_custom_interaction(file_client):
    client, app, session = file_client
    entry = (await upload(client, session, 'notes.md', b'Original notes')).json()
    result = await operation(app, session, action='read', file_id=entry['id'])
    assert 'content_url' in result['delivery'] and 'not a browser render receipt' in result['delivery']
    assert await app.state.store.components(session) == []
    # A later explicit request is still handled by the normal versioned registry;
    # file-delivery guidance has not become a component denylist or tool guard.
    await app.state.store.message(session, None, 'user', '给我加一个能勾选文件检查事项的交互组件。')
    registered = await app.state.engine.tools.execute('update_frontend', {
        'op': 'upsert', 'id': 'requested-file-checklist', 'title': '检查事项',
        'html': '<label><input type="checkbox">已检查金额</label>',
    }, session, None)
    assert registered['version'] == 1
    assert [item['id'] for item in await app.state.store.components(session)] == ['requested-file-checklist']
    assert (await client.get(entry['content_url'])).content == b'Original notes'


async def test_rows_columns_and_sheets_can_be_recovered_without_losing_formula_metadata(file_client):
    client, app, session = file_client
    value = 'a' * 850
    columns = 'ABCDEFGH'
    rows = ''.join('<row r="' + str(row + 7) + '">' + ''.join(
        f'<c r="{column}{row + 7}" t="str"><f>{row}+{index}</f><v>{value}</v></c>' for index, column in enumerate(columns)) + '</row>' for row in range(4))
    data = workbook(rows, sheets=2)
    entry = (await upload(client, session, 'wide.xlsx', data)).json()
    canonical = (await client.get(entry['preview_url'])).json()
    assert not canonical['truncated']
    for sheet_index in range(2):
        restored = []
        for offset in range(4):
            row, column_offset = [], 0
            while column_offset is not None:
                result = await operation(app, session, action='read', file_id=entry['id'],
                    content_revision=entry['content_revision'], sheet_index=sheet_index, offset=offset,
                    limit=1, column_offset=column_offset, column_limit=8)
                selection = result['range']
                assert selection['sheet_index'] == sheet_index
                assert selection['count'] == 1 and selection['column_count'] < 8
                assert selection['offset'] == offset and selection['column_offset'] == column_offset
                assert result['preview']['sheets'][0]['row_numbers'] == [offset + 7]
                row.extend(result['preview']['sheets'][0]['cells'][0])
                column_offset = selection['next_column_offset']
                assert len(json.dumps(result, ensure_ascii=False)) < 19000
            restored.append(row)
        assert [[{key: value for key, value in cell.items() if key != 'address'} for cell in row] for row in restored] == canonical['sheets'][sheet_index]['cells']
        assert [[cell['address'] for cell in row] for row in restored] == [[f'{column}{row + 7}' for column in columns] for row in range(4)]
    page = await operation(app, session, action='read', file_id=entry['id'], limit=4, column_limit=4)
    assert page['range']['count'] < 4 and page['range']['next_offset'] == page['range']['count']
    assert page['preview']['formula_results'] == 'not_recalculated'
    assert (await client.get(entry['content_url'])).content == data


async def test_absolute_addresses_follow_sparse_rows_and_columns_past_z(file_client):
    client, app, session = file_client
    data = workbook('<row r="7"><c r="B7"><v>46299</v></c></row>'
                    '<row r="42"><c r="Z42"><v>26</v></c><c r="AA42"><f>1+1</f></c><c r="AN42"><v>40</v></c></row>')
    entry = (await upload(client, session, 'coordinates.xlsx', data)).json()
    result = await operation(app, session, action='read', file_id=entry['id'], limit=1)
    assert [cell['address'] for cell in result['preview']['sheets'][0]['cells'][0]] == ['A7', 'B7']
    page = await operation(app, session, action='read', file_id=entry['id'], offset=1, limit=1, column_offset=25, column_limit=2)
    cells = page['preview']['sheets'][0]['cells'][0]
    assert [cell['address'] for cell in cells] == ['Z42', 'AA42']
    assert cells[1]['formula_status'] == 'missing' and cells[1]['formula'] == '1+1'
    assert page['range']['next_column_offset'] == 27
    end = await operation(app, session, action='read', file_id=entry['id'], offset=1, limit=1, column_offset=39, column_limit=1)
    assert end['preview']['sheets'][0]['cells'][0][0]['address'] == 'AN42'
    assert end['range']['next_column_offset'] is None
    canonical = (await client.get(entry['preview_url'])).json()
    assert all('address' not in cell for row in canonical['sheets'][0]['cells'] for cell in row)
    assert (await client.get(entry['content_url'])).content == data


async def test_runtime_and_http_resolve_optional_row_indices_or_refuse_conflicts(file_client):
    client, app, session = file_client
    data = workbook('<row><c r="B7"><v>42</v></c></row><row><c><v>next</v></c></row>'
                    '<row r="42"><c><v>later</v></c></row><row><c><v>last</v></c></row>')
    entry = (await upload(client, session, 'implicit-rows.xlsx', data)).json()
    result = await operation(app, session, action='read', file_id=entry['id'], offset=0, limit=4)
    sheet = result['preview']['sheets'][0]
    assert sheet['row_numbers'] == [7, 8, 42, 43]
    assert [[cell['address'] for cell in row] for row in sheet['cells']] == [['A7', 'B7'], ['A8'], ['A42'], ['A43']]
    canonical = (await client.get(entry['preview_url'])).json()
    assert canonical['sheets'][0]['row_numbers'] == [7, 8, 42, 43]
    assert (await client.get(entry['content_url'])).content == data
    conflict = workbook('<row><c r="B7"><v>42</v></c><c r="C8"><v>43</v></c></row>')
    entry = (await upload(client, session, 'conflicting-rows.xlsx', conflict)).json()
    assert (await client.get(entry['preview_url'])).json() == {'kind': 'unsupported'}
    result = await operation(app, session, action='read', file_id=entry['id'])
    assert result['preview'] == {'kind': 'unsupported'} and 'range' not in result
    assert (await client.get(entry['content_url'])).content == conflict


async def test_single_large_cell_keeps_all_cache_metadata_with_an_absolute_bound(file_client):
    client, app, session = file_client
    value = '\\' * 2000
    data = workbook(f'<row><c t="str"><f>{value}</f><v>{value}</v></c></row>')
    entry = (await upload(client, session, 'long.xlsx', data)).json()
    result = await operation(app, session, action='read', file_id=entry['id'])
    cell = result['preview']['sheets'][0]['cells'][0][0]
    assert cell['raw'] == cell['display'] == cell['formula'] == value
    assert cell['formula_status'] == 'saved' and result['range']['count'] == 1
    assert len(json.dumps(result, ensure_ascii=False)) < 66000


async def test_source_preview_truncation_is_not_hidden_by_a_small_page(file_client):
    client, app, session = file_client
    data = workbook(''.join(f'<row r="{index}"><c><v>1</v></c></row>' for index in range(1, 202)))
    entry = (await upload(client, session, 'many.xlsx', data)).json()
    result = await operation(app, session, action='read', file_id=entry['id'], limit=2)
    assert result['preview']['sheets'][0]['truncated']
    assert result['range']['total_rows'] == 200 and result['range']['next_offset'] == 2
    assert (await client.get(entry['content_url'])).content == data


async def test_read_handles_empty_spreadsheet_without_inventing_data(file_client):
    client, app, session = file_client
    entry = (await upload(client, session, 'empty.xlsx', workbook(''))).json()
    result = await operation(app, session, action='read', file_id=entry['id'])
    assert result['preview']['sheets'][0]['cells'] == []
    assert result['range']['total_rows'] == result['range']['total_columns'] == 0
    assert result['range']['next_offset'] is None and result['range']['next_column_offset'] is None


async def test_owned_cross_conversation_read_and_foreign_ids_are_separated(file_client):
    client, app, session = file_client
    own = (await upload(client, session, 'private.md', b'own content')).json()
    second = (await client.post('/api/sessions', json={})).json()['id']
    same = await operation(app, second, action='list', session_id=session)
    assert same['files'][0]['id'] == own['id']
    assert (await operation(app, second, action='read', file_id=own['id']))['preview']['content'] == 'own content'
    await client.post('/api/auth/logout')
    await client.post('/api/auth/register', json={'username': 'foreign-file-reader', 'password': 'synthetic-only-password'})
    foreign = (await client.post('/api/sessions', json={})).json()['id']
    for args in ({'action': 'read', 'file_id': own['id']}, {'action': 'list', 'session_id': session}):
        with pytest.raises(UserError) as error:
            await operation(app, foreign, **args)
        assert error.value.code == 'file_not_found'
        assert 'own content' not in str(error.value)


async def test_same_path_same_size_write_rejects_old_revision_and_reuses_file_id(file_client, config):
    client, app, session = file_client
    entry = (await upload(client, session, 'notes.md', b'Old amount')).json()
    user = (await client.get('/api/auth/me')).json()['user']
    path = config.data_dir / 'files' / user['id'] / session / entry['path']
    before = path.stat()
    path.write_bytes(b'New amount')
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_size == before.st_size and path.stat().st_mtime_ns == before.st_mtime_ns
    with pytest.raises(UserError) as error:
        await operation(app, session, action='read', file_id=entry['id'], content_revision=entry['content_revision'])
    assert error.value.code == 'file_changed'
    current = (await operation(app, session, action='list'))['files'][0]
    assert current['id'] == entry['id'] and current['content_revision'] != entry['content_revision']
    result = await operation(app, session, action='read', file_id=current['id'], content_revision=current['content_revision'])
    assert result['preview']['content'] == 'New amount'
    assert len(await app.state.store.rows('SELECT * FROM file_entries')) == 1


async def test_change_during_preview_is_not_returned_as_a_consistent_read(file_client, config, monkeypatch):
    client, app, session = file_client
    entry = (await upload(client, session, 'race.md', b'first')).json()
    user = (await client.get('/api/auth/me')).json()['user']
    path = config.data_dir / 'files' / user['id'] / session / entry['path']
    def changing(*args):
        result = _preview(*args)
        path.write_bytes(b'other')
        return result
    monkeypatch.setattr('athena.file_tools._preview', changing)
    with pytest.raises(UserError) as error:
        await operation(app, session, action='read', file_id=entry['id'])
    assert error.value.code == 'file_changed'


@pytest.mark.parametrize('link_kind', ['symlink', 'hardlink'])
async def test_read_rejects_file_replaced_by_a_link(file_client, config, tmp_path, link_kind):
    client, app, session = file_client
    entry = (await upload(client, session, 'notes.md', b'original')).json()
    user = (await client.get('/api/auth/me')).json()['user']
    path = config.data_dir / 'files' / user['id'] / session / entry['path']
    outside = tmp_path / 'outside.md'
    outside.write_text('other account')
    path.unlink()
    if link_kind == 'symlink':
        path.symlink_to(outside)
    else:
        os.link(outside, path)
    with pytest.raises(UserError) as error:
        await operation(app, session, action='read', file_id=entry['id'])
    assert error.value.code == 'file_not_found'


async def test_text_and_document_pages_are_explicit_and_originals_recoverable(file_client):
    client, app, session = file_client
    content = '# Notes\n' + '内容' * 10000
    entry = (await upload(client, session, 'notes.md', content.encode())).json()
    restored, offset = '', 0
    while offset is not None:
        result = await operation(app, session, action='read', file_id=entry['id'], offset=offset)
        restored += result['preview']['content']
        assert result['range']['unit'] == 'characters'
        offset = result['range']['next_offset']
    assert restored == content
    data = document(paragraph('First') + paragraph('Second'))
    entry = (await upload(client, session, 'report.docx', data)).json()
    result = await operation(app, session, action='read', file_id=entry['id'], limit=1)
    assert result['preview']['blocks'][0]['text'] == 'First' and result['range']['next_offset'] == 1
    result = await operation(app, session, action='read', file_id=entry['id'], offset=1)
    assert result['preview']['blocks'][0]['text'] == 'Second' and result['range']['next_offset'] is None
    huge = document(paragraph('large block ' * 2000))
    entry = (await upload(client, session, 'large.docx', huge)).json()
    result = await operation(app, session, action='read', file_id=entry['id'])
    assert result['range']['requires_cli'] is True and result['range']['total'] == 1
    assert result['preview']['blocks'] == [] and result['range']['count'] == 0
    assert (await client.get(entry['content_url'])).content == huge


async def test_real_engine_roundtrip_reads_skill_then_file_and_delivers_original_without_components(file_client):
    client, app, session = file_client
    data = workbook('<row><c><f>1+1</f></c><c t="str"><f>IF(TRUE,"","x")</f><v/></c></row>')
    entry = (await upload(client, session, 'original.xlsx', data)).json()
    requests = []
    def provider(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) < 3:
            args = {'op': 'skill', 'args': {'name': 'files'}} if len(requests) == 1 else {'op': 'files', 'args': {'action': 'read', 'file_id': entry['id']}}
            delta = {'tool_calls': [{'index': 0, 'id': f'file-call-{len(requests)}', 'type': 'function',
                'function': {'name': 'runtime', 'arguments': json.dumps(args)}}]}
            finish = 'tool_calls'
        else:
            tool = json.loads([message for message in payload['messages'] if message['role'] == 'tool'][-1]['content'])
            assert tool['preview']['sheets'][0]['cells'][0][0]['formula_status'] == 'missing'
            assert tool['preview']['sheets'][0]['cells'][0][1]['formula_status'] == 'saved'
            delta = {'content': 'A1 未保存计算结果，B1 是保存的空字符串。原文件：[original.xlsx](' + tool['file']['content_url'] + ')'}
            finish = 'stop'
        chunk = {'choices': [{'delta': delta, 'finish_reason': finish}], 'usage': {'prompt_tokens': 100, 'completion_tokens': 20}}
        return httpx.Response(200, text='data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n')
    engine = app.state.engine
    await engine.client.aclose()
    engine.client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    response = await client.post(f'/api/sessions/{session}/messages', json={'text': '检查表里空着的地方，原文件直接发我，不要改界面。', 'file_ids': [entry['id']]})
    assert response.status_code == 200
    await asyncio.wait_for(engine.actors[session], 10)
    run = (await app.state.store.rows('SELECT status,error FROM runs WHERE session_id=?', (session,)))[0]
    assert run == {'status': 'complete', 'error': None}
    assert len(requests) == 3
    assert [tool['function']['name'] for tool in requests[0]['tools']] == [tool['function']['name'] for tool in TOOLS]
    assert 'formula_status' not in requests[0]['messages'][0]['content']
    assert 'files:' in requests[0]['messages'][0]['content']
    history = await app.state.store.history(session)
    assert '未保存计算结果' in history[-1]['content'] and entry['content_url'] in history[-1]['content']
    assert await app.state.store.components(session) == []
    assert await app.state.store.rows('SELECT * FROM app_actions') == []
    assert len(await app.state.store.rows('SELECT * FROM file_entries')) == 1
    assert hashlib.sha256((await client.get(entry['content_url'])).content).digest() == hashlib.sha256(data).digest()


async def test_new_skill_body_is_lazy_and_old_session_prefix_is_not_rewritten(file_client, monkeypatch):
    _, app, session = file_client
    engine = app.state.engine
    with monkeypatch.context() as patch:
        patch.setattr('athena.engine.system_prompt', lambda *_: 'Previously persisted system prefix.')
        previous = await engine.context(session)
    assert (await engine.context(session))[0]['content'] == previous[0]['content']
    skill = await engine.tools.execute('runtime', {'op': 'skill', 'args': {'name': 'files'}}, session, None)
    resource = await engine.tools.execute('runtime', {'op': 'skill', 'args': {'name': 'files', 'path': 'references/reading.md'}}, session, None)
    assert 'formula_status' in skill['content'] and 'next_column_offset' in resource['content']
    assert 'formula_status' not in SYSTEM and 'next_column_offset' not in SYSTEM
    assert (await engine.context(session))[0]['content'] == previous[0]['content']
