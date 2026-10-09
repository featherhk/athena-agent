"""Local companion continuity, owned by the authenticated account and DB worker.

Browser origins are deliberately ephemeral on macOS. This bounded, allowlisted
store persists UI intentions, never executes or retries them. A writer lease and
strict sequences fence abandoned WebViews; duplicate saves are idempotent.
"""
import json
import re

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .files import public_file
from .store import now

IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS desktop_resume (
        user_id TEXT PRIMARY KEY REFERENCES users(id), writer TEXT NOT NULL,
        sequence INTEGER NOT NULL DEFAULT 0, entries TEXT NOT NULL DEFAULT '{}',
        last_write TEXT, updated_at TEXT NOT NULL)""")


class Claim(BaseModel):
    model_config = ConfigDict(extra='forbid')
    writer: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')


class Save(Claim):
    sequence: int = Field(ge=1, le=2**53 - 1, strict=True)
    changes: dict[str, str | None] = Field(max_length=256)


def _invalid():
    raise HTTPException(400, 'invalid_input')


def _scope(db, owner, key):
    if key == f'athena-session:{owner}':
        return 'selection', None
    for prefix, kind, length in (('athena-draft:', 'draft', 2),
                                  ('athena-submissions:', 'submissions', 2),
                                  ('stops:athena-submissions:', 'stops', 2),
                                  ('athena-help-draft:', 'help', 3)):
        if key.startswith(prefix):
            try:
                values = json.loads(key[len(prefix):])
            except ValueError:
                _invalid()
            if not isinstance(values, list) or len(values) != length or values[0] != owner:
                _invalid()
            session = values[1]
            if session is None and kind == 'draft':
                return kind, session
            if not isinstance(session, str) or not db.execute('SELECT 1 FROM sessions WHERE id=? AND user_id=?', (session, owner)).fetchone():
                raise HTTPException(404, 'session_not_found')
            if kind == 'help' and (not isinstance(values[2], str) or not IDENTIFIER.fullmatch(values[2])):
                _invalid()
            return kind, session
    _invalid()


def _revision(value):
    return type(value) is int and 0 <= value <= 2**53 - 1


def _files(db, owner, session, files):
    if not isinstance(files, list) or len(files) > 10:
        _invalid()
    canonical = []
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str):
            _invalid()
        row = db.execute('SELECT * FROM file_entries WHERE id=?', (item['id'],)).fetchone()
        if row:
            if row['user_id'] != owner or row['session_id'] != session:
                raise HTTPException(404, 'file_not_found')
            canonical.append(public_file(row))
    return canonical


def _draft(db, owner, session, value):
    if not isinstance(value, dict) or not isinstance(value.get('text'), str) or len(value['text']) > 30000 or not _revision(value.get('revision')):
        _invalid()
    return {'text': value['text'], 'revision': value['revision'], 'attached': _files(db, owner, session, value.get('attached'))}


def validate(db, owner, key, raw):
    kind, session = _scope(db, owner, key)
    if raw is None:
        return None
    if len(raw) > 1_000_000:
        _invalid()
    if kind == 'selection':
        if raw and not db.execute('SELECT 1 FROM sessions WHERE id=? AND user_id=?', (raw, owner)).fetchone():
            raise HTTPException(404, 'session_not_found')
        return raw  # Empty is an explicit new conversation, not a missing choice.
    try:
        value = json.loads(raw)
    except ValueError:
        _invalid()
    if kind == 'draft':
        value = _draft(db, owner, session, value)
    elif kind == 'help':
        if not isinstance(value, dict) or not _revision(value.get('revision')) or not isinstance(value.get('answers'), dict) or len(value['answers']) > 3:
            _invalid()
        if any(not IDENTIFIER.fullmatch(key) or not isinstance(text, str) or len(text) > 12000 for key, text in value['answers'].items()):
            _invalid()
        value = {'revision': value['revision'], 'answers': value['answers']}
    else:
        if not isinstance(value, list) or len(value) > 64:
            _invalid()
        records = []
        for item in value:
            if not isinstance(item, dict) or item.get('owner') != owner or item.get('session') != session or not isinstance(item.get('id'), str) or not IDENTIFIER.fullmatch(item['id']):
                _invalid()
            record = {key: item[key] for key in ('id', 'owner', 'session')}
            if kind == 'submissions':
                if not isinstance(item.get('text'), str) or len(item['text']) > 30000 or item.get('mode') not in ('queue', 'interrupt') or not _revision(item.get('revision')):
                    _invalid()
                ids = item.get('file_ids')
                if not isinstance(ids, list) or len(ids) > 10 or any(not isinstance(i, str) or not IDENTIFIER.fullmatch(i) for i in ids):
                    _invalid()
                # A deleted file never drops the pending operation's immutable ID.
                _files(db, owner, session, [{'id': identifier} for identifier in ids])
                record.update({key: item[key] for key in ('text', 'mode', 'revision', 'file_ids')})
                record['draft'] = None if item.get('draft') is None else _draft(db, owner, session, item['draft'])
                if item.get('preserveDraft') is True:
                    record['preserveDraft'] = True
            records.append(record)
        value = records
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def claim(db, owner, writer):
    row = db.execute('SELECT * FROM desktop_resume WHERE user_id=?', (owner,)).fetchone()
    # Retrying a claim whose response was lost must not reset its save sequence.
    if row and row['writer'] == writer:
        return {'writer': writer, 'sequence': row['sequence'], 'entries': json.loads(row['entries'])}
    entries = json.loads(row['entries']) if row else {}
    # Resolve file metadata from owned records, not stale URLs from another port.
    for key, raw in list(entries.items()):
        try:
            entries[key] = validate(db, owner, key, raw)
        except HTTPException:
            entries.pop(key)
    db.execute('''INSERT INTO desktop_resume(user_id,writer,sequence,entries,updated_at) VALUES(?,?,0,?,?)
        ON CONFLICT(user_id) DO UPDATE SET writer=excluded.writer,sequence=0,entries=excluded.entries,last_write=NULL,updated_at=excluded.updated_at''',
        (owner, writer, json.dumps(entries, ensure_ascii=False), now()))
    return {'writer': writer, 'sequence': 0, 'entries': entries}


def _prune_empty(entries):
    # Retain a blank draft's revision while an uncertain send still references
    # it. Otherwise hundreds of completed chats must not consume the draft quota.
    for key, raw in list(entries.items()):
        if key.startswith(('athena-submissions:', 'stops:athena-submissions:')) and json.loads(raw) == []:
            entries.pop(key)
    for key, raw in list(entries.items()):
        if key.startswith('athena-draft:'):
            draft = json.loads(raw)
            scope = key.removeprefix('athena-draft:')
            if not draft['text'] and not draft['attached'] and not entries.get('athena-submissions:' + scope):
                entries.pop(key)
        elif key.startswith('athena-help-draft:') and not json.loads(raw)['answers']:
            entries.pop(key)


def save(db, owner, body):
    row = db.execute('SELECT * FROM desktop_resume WHERE user_id=?', (owner,)).fetchone()
    if not row or row['writer'] != body.writer:
        raise HTTPException(409, 'desktop_resume_conflict')
    encoded = json.dumps(body.changes, ensure_ascii=False, sort_keys=True)
    if len(encoded.encode()) > 2_000_000:
        _invalid()
    if body.sequence == row['sequence'] and encoded == row['last_write']:
        return {'writer': body.writer, 'sequence': body.sequence}
    if body.sequence != row['sequence'] + 1:
        raise HTTPException(409, 'desktop_resume_conflict')
    entries = json.loads(row['entries'])
    for key, raw in body.changes.items():
        value = validate(db, owner, key, raw)
        if value is None:
            entries.pop(key, None)
        else:
            entries[key] = value
    _prune_empty(entries)
    encoded_entries = json.dumps(entries, ensure_ascii=False)
    if len(entries) > 256 or len(encoded_entries.encode()) > 4_000_000:
        _invalid()
    db.execute('UPDATE desktop_resume SET sequence=?,entries=?,last_write=?,updated_at=? WHERE user_id=?',
               (body.sequence, encoded_entries, encoded, now(), owner))
    return {'writer': body.writer, 'sequence': body.sequence}


def register_routes(app, config):
    def local():
        if config.public_origin:
            raise HTTPException(404, 'unavailable')

    @app.post('/api/desktop/resume/claim')
    async def claim_route(body: Claim, request: Request):
        local()
        return await app.state.store.call(lambda db: claim(db, request.state.user['id'], body.writer))

    @app.post('/api/desktop/resume/save')
    async def save_route(body: Save, request: Request):
        local()
        return await app.state.store.call(lambda db: save(db, request.state.user['id'], body))
