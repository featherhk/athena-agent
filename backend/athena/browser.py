"""Managed browser work: owned tabs, observed targets and durable action receipts."""
import asyncio
import json
import os
import re
import stat
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .browser_driver import browser_driver, BrowserDriverError
from .capabilities import manual_browser_handoff_available
from .browser_uploads import upload_ids, stage_upload, discard_upload, staged_operations
from fastapi import HTTPException
from .files import _workspace, discover_files, list_files, sanitize_filename, MAX_FILE_BYTES
from .store import now, uid

ACTIONS = ('status', 'list', 'history', 'open', 'snapshot', 'click', 'fill', 'press', 'select',
           'check', 'uncheck', 'scroll', 'back', 'forward', 'reload', 'screenshot', 'download', 'upload', 'close', 'handoff')
REF_ACTIONS = ('click', 'fill', 'select', 'check', 'uncheck', 'download', 'upload')
OBSERVED_ACTIONS = (*REF_ACTIONS, 'press', 'scroll', 'back', 'forward', 'reload', 'close')


def public_url(value):
    try:
        parsed = urlsplit(value)
        blocked = ('token', 'secret', 'password', 'authorization', 'signature', 'credential', 'code', 'key')
        query = [(key, '[redacted]' if any(word in key.lower() for word in blocked) else text)
                 for key, text in parse_qsl(parsed.query, keep_blank_values=True)]
        hostname = parsed.hostname or ''
        if ':' in hostname:
            hostname = f'[{hostname}]'
        authority = hostname + (f':{parsed.port}' if parsed.port else '')
        return urlunsplit((parsed.scheme, authority, parsed.path, urlencode(query), ''))
    except ValueError:
        return ''


def validate_arguments(args):
    action = args.get('action', 'status')
    if action not in ACTIONS:
        raise ValueError('Unknown browser action; read the browser skill.')
    allowed = {'op', 'action', 'tab_id', 'observation_id'} | {
        'open': {'url'}, 'snapshot': {'ref'}, 'click': {'ref'}, 'fill': {'ref', 'text'},
        'select': {'ref', 'value'}, 'check': {'ref'}, 'uncheck': {'ref'},
        'press': {'key'}, 'scroll': {'direction', 'pixels'}, 'download': {'ref', 'name'},
        'upload': {'ref', 'file_id', 'file_ids'},
        'handoff': {'questions', 'reason'},
        'history': {'offset', 'limit'},
    }.get(action, set())
    if set(args) - allowed:
        raise ValueError('Unsupported browser arguments; driver flags and credentials are host-owned.')
    if action == 'open':
        value = args.get('url')
        if not isinstance(value, str) or len(value) > 4096:
            raise ValueError('Use a complete HTTP or HTTPS URL.')
        parsed = urlsplit(value)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Use HTTP or HTTPS without embedded credentials.')
    if action in REF_ACTIONS or args.get('ref') is not None:
        if not isinstance(args.get('ref'), str) or not re.fullmatch(r'@e[0-9]+', args['ref']):
            raise ValueError('Use an element ref from the latest browser observation.')
    for key, maximum in (('text', 10000), ('value', 1000), ('key', 60)):
        if action == {'text': 'fill', 'value': 'select', 'key': 'press'}[key]:
            value = args.get(key)
            if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
                raise ValueError(f'Invalid browser {key}.')
    if action == 'scroll':
        if args.get('direction') not in ('up', 'down', 'left', 'right'):
            raise ValueError('Choose a scroll direction.')
        pixels = args.get('pixels', 600)
        if type(pixels) is not int or not 1 <= pixels <= 3000:
            raise ValueError('Scroll pixels must be an integer from 1 to 3000.')
    if action == 'download':
        name = args.get('name')
        try:
            valid = isinstance(name, str) and bool(name) and sanitize_filename(name) == name and len(name.encode()) <= 220
        except HTTPException:
            valid = False
        if not valid:
            raise ValueError('Use a plain download filename.')
    if action == 'history':
        if type(args.get('limit', 20)) is not int or not 1 <= args.get('limit', 20) <= 100 or type(args.get('offset', 0)) is not int or args.get('offset', 0) < 0:
            raise ValueError('Browser history accepts limit 1–100 and a nonnegative offset.')
    if action == 'upload':
        upload_ids(args)
    return action


def recover_browser_actions(db):
    db.execute("UPDATE browser_tabs SET observation_id=NULL,refs='[]' WHERE session_id IN (SELECT session_id FROM browser_actions WHERE status='running')")
    db.execute("UPDATE browser_actions SET status='unknown_after_restart',finished_at=? WHERE status='running'", (now(),))


