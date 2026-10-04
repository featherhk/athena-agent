"""Durable identities for deliberate sends; one SQLite transaction owns acceptance.

Receipts prove admission, never exactly-once effects in an external service.
The engine's session control lock still owns live interruption and cancellation.
"""
import asyncio
from dataclasses import dataclass
import hashlib
import json
import re

from .errors import ServiceError, UserError


@dataclass(frozen=True)
class SubmissionIntent:
    user_id: str
    client_submission_id: str
    text: str
    mode: str
    file_ids: tuple[str, ...]
    expected_submission_revision: int

    def __post_init__(self):
        if (not self.user_id or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', self.client_submission_id)
                or self.mode not in ('queue', 'interrupt')
                or type(self.expected_submission_revision) is not int
                or not 0 <= self.expected_submission_revision <= 2**53 - 1):
            raise ServiceError('invalid_submission_intent', 'Invalid submission identity or revision.', 422)

    @property
    def payload_hash(self):
        payload = {'text': self.text, 'mode': self.mode, 'file_ids': list(self.file_ids),
                   'expected_submission_revision': self.expected_submission_revision}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        return 'v1:' + hashlib.sha256(encoded.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class StopIntent:
    user_id: str
    client_stop_id: str

    def __post_init__(self):
        if not self.user_id or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', self.client_stop_id):
            raise ServiceError('invalid_stop_intent', 'Invalid stop identity.', 422)


def revision(db, session_id, user_id=None):
    row = db.execute('SELECT user_id,submission_revision FROM sessions WHERE id=?', (session_id,)).fetchone()
    if row is None or (user_id is not None and row['user_id'] != user_id):
        raise ServiceError('session_not_found', 'This conversation was not found.', 404)
    return row['submission_revision']


def lookup(db, session_id, user_id, client_submission_id, *, payload_hash=None):
    current = revision(db, session_id, user_id)
    row = db.execute('''SELECT s.payload_hash,s.run_id,r.status FROM submission_receipts s
        JOIN runs r ON r.id=s.run_id AND r.session_id=s.session_id
        WHERE s.user_id=? AND s.session_id=? AND s.client_submission_id=?''',
                     (user_id, session_id, client_submission_id)).fetchone()
    if row is None:
        return None
    if payload_hash is not None and row['payload_hash'] != payload_hash:
        raise ServiceError('submission_conflict', 'This submission identity already belongs to another message.', 409)
    return {'accepted': True, 'run_id': row['run_id'], 'status': row['status'],
            'client_submission_id': client_submission_id, 'submission_revision': current, 'replayed': True}


def resolve(db, session_id, intent):
    return lookup(db, session_id, intent.user_id, intent.client_submission_id, payload_hash=intent.payload_hash)


def lookup_stop(db, session_id, user_id, client_stop_id):
    revision(db, session_id, user_id)
    row = db.execute('''SELECT submission_revision FROM stop_receipts
        WHERE user_id=? AND session_id=? AND client_stop_id=?''', (user_id, session_id, client_stop_id)).fetchone()
    if row is None:
        return None
    return {'client_stop_id': client_stop_id, 'submission_revision': row['submission_revision']}


def record_stop(db, session_id, intent, submission_revision):
    from .store import now
    revision(db, session_id, intent.user_id)
    db.execute('''INSERT INTO stop_receipts(user_id,session_id,client_stop_id,submission_revision,created_at)
        VALUES (?,?,?,?,?)''', (intent.user_id, session_id, intent.client_stop_id, submission_revision, now()))


def check_revision(db, session_id, intent, expected=None):
    current = revision(db, session_id, intent.user_id)
    expected = intent.expected_submission_revision if expected is None else expected
    if current != expected:
        raise ServiceError('submission_revision_conflict', 'This message predates a Stop. Review it before sending again.', 409)
    return current


def accept(db, session_id, text, attachments, frontend_source, intent, admission_revision):
    """All writes, including the input's original window binding, commit together."""
    from .store import Store, now
    existing = resolve(db, session_id, intent)
    if existing is not None:
        return existing
    check_revision(db, session_id, intent, admission_revision)
    if text != intent.text.strip() or [item['id'] for item in (attachments or [])] != list(intent.file_ids):
        raise ServiceError('invalid_submission_intent', 'The admitted input differs from its submission payload.', 422)
    run_id = Store._enqueue(db, session_id, text, attachments, frontend_source=frontend_source)
    message_id = db.execute("SELECT id FROM messages WHERE run_id=? AND role='user'", (run_id,)).fetchone()[0]
    db.execute('''INSERT INTO submission_receipts
        (user_id,session_id,client_submission_id,payload_hash,run_id,message_id,created_at)
        VALUES (?,?,?,?,?,?,?)''',
               (intent.user_id, session_id, intent.client_submission_id, intent.payload_hash, run_id, message_id, now()))
    result = resolve(db, session_id, intent)
    result['replayed'] = False
    return result


async def submit_input(engine, session_id, text, mode='queue', attachments=None, *,
                       frontend_client_id=None, frontend_auth_hash=None, _admission_epoch=None,
                       submission=None, load_attachments=None):
    engine.check_owner()
    stop_epoch = engine.submission_epoch(session_id) if _admission_epoch is None else _admission_epoch
    began = engine.reset_clock
    owner = await engine.tools.owner(session_id)

    async def existing():
        if submission is None:
            return None
        return await engine.store.call(lambda db: resolve(db, session_id, submission))

    accepted = await existing()
    if accepted is not None:
        return accepted
    try:
        if owner['user_id'] in engine.resetting_users:
            raise UserError('resetting', '正在恢复默认状态，请稍后再发。')
        if submission is not None and (not text.strip() and not submission.file_ids):
            raise UserError('empty_message', '请写下你想做的事')
        if submission is not None and (text != submission.text.strip() or mode != submission.mode):
            raise ServiceError('invalid_submission_intent', 'The input differs from its submission payload.', 422)
        if load_attachments is not None:
            attachments = await load_attachments()
        profile, _ = await engine.models.selected(session_id)
        if engine.cloud:
            await engine.cloud.require_credit(owner['user_id'])
        else:
            await engine.models.require_key(profile)
        async with engine.controls.setdefault(session_id, asyncio.Lock()):
            accepted = await existing()
            if accepted is not None:
                return accepted
            if owner['user_id'] in engine.resetting_users or engine.user_reset_versions.get(owner['user_id'], 0) > began:
                raise UserError('resetting', 'This input was superseded by reset. Send it again when ready.')
            if submission is not None:
                await engine.store.call(lambda db: check_revision(db, session_id, submission))
            if engine.stop_epochs.get(session_id, 0) != stop_epoch:
                raise UserError('submission_stopped', 'Stop cancelled this pending submission. You can send it again when ready.')
            from .frontend_state import capture_source
            source = None
            if frontend_client_id is not None:
                source = await engine.store.call(lambda db: capture_source(db, session_id, frontend_client_id, frontend_auth_hash))
                if owner['user_id'] in engine.resetting_users or engine.user_reset_versions.get(owner['user_id'], 0) > began:
                    raise UserError('resetting', 'This input was superseded by reset. Send it again when ready.')
            admission_revision = None
            if mode == 'interrupt':
                admission_revision = await engine._stop(session_id)
            if submission is None:
                result = await engine.store.enqueue(session_id, text, attachments, frontend_source=source)
            else:
                result = await engine.store.enqueue(session_id, text, attachments, frontend_source=source,
                                                    submission=submission, admission_revision=admission_revision)
    except Exception:
        # A concurrent attempt can have committed while this attempt's independent
        # attachment, provider, credit or source preflight failed.
        accepted = await existing()
        if accepted is not None:
            return accepted
        raise
    if submission is not None and result['replayed']:
        return result
    try:
        await engine.emit(session_id, 'refresh', {})
        engine.kick(session_id)
    except Exception as exc:
        if submission is None:
            raise
        # Admission already committed. Never let a notification ValueError be
        # mapped to an HTTP 4xx claiming the input was rejected. Receipt lookup
        # recovers the result; the ordinary queued-run tick recovers actor wake.
        raise ServiceError('submission_acknowledgement_unavailable',
                           'The message may have been accepted. Check its submission receipt.', 503) from exc
    return result
