"""Account profile actions and a local-only secret entry path for passwords."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
import re
import stat
import unicodedata

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .auth import COOKIE, password_hash, public_user, token_hash, verify
from .errors import UserError
from .files import DIRECTORY_FLAGS, _child_dir, _identity, _open_file
from .store import now, uid

MAX_AVATAR_BYTES = 2 * 1024 * 1024
AVATAR_TYPES = {'png': 'image/png', 'jpg': 'image/jpeg', 'gif': 'image/gif', 'webp': 'image/webp'}


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    display_name: str = Field(max_length=60)


class PasswordInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


async def initialize_accounts(store):
    def migrate(db):
        columns = {row[1] for row in db.execute('PRAGMA table_info(users)')}
        for name, definition in (('display_name', "TEXT NOT NULL DEFAULT ''"), ('avatar_path', 'TEXT'), ('profile_version', 'INTEGER NOT NULL DEFAULT 0')):
            if name not in columns:
                db.execute(f'ALTER TABLE users ADD COLUMN {name} {definition}')
    await store.call(migrate)


def _name(value):
    if not isinstance(value, str) or len(value) > 60:
        raise UserError('invalid_input', 'Display name must be at most 60 characters.')
    value = unicodedata.normalize('NFC', value).strip()
    if any(unicodedata.category(char).startswith('C') for char in value):
        raise UserError('invalid_input', 'Display name contains unsupported characters.')
    return value


def _avatar_type(data):
    if not 0 < len(data) <= MAX_AVATAR_BYTES:
        raise UserError('invalid_avatar', 'Choose an image under 2 MB.')
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) >= 24 and data[12:16] == b'IHDR':
        width, height = int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big')
        if 0 < width <= 8192 and 0 < height <= 8192 and width * height <= 16_000_000:
            return 'png'
    if data.startswith(b'\xff\xd8\xff') and len(data) >= 4 and data.endswith(b'\xff\xd9'):
        return 'jpg'
    if data[:6] in {b'GIF87a', b'GIF89a'} and len(data) >= 13:
        width, height = int.from_bytes(data[6:8], 'little'), int.from_bytes(data[8:10], 'little')
        if 0 < width <= 8192 and 0 < height <= 8192 and width * height <= 16_000_000:
            return 'gif'
    if len(data) >= 20 and data[:4] == b'RIFF' and data[8:12] == b'WEBP' and data[12:16] in {b'VP8 ', b'VP8L', b'VP8X'}:
        if int.from_bytes(data[4:8], 'little') + 8 == len(data):
            return 'webp'
    raise UserError('invalid_avatar', 'Choose a PNG, JPEG, WebP or GIF image.')


@contextmanager
def _avatar_directory(config, user_id, create=False):
    descriptors = []
    try:
        descriptors.append(os.open(config.data_dir, DIRECTORY_FLAGS))
        for name in ('account-avatars', _identity(user_id)):
            descriptors.append(_child_dir(descriptors[-1], name, create))
        yield descriptors[-1]
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _save_avatar(config, user_id, data):
    kind = _avatar_type(data)
    name = f'{uid()}.{kind}'
    with _avatar_directory(config, user_id, create=True) as directory:
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            os.unlink(name, dir_fd=directory)
            raise
    return name


def _read_avatar(config, user_id, name):
    if not name or not re.fullmatch(r'[a-f0-9]{32}\.(?:png|jpg|webp|gif)', name):
        raise HTTPException(404, 'file_not_found')
    try:
        with _avatar_directory(config, user_id) as directory:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(descriptor, 'rb') as handle:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_AVATAR_BYTES:
                    raise HTTPException(404, 'file_not_found')
                data = handle.read(MAX_AVATAR_BYTES + 1)
                kind = _avatar_type(data)
                return data, AVATAR_TYPES[kind]
    except OSError:
        raise HTTPException(404, 'file_not_found') from None


async def read_profile(store, user_id):
    rows = await store.rows('SELECT * FROM users WHERE id=?', (user_id,))
    if not rows:
        raise HTTPException(401, 'login_required')
    return public_user(rows[0])


async def update_profile(store, config, user_id, changes, avatar=None, source_run_id=None):
    if not isinstance(changes, dict) or changes.keys() - {'display_name'}:
        raise UserError('invalid_input', 'Unsupported profile fields.')
    display_name = _name(changes['display_name']) if 'display_name' in changes else None
    avatar_path = await asyncio.to_thread(_save_avatar, config, user_id, avatar) if avatar is not None else None
    def update(db):
        row = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if not row:
            raise HTTPException(401, 'login_required')
        if source_run_id and not db.execute('SELECT 1 FROM runs r JOIN sessions s ON s.id=r.session_id WHERE r.id=? AND s.user_id=?', (source_run_id, user_id)).fetchone():
            raise UserError('invalid_input', 'The action does not belong to this account.')
        changed = (display_name is not None and display_name != row['display_name']) or avatar_path is not None
        if changed:
            db.execute('UPDATE users SET display_name=?,avatar_path=?,profile_version=profile_version+1 WHERE id=?',
                       (display_name if display_name is not None else row['display_name'], avatar_path if avatar_path is not None else row['avatar_path'], user_id))
        user = public_user(dict(db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()))
        if changed:
            db.execute("INSERT INTO events(session_id,type,payload,created_at) SELECT id,'account_changed',?,? FROM sessions WHERE user_id=?",
                       (json.dumps({'user': user, 'source_run_id': source_run_id}), now(), user_id))
        return user
    return await store.call(update)


async def change_password(store, user_id, token, current_password, new_password):
    if not token:
        raise HTTPException(401, 'login_required')
    stamp = datetime.now(timezone.utc)
    attempt_key = f'password-change:{user_id}'
    def reserve(db):
        previous = db.execute('SELECT * FROM auth_attempts WHERE username=?', (attempt_key,)).fetchone()
        recent = previous and (stamp - datetime.fromisoformat(previous['last_at'])).total_seconds() < 900
        if recent and previous['attempts'] >= 10:
            raise UserError('login_rate_limited', 'Too many attempts. Try again in 15 minutes.')
        count = previous['attempts'] + 1 if recent else 1
        db.execute('INSERT INTO auth_attempts VALUES (?,?,?) ON CONFLICT(username) DO UPDATE SET attempts=excluded.attempts,last_at=excluded.last_at', (attempt_key, count, stamp.isoformat()))
        user = db.execute('SELECT password_hash FROM users WHERE id=?', (user_id,)).fetchone()
        if not user:
            raise HTTPException(401, 'login_required')
        return user[0]
    encoded = await store.call(reserve)
    if not await asyncio.to_thread(verify, current_password, encoded):
        raise UserError('invalid_password', 'The current password is incorrect.')
    replacement = await asyncio.to_thread(password_hash, new_password)
    current_token = token_hash(token)
    def change(db):
        if not db.execute('SELECT 1 FROM auth_sessions WHERE token_hash=? AND user_id=? AND expires_at>?', (current_token, user_id, now())).fetchone():
            raise HTTPException(401, 'login_required')
        if db.execute('UPDATE users SET password_hash=? WHERE id=? AND password_hash=?', (replacement, user_id, encoded)).rowcount != 1:
            raise UserError('invalid_password', 'The password changed. Sign in again.')
        db.execute('DELETE FROM auth_sessions WHERE user_id=? AND token_hash!=?', (user_id, current_token))
        db.execute('DELETE FROM auth_attempts WHERE username=?', (attempt_key,))
    await store.call(change)


async def account_operation(store, config, session_id, run_id, args):
    if not isinstance(args, dict) or args.keys() - {'op', 'display_name', 'avatar_file_id'}:
        raise UserError('invalid_input', 'Unsupported account action fields. Passwords are entered in the local account form.')
    rows = await store.rows('SELECT user_id FROM sessions WHERE id=?', (session_id,))
    if not rows or not rows[0]['user_id']:
        raise HTTPException(401, 'login_required')
    user_id = rows[0]['user_id']
    if run_id and not await store.rows('SELECT id FROM runs WHERE id=? AND session_id=?', (run_id, session_id)):
        raise UserError('invalid_input', 'The action does not belong to this conversation.')
    operation = args.get('op')
    if operation == 'read' and args.keys() == {'op'}:
        return {'user': await read_profile(store, user_id)}
    if operation == 'change_password' and args.keys() == {'op'}:
        await store.event(session_id, 'account_action', {'action': 'change_password', 'source_run_id': run_id})
        return {'action': 'change_password', 'status': 'form_requested'}
    if operation != 'update_profile' or not args.keys() - {'op'}:
        raise UserError('invalid_input', 'Unsupported account action.')
    avatar = None
    if 'avatar_file_id' in args:
        records = await store.rows('SELECT f.* FROM file_entries f JOIN sessions s ON s.id=f.session_id WHERE f.id=? AND f.user_id=? AND s.user_id=?', (args['avatar_file_id'], user_id, user_id))
        if not records:
            raise HTTPException(404, 'file_not_found')
        def read():
            handle, size = _open_file(config, records[0])
            with handle:
                if size > MAX_AVATAR_BYTES:
                    raise UserError('invalid_avatar', 'Choose an image under 2 MB.')
                return handle.read(MAX_AVATAR_BYTES + 1)
        avatar = await asyncio.to_thread(read)
    changes = {'display_name': args['display_name']} if 'display_name' in args else {}
    return {'user': await update_profile(store, config, user_id, changes, avatar, run_id)}


async def pending_account_action(store, user_id):
    rows = await store.rows("""SELECT e.seq,e.payload FROM events e JOIN sessions s ON s.id=e.session_id
        WHERE s.user_id=? AND e.type='account_action' AND NOT EXISTS (
            SELECT 1 FROM events d JOIN sessions ds ON ds.id=d.session_id
            WHERE ds.user_id=s.user_id AND d.type='account_action_dismissed'
            AND CAST(json_extract(d.payload,'$.through_seq') AS INTEGER)>=e.seq)
        ORDER BY e.seq DESC LIMIT 1""", (user_id,))
    return {"seq": rows[0]["seq"], **json.loads(rows[0]["payload"])} if rows else None


def register_account_routes(app: FastAPI):
    def context(request):
        user = getattr(request.state, 'user', None)
        if not user:
            raise HTTPException(401, 'login_required')
        return app.state.store, app.state.engine.config, user['id']

    @app.post('/api/account/actions/{sequence}/dismiss')
    async def dismiss_action(sequence: int, request: Request):
        store, _, user_id = context(request)
        rows = await store.rows("SELECT e.session_id FROM events e JOIN sessions s ON s.id=e.session_id WHERE e.seq=? AND e.type='account_action' AND s.user_id=?", (sequence, user_id))
        if not rows:
            raise HTTPException(404, 'invalid_input')
        await store.event(rows[0]['session_id'], 'account_action_dismissed', {'through_seq': sequence})
        return {'ok': True}

    @app.get('/api/account/profile')
    async def profile(request: Request):
        store, _, user_id = context(request)
        return {'user': await read_profile(store, user_id)}

    @app.post('/api/account/profile')
    async def update(request: Request, body: ProfileInput):
        store, config, user_id = context(request)
        return {'user': await update_profile(store, config, user_id, {'display_name': body.display_name})}

    @app.post('/api/account/password')
    async def password(request: Request, body: PasswordInput):
        store, _, user_id = context(request)
        cloud=getattr(app.state,'cloud',None)
        if cloud:
            rows=await store.rows('SELECT remote_token FROM cloud_logins WHERE local_hash=?',(token_hash(request.cookies.get(COOKIE,'')),))
            if not rows:
                raise HTTPException(401,'login_required')
            return await cloud.call('/account/password',token=rows[0]['remote_token'],body={'current_password':body.current_password,'new_password':body.new_password})
        await change_password(store, user_id, request.cookies.get(COOKIE), body.current_password, body.new_password)
        return {'ok': True}

    @app.post('/api/account/avatar')
    async def upload_avatar(request: Request):
        store, config, user_id = context(request)
        length = request.headers.get('content-length', '0')
        if not length.isdigit() or int(length) > MAX_AVATAR_BYTES:
            raise UserError('invalid_avatar', 'Choose an image under 2 MB.')
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_AVATAR_BYTES:
                raise UserError('invalid_avatar', 'Choose an image under 2 MB.')
            data.extend(chunk)
        try:
            user = await update_profile(store, config, user_id, {}, bytes(data))
        except OSError:
            raise UserError('invalid_avatar', 'The image could not be saved.') from None
        return {'user': user}

    @app.get('/api/account/avatar')
    async def avatar(request: Request, owner: str | None = None):
        store, config, user_id = context(request)
        if owner is not None and owner != user_id:
            raise HTTPException(404, 'file_not_found')
        rows = await store.rows('SELECT avatar_path FROM users WHERE id=?', (user_id,))
        data, media_type = await asyncio.to_thread(_read_avatar, config, user_id, rows[0]['avatar_path'])
        return Response(data, media_type=media_type, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "sandbox; default-src 'none'"})