def publish_browser_file(config, user_id, session_id, source, name):
    """Copy a bounded regular download into the no-follow user file workspace."""
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as incoming:
        info = os.fstat(incoming.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE_BYTES:
            raise ValueError('Browser output must be a regular file no larger than 20 MB.')
        with _workspace(config, user_id, session_id, create=True) as workspace:
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=workspace)
            try:
                with os.fdopen(descriptor, 'wb') as output:
                    total = 0
                    while chunk := incoming.read(65536):
                        total += len(chunk)
                        if total > MAX_FILE_BYTES:
                            raise ValueError('Browser output grew beyond 20 MB.')
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            except BaseException:
                os.unlink(name, dir_fd=workspace)
                raise
    source.unlink()


class BrowserHost:
    def __init__(self, config, store, driver=None):
        self.config, self.store = config, store
        self.driver = driver or browser_driver(config)
        self.locks = {}

    async def _owner(self, session_id):
        rows = await self.store.rows('SELECT user_id FROM sessions WHERE id=?', (session_id,))
        if not rows or not rows[0]['user_id']:
            raise ValueError('Managed browsing requires a signed-in account.')
        return rows[0]['user_id']

    async def _identity(self, user_id):
        identity = await self.driver.identity(user_id)
        targets = None
        if identity:
            inventory = await self.driver.run(user_id, ['tab', 'list'])
            if not isinstance(inventory, dict) or not isinstance(inventory.get('tabs'), list):
                raise BrowserDriverError('browser_protocol_error', 'Browser tab inventory is unavailable.')
            targets = {tab.get('targetId') for tab in inventory['tabs'] if isinstance(tab, dict)}
        def reconcile(db):
            previous = db.execute('SELECT identity FROM browser_instances WHERE user_id=?', (user_id,)).fetchone()
            if previous is None or previous['identity'] != identity:
                db.execute("UPDATE browser_tabs SET status='closed',observation_id=NULL,refs='[]' WHERE user_id=?", (user_id,))
                db.execute('INSERT INTO browser_instances VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET identity=excluded.identity,updated_at=excluded.updated_at', (user_id, identity, now()))
            if targets is not None:
                for row in db.execute("SELECT id,target_id FROM browser_tabs WHERE user_id=? AND status='open'", (user_id,)).fetchall():
                    if row['target_id'] not in targets:
                        db.execute("UPDATE browser_tabs SET status='closed',observation_id=NULL,refs='[]',updated_at=? WHERE id=?", (now(), row['id']))
        await self.store.call(reconcile)
        await self._discard_closed_uploads(user_id)
        return identity

    async def _discard_closed_uploads(self, user_id):
        # A live input may read the file on a later submit. Only a confirmed closed
        # tab/old browser generation releases its snapshots, including unknown actions.
        try:
            operations = await asyncio.to_thread(staged_operations, self.config, user_id)
            if not operations:
                return
            slots = ','.join('?' for _ in operations)
            rows = await self.store.rows(f"""SELECT a.id FROM browser_actions a JOIN browser_tabs t ON t.id=a.tab_id
                WHERE t.user_id=? AND t.status='closed' AND a.action='upload' AND a.id IN ({slots})""", (user_id, *operations))
            for row in rows:
                await asyncio.to_thread(discard_upload, self.config, user_id, row['id'])
        except OSError:
            # Retain an unremovable snapshot; never break unrelated browsing or
            # follow an unexpected path. A later upload still checks its quota.
            pass

    async def _stage_upload(self, user_id, operation_id, records):
        copying = asyncio.create_task(asyncio.to_thread(stage_upload, self.config, user_id, operation_id, records))
        try:
            return await asyncio.shield(copying)
        except asyncio.CancelledError:
            # A worker thread cannot be cancelled. Finish its bounded copy before
            # releasing ownership or deleting paths it may still be writing.
            async def drain():
                try:
                    await copying
                except Exception:
                    pass  # The stopped request must remain cancelled if its copy failed.
                try:
                    await asyncio.to_thread(discard_upload, self.config, user_id, operation_id)
                except OSError:
                    pass  # Retain an unremovable snapshot rather than lose cancellation.
            cleanup = asyncio.create_task(drain())
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            cleanup.result()
            raise

    async def _tab(self, user_id, session_id, identifier):
        rows = await self.store.rows('SELECT * FROM browser_tabs WHERE id=? AND user_id=? AND session_id=?', (identifier, user_id, session_id))
        if not rows:
            raise ValueError('Browser tab not found in this conversation.')
        return rows[0]

    @staticmethod
    def _public_tab(row):
        return {key: row[key] for key in ('id', 'status', 'url', 'observation_id', 'updated_at')}

    async def _observation(self, user_id, session_id, run_id, tab_id, ref=None):
        command = ['snapshot']
        if ref:
            command += ['--selector', ref]
        data = await self.driver.run(user_id, command)
        if not isinstance(data, dict) or not isinstance(data.get('snapshot'), str) or not isinstance(data.get('refs'), dict):
            raise BrowserDriverError('browser_protocol_error', 'The browser returned an invalid observation.')
        text, artifact_id, observation_id = data['snapshot'], uid(), uid()
        path = self.config.data_dir / 'artifacts' / f'{artifact_id}.txt'
        await asyncio.to_thread(path.write_text, text)
        path.chmod(0o600)
        refs = ['@' + key.lstrip('@') for key in data['refs'] if re.fullmatch(r'@?e[0-9]+', key)]
        url = public_url(data.get('origin', ''))
        def save(db):
            db.execute('INSERT INTO artifacts VALUES (?,?,?,?,?,?)', (artifact_id, session_id, run_id, path.name, path.stat().st_size, now()))
            # The native engine's ref map belongs to its latest observed tab.
            # Another conversation must observe again after a tab switch.
            db.execute("UPDATE browser_tabs SET observation_id=NULL,refs='[]' WHERE user_id=? AND id<>?", (user_id, tab_id))
            db.execute("UPDATE browser_tabs SET status='open',observation_id=?,refs=?,url=?,updated_at=? WHERE id=?", (observation_id, json.dumps(refs), url, now(), tab_id))
        await self.store.call(save)
        if len(text) > 12000:
            text = text[:8000] + f'\n[Clipped observation; runtime read_artifact artifact_id={artifact_id} restores the original.]\n' + text[-3000:]
        return {'tab_id': tab_id, 'observation_id': observation_id, 'url': url,
                'text': text, 'artifact_id': artifact_id, 'untrusted_page_content': True}

    async def operate(self, args, session_id, run_id):
        action = validate_arguments(args)
        user_id = await self._owner(session_id)
        if action == 'status':
            return {'available': self.driver.available(), 'provider': 'agent-browser',
                    'actions': list(ACTIONS), 'identity_scope': 'account', 'tab_scope': 'conversation',
                    'manual_handoff': manual_browser_handoff_available(self.config, self.driver),
                    'vision_input': False, 'computer_control': False}
        if action == 'history':
            limit, offset = args.get('limit', 20), args.get('offset', 0)
            rows = await self.store.rows('SELECT id,tab_id,run_id,action,status,error_code,created_at,finished_at FROM browser_actions WHERE session_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',
                                         (session_id, limit + 1, offset))
            return {'actions': rows[:limit], 'next_offset': offset + limit if len(rows) > limit else None}
        if not self.driver.available():
            raise BrowserDriverError('browser_unavailable', 'This execution node has no managed browser. Do not claim browser actions have run.')
        async with self.locks.setdefault(user_id, asyncio.Lock()):
            identity = await self._identity(user_id)
            if action == 'list':
                rows = await self.store.rows('SELECT * FROM browser_tabs WHERE user_id=? AND session_id=? ORDER BY created_at', (user_id, session_id))
                return {'tabs': [self._public_tab(row) for row in rows], 'running': identity is not None}
            tab = await self._tab(user_id, session_id, args.get('tab_id')) if args.get('tab_id') else None
            if action != 'open' and (tab is None or tab['status'] != 'open'):
                raise ValueError('Open a browser tab first. Closed tabs are never silently recreated.')
            if tab and tab['status'] != 'open':
                raise ValueError('This browser tab is closed. Open a new tab explicitly.')
            if action in OBSERVED_ACTIONS or args.get('ref'):
                if not tab['observation_id'] or args.get('observation_id') != tab['observation_id']:
                    raise ValueError('The observation is stale. Take a fresh snapshot before acting.')
                if args.get('ref') and args['ref'] not in json.loads(tab['refs']):
                    raise ValueError('This element was not present in the current observation.')
            if action == 'handoff':
                if not manual_browser_handoff_available(self.config, self.driver):
                    return {'ok': False, 'status': 'manual_handoff_unavailable', 'effect_known': True,
                            'manual_handoff': False,
                            'error': {'code': 'manual_browser_handoff_unavailable',
                                      'message': 'This server or headless browser has no window the user can take over. No local browser was opened and no login handoff was created.'},
                            'recovery': 'Use runtime user_help separately to ask for non-credential information or files the user chooses to share, or an alternative public source. They can download a document in their own browser and attach it here. Their own browser login does not authenticate this browser. Never request passwords, verification codes, cookies or session tokens.'}
                from .user_help import request_help
                await self._select(user_id, tab, force=True)
                await self.store.execute("UPDATE browser_tabs SET observation_id=NULL,refs='[]' WHERE user_id=?", (user_id,))
                return await request_help(self.store, session_id, run_id,
                    {'questions': args.get('questions'), 'reason': args.get('reason', '')})
            if run_id is not None and not await self.store.rows("SELECT 1 FROM runs WHERE id=? AND session_id=? AND status='running'", (run_id, session_id)):
                raise ValueError('Browser work requires its active source run.')
            operation_id = uid()
            safe_args = {**args}
            # The model request already records tool arguments. Avoid another
            # clear-text copy of form content in browser action bookkeeping.
            for key in ('text', 'value'):
                if key in safe_args:
                    safe_args[key] = {'characters': len(safe_args[key])}
            if 'url' in safe_args:
                safe_args['url'] = public_url(safe_args['url'])
            await self.store.execute('INSERT INTO browser_actions(id,session_id,run_id,tab_id,action,arguments,status,created_at) VALUES (?,?,?,?,?,?,\'running\',?)',
                (operation_id, session_id, run_id, tab['id'] if tab else None, action, json.dumps(safe_args, ensure_ascii=False), now()))
            result, dispatched = None, False
            try:
                if tab:
                    await self._select(user_id, tab)
                if action == 'open' and tab is None:
                    count = (await self.store.rows("SELECT COUNT(*) AS total FROM browser_tabs WHERE user_id=? AND status='open'", (user_id,)))[0]['total']
                    if count >= 12:
                        raise ValueError('Close an unused browser tab before opening another.')
                    if not identity:
                        await self.driver.start(user_id)
                        await self._identity(user_id)
                    # Establish ownership before any navigation. An interrupted
                    # creation can leave only an empty tab, never an untracked
                    # external form submission or URL side effect.
                    created = await self.driver.run(user_id, ['tab', 'new', 'about:blank'])
                    target = created.get('targetId') if isinstance(created, dict) else None
                    if not isinstance(target, str) or not re.fullmatch(r'[a-zA-Z0-9-]{1,100}', target):
                        raise BrowserDriverError('browser_protocol_error', 'No browser tab identity was returned.')
                    tab = {'id': uid(), 'target_id': target}
                    await self.store.execute('INSERT INTO browser_tabs(id,user_id,session_id,target_id,status,url,created_at,updated_at) VALUES (?,?,?,?,\'open\',?,?,?)',
                        (tab['id'], user_id, session_id, target, public_url(args['url']), now(), now()))
                    await self.store.execute('UPDATE browser_actions SET tab_id=? WHERE id=?', (tab['id'], operation_id))
                    dispatched = True
                    await self.driver.run(user_id, ['open', args['url']])
                elif action == 'snapshot':
                    pass
                elif action == 'upload':
                    records = []
                    for file_id in upload_ids(args):
                        rows = await self.store.rows("""SELECT f.* FROM file_entries f JOIN sessions s ON s.id=f.session_id
                            WHERE f.id=? AND f.user_id=? AND s.user_id=?""", (file_id, user_id, user_id))
                        if not rows:
                            raise ValueError('The upload file was not found in this account. Use runtime app action=list to discover files; never provide a filesystem path.')
                        records.append(rows[0])
                    kind = await self.driver.run(user_id, ['get', 'attr', args['ref'], 'type'])
                    if not isinstance(kind, dict) or str(kind.get('value', kind.get('attribute', ''))).lower() != 'file':
                        raise ValueError('Upload requires an observed file input. A custom chooser button or drag-only target is not supported.')
                    staged = await self._stage_upload(user_id, operation_id, records)
                    dispatched = True
                    selected = await self.driver.run(user_id, ['upload', args['ref'], *staged['paths']])
                    if (not isinstance(selected, dict) or type(selected.get('uploaded')) is not int
                            or selected['uploaded'] != len(records) or selected.get('selector') != args['ref']):
                        raise BrowserDriverError('browser_protocol_error', 'The file selection result could not be confirmed.')
                    result = {'files': staged['files'], 'bytes': staged['bytes'], 'input_selected': True,
                              'submission_verified': False,
                              'next': 'File selection may trigger an automatic website upload. Inspect the fresh page for its result before submitting or retrying; selection alone does not confirm website acceptance.'}
                elif action in ('screenshot', 'download'):
                    name = f"browser-{operation_id}.png" if action == 'screenshot' else f"{operation_id[:8]}-{args['name']}"
                    root, _ = self.driver.paths(user_id)
                    destination = root / 'outputs' / name
                    command = ['screenshot', str(destination)] if action == 'screenshot' else ['download', args['ref'], str(destination)]
                    dispatched = True
                    await self.driver.run(user_id, command)
                    await asyncio.to_thread(publish_browser_file, self.config, user_id, session_id, destination, name)
                    await discover_files(self.config, self.store, user_id, session_id)
                    files = await list_files(self.store, user_id, session_id)
                    file = next((row for row in files if row['name'] == name), None)
                    if file is None:
                        raise BrowserDriverError('browser_file_missing', 'The browser did not produce the requested file.')
                    result = {'file': file}
                else:
                    command = {'open': ['open', args.get('url', '')], 'close': ['tab', 'close', tab['target_id']],
                        'fill': ['fill', args.get('ref', ''), args.get('text', '')],
                        'select': ['select', args.get('ref', ''), args.get('value', '')],
                        'press': ['press', args.get('key', '')],
                        'scroll': ['scroll', args.get('direction', ''), str(args.get('pixels', 600))]}.get(action, [action, args.get('ref')] if action in REF_ACTIONS else [action])
                    if action == 'fill':
                        kind = await self.driver.run(user_id, ['get', 'attr', args['ref'], 'type'])
                        if isinstance(kind, dict) and str(kind.get('value', kind.get('attribute', ''))).lower() == 'password':
                            if not manual_browser_handoff_available(self.config, self.driver):
                                raise ValueError('Password entry is unavailable in this server or headless browser. There is no local window to open for login. Use user_help for non-credential information or files; never ask for passwords, verification codes, cookies or session tokens.')
                            raise ValueError('Let the user enter credentials in the managed browser; use handoff.')
                    dispatched = True
                    await self.driver.run(user_id, command)
                if action == 'close':
                    await self.store.execute("UPDATE browser_tabs SET status='closed',observation_id=NULL,refs='[]',updated_at=? WHERE id=?", (now(), tab['id']))
                    await self._discard_closed_uploads(user_id)
                    result = {'tab_id': tab['id'], 'closed': True}
                else:
                    result = {**(result or {}), **await self._observation(user_id, session_id, run_id, tab['id'], args.get('ref') if action == 'snapshot' else None)}
                await self.store.execute("UPDATE browser_actions SET status='complete',result=?,finished_at=? WHERE id=?", (json.dumps(result, ensure_ascii=False), now(), operation_id))
                return {'operation_id': operation_id, 'ok': True, **result}
            except asyncio.CancelledError:
                # A dispatched browser effect may have happened. Stop this tab and
                # invalidate its observation; never repeat the action automatically.
                if tab and dispatched:
                    try:
                        await self.driver.run(user_id, ['tab', 'close', tab['target_id']], timeout=5)
                    except (ValueError, OSError, TimeoutError):
                        pass
                await self._failed(operation_id, tab, 'interrupted', 'interrupted')
                raise
            except (ValueError, OSError, TimeoutError, HTTPException) as error:
                code = getattr(error, 'code', 'browser_timeout' if isinstance(error, TimeoutError) else 'browser_action_failed')
                await self._failed(operation_id, tab, 'unknown' if dispatched else 'failed', code)
                return {'operation_id': operation_id, 'ok': False, 'effect_known': not dispatched,
                        'error': {'code': code, 'message': str(error)[:1000]},
                        'recovery': 'Inspect actual browser state before retrying. No action was automatically replayed.'}

    async def _select(self, user_id, tab, force=False):
        inventory = await self.driver.run(user_id, ['tab', 'list'])
        if not force and isinstance(inventory, dict) and any(item.get('targetId') == tab['target_id'] and item.get('active')
                for item in inventory.get('tabs', []) if isinstance(item, dict)):
            return  # Even reselecting the same native tab clears its element refs.
        selected = await self.driver.run(user_id, ['tab', tab['target_id']])
        if not isinstance(selected, dict) or selected.get('targetId') != tab['target_id']:
            raise BrowserDriverError('browser_tab_changed', 'The browser tab changed. Observe it again.')

    async def _failed(self, operation_id, tab, status, code):
        def fail(db):
            db.execute('UPDATE browser_actions SET status=?,error_code=?,finished_at=? WHERE id=?', (status, code, now(), operation_id))
            if tab:
                db.execute("UPDATE browser_tabs SET observation_id=NULL,refs='[]',updated_at=? WHERE id=?", (now(), tab['id']))
        await self.store.call(fail)

    async def close(self):
        await self.driver.shutdown()
