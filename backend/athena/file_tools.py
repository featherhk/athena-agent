"""Model-readable file views from the same owned previews as the user interface.

No file is copied or rewritten. Pages retain cell semantics rather than sending
the entire UI preview into the model context. SQLite and disk keep their owners.
"""
import asyncio
import json
import os
import re

from fastapi import HTTPException

from .errors import UserError
from .files import (
    _content_revision, _open_file, _preview, initialize_files,
    list_session_files, public_file,
)

PAGE_CHARS = 16000
IDENTIFIER = re.compile(r'[A-Za-z0-9_-]{1,80}\Z')
REVISION = re.compile(r's1-[a-f0-9]{64}\Z')
DELIVERY_NOTE = (
    'Already registered: deliver a Markdown link using the returned name and content_url. '
    'The default conversation provides inline output-file previews/downloads; ordinary file display needs no duplicate component. '
    'Custom interaction remains available when requested. This describes presentation behavior, not a browser render receipt.'
)


def _integer(args, name, default, minimum, maximum):
    value = args.get(name, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer from {minimum} to {maximum}.')
    return value


def _identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError('Use an existing file or conversation identifier.')
    return value


def _size(value):
    return len(json.dumps(value, ensure_ascii=False))


def _column_name(index):
    label, value = '', index + 1
    while value:
        value, digit = divmod(value - 1, 26)
        label = chr(ord('A') + digit) + label
    return label


def _spreadsheet_page(preview, args):
    index = _integer(args, 'sheet_index', 0, 0, 5)
    sheets = preview['sheets']
    if index >= len(sheets):
        if index == 0 and not sheets:
            return preview, {'sheet_index': 0, 'sheets': [], 'total_rows': 0, 'total_columns': 0,
                             'offset': 0, 'count': 0, 'next_offset': None, 'next_column_offset': None}
        raise ValueError('sheet_index is outside the available preview sheets.')
    offset = _integer(args, 'offset', 0, 0, 1000000)
    limit = _integer(args, 'limit', 20, 1, 100)
    column = _integer(args, 'column_offset', 0, 0, 39)
    width = _integer(args, 'column_limit', 12, 1, 40)
    sheet = sheets[index]
    total_columns = max((len(row) for row in sheet['cells']), default=0)
    if total_columns and column >= total_columns:
        raise ValueError('column_offset is outside the available preview columns.')
    width = min(width, max(0, total_columns - column))
    available = sheet['cells'][offset:offset + limit]

    def slice_row(row, row_number):
        # Add absolute coordinates only to the model view, without modifying the
        # canonical UI preview or asking the model to count sparse/paged arrays.
        cells = [{**cell, 'address': f'{_column_name(column + index)}{row_number}'}
                 for index, cell in enumerate(row[column:column + width])]
        return cells, [cell['display'] for cell in cells]

    # Reduce the rectangle before dropping any requested cell metadata. The
    # caller receives exact next row/column offsets and can read the remainder.
    if available:
        while width > 1 and _size(slice_row(available[0], sheet['row_numbers'][offset])) > PAGE_CHARS:
            width -= 1
    rows, cells, used = [], [], 0
    for row_index, row in enumerate(available):
        cell_row, values = slice_row(row, sheet['row_numbers'][offset + row_index])
        cost = _size((cell_row, values))
        if cells and used + cost > PAGE_CHARS:
            break
        # One cell can exceed the target because JSON escaping expands text.
        # Its canonical 2,000-character fields still impose an absolute bound;
        # keep its formula/cache metadata intact rather than clipping it again.
        used += cost
        cells.append(cell_row); rows.append(values)
    count = len(rows)
    page = {**preview, 'sheets': [{**sheet, 'cells': cells, 'rows': rows,
                                  'row_numbers': sheet['row_numbers'][offset:offset + count]}]}
    selection = {'sheet_index': index,
                 'sheets': [{'index': i, 'name': item['name'], 'truncated': item['truncated']} for i, item in enumerate(sheets)],
                 'offset': offset, 'count': count, 'column_offset': column, 'column_count': width,
                 'total_rows': len(sheet['cells']), 'total_columns': total_columns,
                 'next_offset': offset + count if offset + count < len(sheet['cells']) else None,
                 'next_column_offset': column + width if column + width < total_columns else None}
    return page, selection


def _page(preview, args):
    kind = preview['kind']
    if kind == 'spreadsheet':
        return _spreadsheet_page(preview, args)
    if any(key in args for key in ('sheet_index', 'column_offset', 'column_limit')):
        raise ValueError('Sheet and column selection applies only to spreadsheet previews.')
    offset = _integer(args, 'offset', 0, 0, 1000000)
    if kind in ('markdown', 'html', 'text'):
        limit = _integer(args, 'limit', 8000, 1, 12000)
        content = preview['content'][offset:offset + limit]
        return {**preview, 'content': content}, {'unit': 'characters', 'offset': offset,
            'count': len(content), 'total': len(preview['content']),
            'next_offset': offset + len(content) if offset + len(content) < len(preview['content']) else None}
    if kind == 'document':
        limit = _integer(args, 'limit', 20, 1, 100)
        blocks, used = [], 0
        for block in preview['blocks'][offset:offset + limit]:
            cost = _size(block)
            if used + cost > PAGE_CHARS:
                break
            blocks.append(block); used += cost
        too_large = not blocks and offset < len(preview['blocks'])
        return {**preview, 'blocks': blocks}, {'unit': 'blocks', 'offset': offset,
            'count': len(blocks), 'total': len(preview['blocks']),
            'next_offset': offset + len(blocks) if blocks and offset + len(blocks) < len(preview['blocks']) else None,
            'requires_cli': too_large,
            **({'reason': 'This document block exceeds the model page budget; inspect the original file with a bounded script.'} if too_large else {})}
    if 'offset' in args or 'limit' in args:
        raise ValueError('This preview has no pageable text or cells.')
    return preview, None


def _read(config, record, args):
    expected = args.get('content_revision')
    if expected is not None and (not isinstance(expected, str) or not REVISION.fullmatch(expected)):
        raise ValueError('content_revision must be the observed s1- revision from the file entry.')
    # Get the current token for legacy records too. A supplied token is a strict
    # condition, not a cache hint. No-follow reads guard both pathname and inode.
    handle, size = _open_file(config, record, expected)
    with handle:
        revision = _content_revision(os.fstat(handle.fileno()))
    preview = _preview(config, record, revision)
    handle, _ = _open_file(config, record, revision)
    handle.close()
    page, selection = _page(preview, args)
    result = {'file': public_file({**record, 'size': size, 'content_revision': revision}),
              'preview': page, 'delivery': DELIVERY_NOTE}
    if selection is not None:
        result['range'] = selection
    return result


async def file_operation(store, config, session_id, args):
    action = args.get('action', 'list')
    allowed = {'op', 'action', 'offset', 'limit'} | ({'session_id'} if action == 'list' else
        {'file_id', 'content_revision', 'sheet_index', 'column_offset', 'column_limit'} if action == 'read' else set())
    if action not in ('list', 'read') or set(args) - allowed:
        raise ValueError('Files support read-only list and read; read the files skill for exact arguments.')
    await initialize_files(store)
    owner = await store.rows('SELECT user_id FROM sessions WHERE id=?', (session_id,))
    if not owner or not owner[0]['user_id']:
        raise UserError('not_found', 'This conversation was not found.')
    user_id = owner[0]['user_id']
    try:
        if action == 'list':
            target = _identifier(args.get('session_id', session_id))
            offset = _integer(args, 'offset', 0, 0, 1000000)
            limit = _integer(args, 'limit', 50, 1, 200)
            files = await list_session_files(store, config, user_id, target)
            return {'session_id': target, 'files': files[offset:offset + limit], 'total': len(files), 'delivery': DELIVERY_NOTE,
                    'offset': offset, 'next_offset': offset + limit if offset + limit < len(files) else None}
        identifier = _identifier(args.get('file_id'))
        records = await store.rows('''SELECT f.* FROM file_entries f JOIN sessions s ON s.id=f.session_id
            WHERE f.id=? AND f.user_id=? AND s.user_id=?''', (identifier, user_id, user_id))
        if not records:
            raise UserError('file_not_found', 'This file was not found in your account.')
        return await asyncio.to_thread(_read, config, records[0], args)
    except HTTPException as exc:
        if exc.status_code == 409:
            raise UserError('file_changed', 'The file changed during or since the observed read. List files and read its new revision before drawing conclusions.') from None
        raise UserError('file_not_found', 'This file or conversation is unavailable in your account.') from None
