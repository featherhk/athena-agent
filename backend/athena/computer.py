"""Native accessibility operations with account/session-bound observations and receipts."""
import asyncio
import json
import time

from .store import now, uid


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS computer_actions (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
        run_id TEXT REFERENCES runs(id), action TEXT NOT NULL, status TEXT NOT NULL,
        result TEXT, created_at TEXT NOT NULL, finished_at TEXT)''')
    db.execute("UPDATE computer_actions SET status='unknown_after_restart',finished_at=? WHERE status='running'", (now(),))


def validate(args):
    action = args.get('action', 'status')
    fields = {'status': set(), 'list': set(), 'snapshot': {'app_id'},
              'invoke': {'observation_id', 'ref', 'native_action'},
              'set_value': {'observation_id', 'ref', 'text'}, 'history': set()}
    optional = {'window_title'} if action == 'snapshot' else set()
    if action not in fields or set(args) - {'op', 'action'} - fields[action] - optional:
        raise ValueError('Unsupported computer operation; read desktop/references/control.md.')
    required = fields[action]
    supplied = required | (optional & args.keys())
    if any(not isinstance(args.get(key), str) or (not args[key] and key != 'text') or '\x00' in args[key]
           for key in supplied):
        raise ValueError('Computer operation requires its documented string fields.')
    if any(len(args[key]) > (10000 if key == 'text' else 600 if key == 'window_title' else 200) for key in supplied):
        raise ValueError('Computer argument is too long.')
    return action


class ComputerHost:
    def __init__(self, store, native=None):
        self.store, self.native = store, native
        # One physical desktop, including competing sessions: serialize observation/action.
        self.lock = asyncio.Lock()
        self.observations = {}

    @property
    def available(self):
        return self.native is not None and self.native.connected

    async def operate(self, args, session_id, run_id):
        action = validate(args)
        owner = await self.store.rows('SELECT user_id FROM sessions WHERE id=?', (session_id,))
        if not owner or not owner[0]['user_id']:
            raise ValueError('Computer control requires an owned conversation.')
        if action == 'history':
            return {'actions': await self.store.rows(
                'SELECT * FROM computer_actions WHERE session_id=? ORDER BY created_at DESC LIMIT 20', (session_id,))}
        if not self.available:
            return {'supported': False, 'status': 'unavailable', 'code': 'native_host_unavailable',
                    'message': 'This execution node has no connected Mac accessibility host.'}
        async with self.lock:
            payload = {key: value for key, value in args.items() if key != 'op'}
            payload['action'] = action
            # Expire observations even if many sessions only ever read the desktop.
            self.observations = {key: value for key, value in self.observations.items()
                                 if time.monotonic() - value[1] < 30}
            if action in ('invoke', 'set_value'):
                observation = self.observations.get(args['observation_id'])
                if observation is None or observation[0] != session_id:
                    raise ValueError('Desktop observation expired or belongs to another conversation; take a new snapshot.')
                # Invalidate before dispatch, including lost responses or cancellation.
                self.observations.clear()
            identifier = uid()
            dispatched = False
            try:
                await self.store.execute('INSERT INTO computer_actions VALUES (?,?,?,?,?,?,?,?)',
                    (identifier, session_id, run_id, action, 'running', None, now(), None))
                dispatched = True
                result = await self.native.call(payload)
                if not isinstance(result, dict) or result.get('status') not in ('complete', 'rejected', 'unknown'):
                    raise ValueError('Invalid native receipt')
                if action == 'snapshot' and result['status'] == 'complete':
                    self.observations.clear()
                    self.observations[result['observation_id']] = (session_id, time.monotonic())
                await self.store.execute('UPDATE computer_actions SET status=?,result=?,finished_at=? WHERE id=?',
                    (result['status'], json.dumps(result, ensure_ascii=False), now(), identifier))
                return {**result, 'operation_id': identifier}
            except (Exception, asyncio.CancelledError) as error:
                # No success/error inference from a cancelled or broken pipe.
                status = 'unknown' if dispatched else 'not_dispatched'
                result = {'status': status, 'code': 'native_receipt_missing' if dispatched else 'cancelled_before_dispatch',
                          'message': 'Observe current state before another action; do not automatically retry.'}
                await self.store.execute('UPDATE computer_actions SET status=?,result=?,finished_at=? WHERE id=?',
                    (status, json.dumps(result), now(), identifier))
                if isinstance(error, asyncio.CancelledError):
                    raise
                return {**result, 'operation_id': identifier}
