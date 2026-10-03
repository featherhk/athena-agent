"""Bounded, authenticated semantic observations of Athena's interaction surface.

This is a last-observation cache, not a DOM recorder or a source of instructions.
All mutation and account checks execute on Store's single SQLite worker.
"""
from datetime import datetime, timedelta, timezone
import json
from typing import Annotated, Literal

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from .auth import COOKIE, token_hash
from .errors import UserError
from .store import now

TTL_SECONDS = 75
MAX_CLIENTS = 24
Identifier = Annotated[str, Field(min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$')]
View = Literal['menu', 'history', 'files', 'account', 'appearance', 'developer', 'password', 'workspace', 'preview', 'surfaces', 'authentication']
Placement = Literal['inline', 'workspace', 'banner', 'top-start', 'top-end', 'bottom-start', 'bottom-end', 'overlay', 'fullscreen', 'presence', 'sidebar-start', 'sidebar-end']
ComponentError = Literal['script_error', 'unhandled_rejection', 'blocked_resource', 'empty_document', 'load_timeout']


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Viewport(Strict):
    width: int = Field(ge=0, le=16384)
    height: int = Field(ge=0, le=16384)


class Rect(Strict):
    x: int = Field(ge=-32768, le=32768)
    y: int = Field(ge=-32768, le=32768)
    width: int = Field(ge=0, le=32768)
    height: int = Field(ge=0, le=32768)


class Preview(Strict):
    file_id: Identifier
    status: Literal['loading', 'ready', 'failed']
    error_code: Literal['preview_unavailable'] | None = None


class Workspace(Strict):
    id: Identifier
    version: int = Field(ge=1)


class Component(Workspace):
    view_revision: int = Field(ge=0)
    instance: Identifier | None = None
    placement: Placement
    rect: Rect
    visible: bool
    state: Literal['unobserved', 'mounted', 'failed']
    error_code: ComponentError | None = None


class Waiting(Strict):
    kind: Literal['user_help', 'file_selection', 'upload', 'authentication']
    id: Identifier | None = None


class Error(Strict):
    source: Literal['connection', 'ui', 'preview', 'component']
    code: Literal['ui_error', 'connection_lost', 'preview_unavailable', 'script_error', 'unhandled_rejection', 'blocked_resource', 'empty_document', 'load_timeout']


class FrontendObservation(Strict):
    client_id: Identifier
    revision: int = Field(ge=1, le=9007199254740991)
    session_id: Identifier | None
    foreground: bool
    viewport: Viewport
    open_views: list[View] = Field(default_factory=list, max_length=12)
    preview: Preview | None = None
    workspace: Workspace | None = None
    components: list[Component] = Field(default_factory=list, max_length=32)
    waiting: list[Waiting] = Field(default_factory=list, max_length=8)
    errors: list[Error] = Field(default_factory=list, max_length=8)
    has_unsent_changes: bool = False
    attachment_ids: list[Identifier] = Field(default_factory=list, max_length=10)


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS frontend_clients (
        client_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
        auth_hash TEXT NOT NULL, session_id TEXT REFERENCES sessions(id),
        revision INTEGER NOT NULL, state TEXT NOT NULL, observed_at TEXT NOT NULL,
        expires_at TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS frontend_revocations (client_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS frontend_clients_owner ON frontend_clients(user_id,expires_at)')


def _authenticated(db, user_id, auth_hash):
    if not auth_hash or not db.execute('SELECT 1 FROM auth_sessions WHERE user_id=? AND token_hash=? AND expires_at>?', (user_id, auth_hash, now())).fetchone():
        raise UserError('login_required', 'This browser authentication is no longer valid.')


def _current(db, row):
    return bool(row['expires_at'] > now() and db.execute('SELECT 1 FROM auth_sessions WHERE user_id=? AND token_hash=? AND expires_at>?', (row['user_id'], row['auth_hash'], now())).fetchone())


def _summary(db, row):
    state = json.loads(row['state'])
    return {'client_id': row['client_id'], 'session_id': row['session_id'], 'revision': row['revision'],
            'foreground': state['foreground'], 'stale': not _current(db, row),
            'observed_at': row['observed_at'], 'expires_at': row['expires_at'],
            'viewport': state['viewport']}


def clients(db, user_id):
    initialize(db)
    return [_summary(db, row) for row in db.execute('SELECT * FROM frontend_clients WHERE user_id=? ORDER BY observed_at DESC LIMIT ?', (user_id, MAX_CLIENTS))]


def client(db, user_id, client_id, auth_hash=None, fresh=False):
    initialize(db)
    row = db.execute('SELECT * FROM frontend_clients WHERE client_id=? AND user_id=?', (client_id, user_id)).fetchone()
    if row is None or (auth_hash is not None and row['auth_hash'] != auth_hash):
        raise UserError('frontend_unavailable', 'This browser has no observation for the current authenticated identity.')
    if fresh and not _current(db, row):
        raise UserError('frontend_unavailable', 'This browser observation has expired; wait for a fresh observation.')
    return row


def snapshot(db, user_id, client_id):
    row = client(db, user_id, client_id)
    return {**_summary(db, row), 'state': json.loads(row['state']),
            'scope': 'Authenticated client-reported semantics; no drafts, form values, DOM, clipboard or pixel verification.'}


def target(db, user_id, session_id, client_id=None, expected_revision=None):
    if client_id is None:
        choices = [item for item in clients(db, user_id) if not item['stale'] and item['foreground'] and item['session_id'] == session_id]
        if len(choices) > 1:
            raise UserError('frontend_ambiguous', 'Multiple foreground browsers show this conversation. Inspect app list/snapshot and specify client_id.')
        if not choices:
            raise UserError('frontend_unavailable', 'No fresh foreground browser is observable for this conversation. No UI action was sent.')
        client_id = choices[0]['client_id']
    row = client(db, user_id, client_id, fresh=True)
    state = json.loads(row['state'])
    if not state['foreground']:
        raise UserError('frontend_unavailable', 'The selected browser is not in the foreground. No UI action was sent.')
    if expected_revision is not None and expected_revision != row['revision']:
        raise UserError('frontend_conflict', 'The selected browser changed. Read a fresh app snapshot before acting.')
    return row


async def observe(store, user_id, auth_hash, observation):
    body = observation if isinstance(observation, FrontendObservation) else FrontendObservation.model_validate(observation)
    state = body.model_dump(exclude={'client_id', 'revision'})
    encoded = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    if len(encoded.encode()) > 24576:
        raise ValueError('Frontend observation exceeds 24 KiB.')
    def persist(db):
        initialize(db)
        _authenticated(db, user_id, auth_hash)
        db.execute('DELETE FROM frontend_revocations WHERE expires_at<=?', (now(),))
        if db.execute('SELECT 1 FROM frontend_revocations WHERE client_id=?', (body.client_id,)).fetchone():
            raise UserError('frontend_identity_revoked', 'This browser identity was reset; create a new client_id.')
        if body.session_id and not db.execute('SELECT 1 FROM sessions WHERE id=? AND user_id=?', (body.session_id, user_id)).fetchone():
            raise UserError('not_found', 'This conversation was not found.')
        file_ids = set(body.attachment_ids)
        if body.preview:
            file_ids.add(body.preview.file_id)
        for file_id in file_ids:
            if not db.execute('SELECT 1 FROM file_entries WHERE id=? AND user_id=?', (file_id, user_id)).fetchone():
                raise UserError('file_not_found', 'An observed file is unavailable in this account.')
        previous = db.execute('SELECT * FROM frontend_clients WHERE client_id=?', (body.client_id,)).fetchone()
        if previous and previous['user_id'] != user_id:
            raise UserError('frontend_conflict', 'Create a new browser identity after authentication changes.')
        if previous and previous['auth_hash'] != auth_hash:
            # A login in another tab rotates the shared cookie. Retire the old
            # identity durably before asking the same account to register anew;
            # otherwise it remains a second fresh target until the lease expires.
            expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
            db.execute('INSERT OR REPLACE INTO frontend_revocations VALUES (?,?,?)', (body.client_id, user_id, expires))
            db.execute('DELETE FROM frontend_revocations WHERE user_id=? AND client_id NOT IN (SELECT client_id FROM frontend_revocations WHERE user_id=? ORDER BY expires_at DESC LIMIT 128)', (user_id, user_id))
            db.execute('DELETE FROM frontend_clients WHERE client_id=?', (body.client_id,))
            return {'code': 'frontend_identity_changed', 'message': 'This account has a new authentication session; register a new client_id.'}
        if previous and (body.revision < previous['revision'] or (body.revision == previous['revision'] and encoded != previous['state'])):
            raise UserError('frontend_conflict', 'The frontend observation revision is out of order.')
        observed_at = now()
        # Keep only latest observations, with a bounded per-account cache. Expired
        # identities may be replaced; never evict another live tab to admit a new one.
        if previous is None:
            db.execute('DELETE FROM frontend_clients WHERE user_id=? AND expires_at<=?', (user_id, observed_at))
            if db.execute('SELECT count(*) FROM frontend_clients WHERE user_id=?', (user_id,)).fetchone()[0] >= MAX_CLIENTS:
                raise UserError('frontend_unavailable', 'Too many active browser identities; close an unused client.')
        expires = (datetime.now(timezone.utc) + timedelta(seconds=TTL_SECONDS)).isoformat()
        db.execute('''INSERT INTO frontend_clients VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(client_id) DO UPDATE SET session_id=excluded.session_id,revision=excluded.revision,
            state=excluded.state,observed_at=excluded.observed_at,expires_at=excluded.expires_at''',
                   (body.client_id, user_id, auth_hash, body.session_id, body.revision, encoded, observed_at, expires))
        return {'accepted': True, 'client_id': body.client_id, 'revision': body.revision, 'observed_at': observed_at, 'expires_at': expires}
    result = await store.call(persist)
    if result.get('code'):
        raise UserError(result['code'], result['message'])
    return result


def clear(db, user_id, auth_hash=None):
    initialize(db)
    expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    db.execute('INSERT OR REPLACE INTO frontend_revocations SELECT client_id,user_id,? FROM frontend_clients WHERE user_id=? AND (? IS NULL OR auth_hash=?)', (expires, user_id, auth_hash, auth_hash))
    db.execute('DELETE FROM frontend_revocations WHERE user_id=? AND client_id NOT IN (SELECT client_id FROM frontend_revocations WHERE user_id=? ORDER BY expires_at DESC LIMIT 128)', (user_id, user_id))
    if auth_hash:
        db.execute('DELETE FROM frontend_clients WHERE user_id=? AND auth_hash=?', (user_id, auth_hash))
    else:
        db.execute('DELETE FROM frontend_clients WHERE user_id=?', (user_id,))


def register_frontend_routes(app):
    @app.post('/api/frontend/observation')
    async def observation(body: FrontendObservation, request: Request):
        return await observe(app.state.store, request.state.user['id'], token_hash(request.cookies[COOKIE]), body)
