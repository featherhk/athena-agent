"""Browser requirements: owned state, no speculative replay, recoverable outputs."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
from dataclasses import replace

import pytest

from athena.auth import Auth
from athena.browser import BrowserHost, publish_browser_file, public_url, validate_arguments
from athena.browser_driver import NativeBrowserDriver, BrowserDriverError, protocol_command
from athena.files import initialize_files


class FakeBrowser:
    def __init__(self, config):
        self.config, self.calls, self.tabs = config, [], {}
        self.headed = True
        self.uploads = []
        self.selected_files = []
        self.generation, self.active, self.live = 0, None, False
        self.fail, self.pause, self.entered = None, None, asyncio.Event()

    def available(self):
        return True

    def paths(self, user):
        return self.config.data_dir / 'browser-profiles' / user, self.config.data_dir / 'ipc' / user

    async def start(self, user):
        self.live = True
        self.generation += 1
        (self.paths(user)[0] / 'outputs').mkdir(parents=True, exist_ok=True)

    async def identity(self, user):
        return f'instance-{self.generation}' if self.live else None

    async def run(self, user, command, **kwargs):
        self.calls.append((user, command[:]))
        await asyncio.sleep(0)
        if command[0] == self.pause:
            self.entered.set()
            await asyncio.Event().wait()
        if command[0] == self.fail:
            raise BrowserDriverError('lost_response', 'The reply was lost.')
        match command:
            case ['tab', 'new', url]:
                self.active = f'target-{len(self.tabs) + 1}'
                self.tabs[self.active] = url
                return {'targetId': self.active}
            case ['tab', 'list']:
                return {'tabs': [{'targetId': target, 'active': target == self.active} for target in self.tabs]}
            case ['tab', 'close', target]:
                self.tabs.pop(target, None)
                return {}
            case ['tab', target]:
                assert target in self.tabs
                self.active = target
                return {'targetId': target}
            case ['open', url]:
                self.tabs[self.active] = url
                return {}
            case ['snapshot', *_]:
                return {'snapshot': 'button "Submit" [ref=e1]\ntextbox "Name" [ref=e2]\nbutton "Document" [ref=e3]',
                        'origin': self.tabs[self.active], 'refs': {'e1': {}, 'e2': {}, 'e3': {}}}
            case ['get', 'attr', _, 'id']:
                return {'value': 'documents'}
            case ['get', 'attr', ref, _]:
                return {'value': 'file' if ref == '@e3' else 'text'}
            case ['get', 'files', _]:
                return {'result': {'matched': 1, 'multiple': True, 'count': len(self.selected_files), 'files': self.selected_files}}
            case ['upload', ref, *paths]:
                self.uploads.append([Path(path).read_bytes() for path in paths])
                self.selected_files = [{'name': Path(path).name, 'size': Path(path).stat().st_size} for path in paths]
                return {'uploaded': len(paths), 'selector': ref}
            case ['screenshot', path] | ['download', _, path]:
                Path(path).write_bytes(b'synthetic-output')
                return {}
        return {}

    async def shutdown(self):
        self.tabs.clear()
        self.live = False


@pytest.fixture
async def browsing(config, store):
    await initialize_files(store)
    user, _ = await Auth(store).register('browser-owner', 'synthetic-password')
    other, _ = await Auth(store).register('browser-outsider', 'synthetic-password')
    sessions = [await store.session(user_id=who) for who in (user['id'], user['id'], other['id'])]
    driver = FakeBrowser(config)
    host = BrowserHost(config, store, driver)
    yield host, driver, sessions
    await host.close()


async def open_page(host, session, url='https://example.invalid/form'):
    result = await host.operate({'action': 'open', 'url': url}, session, None)
    assert result['ok'], result
    return result


async def act(host, session, page, action, **fields):
    return await host.operate({'action': action, 'tab_id': page['tab_id'],
                              'observation_id': page['observation_id'], **fields}, session, None)


async def test_effects_have_durable_receipts_and_new_observations(browsing, store):
    host, driver, (session, _, _) = browsing
    page = await open_page(host, session)
    filled = await act(host, session, page, 'fill', ref='@e2', text='--profile is literal text')
    assert filled['ok'] and filled['observation_id'] != page['observation_id']
    with pytest.raises(ValueError, match='stale'):
        await act(host, session, page, 'click', ref='@e1')
    clicked = await act(host, session, filled, 'click', ref='@e1')
    assert clicked['ok']
    rows = await store.rows('SELECT * FROM browser_actions ORDER BY created_at')
    assert [row['status'] for row in rows] == ['complete'] * 3
    assert json.loads(rows[-1]['result'])['observation_id'] == clicked['observation_id']
    assert (host.config.data_dir / 'artifacts' / f"{clicked['artifact_id']}.txt").read_text() == clicked['text']
    assert sum(cmd[0] == 'click' for _, cmd in driver.calls) == 1


async def test_universal_tool_uses_the_same_browser_contract(engine, browsing):
    host, _, (session, _, _) = browsing
    engine.tools.browser = host
    result = await engine.tools.execute('runtime', {'op': 'browser', 'args': {
        'action': 'open', 'url': 'https://example.invalid/form'}}, session, None)
    assert result['ok'] and result['observation_id']
    action = await engine.tools.execute('runtime', {'op': 'browser', 'args': {
        'action': 'fill', 'tab_id': result['tab_id'], 'observation_id': result['observation_id'],
        'ref': '@e2', 'text': 'ordinary input'}}, session, None)
    assert action['ok']


async def test_password_entry_is_handed_to_the_person_without_duplicate_audit_text(browsing, store, monkeypatch):
    monkeypatch.setattr('athena.capabilities.sys.platform', 'darwin')
    host, driver, (session, _, _) = browsing
    page = await open_page(host, session)
    original = driver.run
    async def password(user, command, **kwargs):
        if command[0] == 'get':
            return {'value': 'password'}
        return await original(user, command, **kwargs)
    monkeypatch.setattr(driver, 'run', password)
    result = await act(host, session, page, 'fill', ref='@e2', text='fixture-secret-not-an-actual-key')
    assert not result['ok'] and result['effect_known']
    assert 'handoff' in result['error']['message']
    audit = (await store.rows('SELECT arguments FROM browser_actions WHERE id=?', (result['operation_id'],)))[0]
    assert 'fixture-secret' not in audit['arguments']
    assert not any(command[0] == 'fill' for _, command in driver.calls)


async def test_native_service_environment_and_launch_flags_are_host_owned(config, monkeypatch):
    driver = NativeBrowserDriver(config, headed=False)
    driver.chrome = Path('/Applications/Fixture Chrome.app/Contents/MacOS/Fixture Chrome')
    monkeypatch.setattr(driver, 'available', lambda: True)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fixture-key-must-not-leak')
    for operation in ('STT', 'TTS'):
        monkeypatch.setenv('ATHENA_AUDIO_' + operation + '_API_KEY', 'fixture-key-must-not-leak')
        monkeypatch.setenv('ATHENA_AUDIO_' + operation + '_API_KEY_FILE', '/private/fixture-key-file')
    monkeypatch.setenv('AGENT_BROWSER_PROFILE', '/personal/profile')
    monkeypatch.setenv('AGENT_BROWSER_CDP', 'http://untrusted.invalid')
    captured = []
    class Process:
        def __init__(self):
            self.returncode = 0
            self.stdout = asyncio.StreamReader()
            self.stdout.feed_data(b'{"success":true,"data":{"active":false}}')
            self.stdout.feed_eof()
        async def wait(self):
            return 0
    async def spawn(*argv, **kwargs):
        captured.append((argv, kwargs))
        return Process()
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    assert await driver.identity('owner') is None
    argv, kwargs = captured[0]
    assert 'fixture-key-must-not-leak' not in repr((argv, kwargs))
    assert 'AGENT_BROWSER_PROFILE' not in kwargs['env'] and 'AGENT_BROWSER_CDP' not in kwargs['env']
    assert argv[argv.index('--profile') + 1] == str((config.data_dir / 'browser-profiles/owner/profile').resolve())
    assert '--no-sandbox' not in argv and argv[-2:] == ('session', 'info')
    assert set(kwargs['env']) <= {'PATH', 'HOME', 'USER', 'LANG', 'LC_ALL', 'TMPDIR', 'AGENT_BROWSER_SOCKET_DIR', 'AGENT_BROWSER_DEFAULT_TIMEOUT'}


async def test_conversation_account_and_fork_boundaries(browsing, store):
    host, driver, (session, sibling, outsider) = browsing
    page = await open_page(host, session)
    for other in (sibling, outsider, await store.fork(session)):
        assert not (await host.operate({'action': 'list'}, other, None))['tabs']
        with pytest.raises(ValueError, match='not found'):
            await act(host, other, page, 'click', ref='@e1')
    await open_page(host, sibling)
    # Native refs are process-wide: switching conversations requires observation.
    with pytest.raises(ValueError, match='stale'):
        await act(host, session, page, 'click', ref='@e1')
    refreshed = await act(host, session, page, 'snapshot')
    assert (await act(host, session, refreshed, 'click', ref='@e1'))['ok']


async def test_unknown_results_and_process_restart_never_replay(browsing, store):
    host, driver, (session, _, _) = browsing
    page = await open_page(host, session)
    driver.fail = 'click'
    result = await act(host, session, page, 'click', ref='@e1')
    assert not result['ok'] and not result['effect_known']
    row = (await store.rows('SELECT * FROM browser_actions WHERE id=?', (result['operation_id'],)))[0]
    assert row['status'] == 'unknown'
    history = await host.operate({'action': 'history', 'limit': 1}, session, None)
    assert history['actions'][0]['status'] == 'unknown' and history['next_offset'] == 1
    count = len(driver.calls)
    await store.initialize()
    assert len(driver.calls) == count
    driver.generation += 1  # A new daemon could reuse a short native tab label.
    tabs = (await host.operate({'action': 'list'}, session, None))['tabs']
    assert tabs[0]['status'] == 'closed'
    with pytest.raises(ValueError, match='Closed tabs'):
        await act(host, session, page, 'snapshot')


async def test_manual_tab_close_and_interruption_are_reconciled(browsing, store):
    host, driver, (session, _, _) = browsing
    page = await open_page(host, session)
    driver.pause = 'click'
    task = asyncio.create_task(act(host, session, page, 'click', ref='@e1'))
    await asyncio.wait_for(driver.entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await store.rows('SELECT status FROM browser_actions ORDER BY created_at DESC LIMIT 1'))[0]['status'] == 'interrupted'
    assert (await host.operate({'action': 'list'}, session, None))['tabs'][0]['status'] == 'closed'
    assert sum(cmd[0] == 'click' for _, cmd in driver.calls) == 1


async def test_concurrent_conversations_do_not_interleave_native_actions(browsing):
    host, driver, (session, sibling, _) = browsing
    first, second = await asyncio.gather(open_page(host, session), open_page(host, sibling))
    assert first['tab_id'] != second['tab_id']
    actions = [cmd[0] for _, cmd in driver.calls]
    assert actions == ['tab', 'tab', 'open', 'snapshot', 'tab', 'tab', 'open', 'snapshot']


async def test_outputs_are_files_and_handoff_is_a_persisted_wait(browsing, store, monkeypatch):
    monkeypatch.setattr('athena.capabilities.sys.platform', 'darwin')
    host, _, (session, _, _) = browsing
    page = await open_page(host, session)
    for action, fields in (('screenshot', {}), ('download', {'ref': '@e1', 'name': 'statement.csv'})):
        page = await act(host, session, page, action, **fields)
        assert page['ok'] and page['file']['source'] == 'output'
        assert page['file']['content_url'].startswith('/api/files/')
    run = await store.enqueue(session, 'Open the account page and let me log in.')
    await store.claim(session)
    result = await host.operate({'action': 'handoff', 'tab_id': page['tab_id'],
        'questions': [{'id': 'login', 'question': 'Log in in the browser, then reply done.'}]}, session, run)
    assert result['control']['wait_for_user'] == result['request']['id']
    assert (await store.rows('SELECT status FROM user_help WHERE run_id=?', (run,)))[0]['status'] == 'preparing'


async def test_cloud_handoff_does_not_create_an_impossible_login_wait(browsing, store):
    from athena.user_help import request_help
    host, driver, (session, _, _) = browsing
    host.config = replace(host.config, execution_socket='/fixture/broker.sock')
    page = await open_page(host, session)
    run = await store.enqueue(session, 'Download my bill from the account website.')
    await store.claim(session)
    before = list(driver.calls)
    result = await host.operate({'action': 'handoff', 'tab_id': page['tab_id'],
        'questions': [{'id': 'login', 'question': 'Log in in the browser I opened on your phone.'}]}, session, run)
    assert result['ok'] is False and result['effect_known'] is True
    assert result['error']['code'] == 'manual_browser_handoff_unavailable'
    assert 'No local browser was opened' in result['error']['message']
    assert 'Never request passwords' in result['recovery']
    # Read-only inventory is allowed; no pretend window selection/remote takeover.
    assert all(command == ['tab', 'list'] for _, command in driver.calls[len(before):])
    assert await store.rows('SELECT id FROM user_help WHERE run_id=?', (run,)) == []
    assert (await store.rows('SELECT status FROM runs WHERE id=?', (run,)))[0]['status'] == 'running'
    assert (await store.rows('SELECT observation_id FROM browser_tabs WHERE id=?', (page['tab_id'],)))[0]['observation_id'] == page['observation_id']
    status = await host.operate({'action': 'status'}, session, run)
    assert status['manual_handoff'] is False
    # Asking for a document remains a durable path in this same run.
    help_result = await request_help(store, session, run, {'questions': [
        {'id': 'bill', 'question': 'Could you attach the bill downloaded from your own browser? Do not send login credentials.'}]})
    assert help_result['control']['wait_for_user'] == help_result['request']['id']


@pytest.mark.parametrize('platform,headed,hosted,expected', [
    ('darwin', True, False, True), ('darwin', False, False, False),
    ('darwin', True, True, False), ('linux', True, False, False),
])
def test_capabilities_distinguish_browser_actions_from_manual_takeover(config, monkeypatch, platform, headed, hosted, expected):
    from athena.capabilities import execution_capabilities
    import athena.browser_driver as drivers
    driver = FakeBrowser(config)
    driver.headed = headed
    monkeypatch.setattr('athena.capabilities.sys.platform', platform)
    monkeypatch.setattr(drivers, 'browser_driver', lambda _: driver)
    if hosted:
        config = replace(config, execution_socket='/fixture/broker.sock')
    status = execution_capabilities(config)
    assert status['browser'] is True
    assert status['browser_manual_handoff'] is expected
    assert status['user_help'] is True


async def test_cloud_password_field_does_not_offer_unreachable_login_window(browsing, monkeypatch):
    host, driver, (session, _, _) = browsing
    host.config = replace(host.config, execution_socket='/fixture/broker.sock')
    page = await open_page(host, session)
    original = driver.run
    async def password(user, command, **kwargs):
        if command[0] == 'get':
            return {'value': 'password'}
        return await original(user, command, **kwargs)
    monkeypatch.setattr(driver, 'run', password)
    result = await act(host, session, page, 'fill', ref='@e2', text='synthetic-only')
    assert not result['ok'] and result['effect_known']
    assert 'no local window' in result['error']['message']
    assert 'never ask for passwords' in result['error']['message']
    assert not any(command[0] == 'fill' for _, command in driver.calls)


async def uploaded_record(host, store, session, name='document.pdf', content=b'synthetic PDF bytes'):
    from athena.files import discover_files, list_files
    owner = (await store.rows('SELECT user_id FROM sessions WHERE id=?', (session,)))[0]['user_id']
    directory = host.config.data_dir / 'files' / owner / session
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(content)
    await discover_files(host.config, store, owner, session)
    record = next(row for row in await list_files(store, owner, session) if row['name'] == name)
    return record, directory / name


async def test_upload_selects_owned_file_snapshots_and_retains_them_until_tab_close(browsing, store, engine):
    import hashlib
    host, driver, (session, sibling, _) = browsing
    first, source = await uploaded_record(host, store, session, '报价单.pdf', b'first')
    second, _ = await uploaded_record(host, store, sibling, 'sheet.xlsx', b'second')
    page = await open_page(host, session)
    engine.tools.browser = host
    result = await engine.tools.execute('runtime', {'op': 'browser', 'args': {
        'action': 'upload', 'tab_id': page['tab_id'], 'observation_id': page['observation_id'],
        'ref': '@e3', 'file_ids': [first['id'], second['id']]}}, session, None)
    assert result['ok'] and result['input_selected'] and result['submission_verified'] is False
    assert driver.uploads == [[b'first', b'second']]
    assert result['files'][0]['sha256'] == hashlib.sha256(b'first').hexdigest()
    paths = [Path(path) for _, command in driver.calls if command[0] == 'upload' for path in command[2:]]
    assert all('/browser-profiles/' in str(path) and '/uploads/' in str(path) for path in paths)
    assert paths[0].name == '报价单.pdf'
    source.write_bytes(b'changed later')
    assert paths[0].read_bytes() == b'first'  # A later form submission gets the selected version.
    await act(host, session, result, 'snapshot')
    assert paths[0].exists()
    current = await act(host, session, result, 'snapshot')
    await act(host, session, current, 'close')
    assert not any(path.exists() for path in paths)
    assert source.read_bytes() == b'changed later'


async def test_upload_rejects_other_accounts_stale_refs_and_non_file_controls(browsing, store):
    host, driver, (session, _, outsider) = browsing
    own, _ = await uploaded_record(host, store, session)
    foreign, _ = await uploaded_record(host, store, outsider)
    page = await open_page(host, session)
    rejected = await act(host, session, page, 'upload', ref='@e3', file_id=foreign['id'])
    assert not rejected['ok'] and rejected['effect_known']
    page = await act(host, session, page, 'snapshot')
    wrong_input = await act(host, session, page, 'upload', ref='@e2', file_id=own['id'])
    assert not wrong_input['ok'] and wrong_input['effect_known']
    with pytest.raises(ValueError, match='stale'):
        await act(host, session, page, 'upload', ref='@e3', file_id=own['id'])
    assert driver.uploads == []


async def test_upload_lost_result_keeps_snapshots_and_never_replays(browsing, store):
    host, driver, (session, _, _) = browsing
    record, _ = await uploaded_record(host, store, session)
    page = await open_page(host, session)
    driver.fail = 'upload'
    result = await act(host, session, page, 'upload', ref='@e3', file_id=record['id'])
    assert not result['ok'] and not result['effect_known']
    command = next(command for _, command in driver.calls if command[0] == 'upload')
    staged = Path(command[2])
    assert staged.exists()
    assert (await store.rows('SELECT status FROM browser_actions WHERE id=?', (result['operation_id'],)))[0]['status'] == 'unknown'
    await store.initialize()
    assert sum(command[0] == 'upload' for _, command in driver.calls) == 1
    assert staged.exists()
    driver.fail = None
    current = await act(host, session, page, 'snapshot')
    await act(host, session, current, 'close')
    assert not staged.exists()


async def test_upload_requires_the_expected_selection_receipt(browsing, store, monkeypatch):
    host, driver, (session, _, _) = browsing
    record, _ = await uploaded_record(host, store, session)
    page = await open_page(host, session)
    real_run = driver.run
    async def unrelated_receipt(owner, command, **kwargs):
        value = await real_run(owner, command, **kwargs)
        return {'uploaded': 0} if command[0] == 'upload' else value
    monkeypatch.setattr(driver, 'run', unrelated_receipt)
    result = await act(host, session, page, 'upload', ref='@e3', file_id=record['id'])
    assert not result['ok'] and not result['effect_known']
    assert result['error']['code'] == 'browser_protocol_error'
    assert len(driver.uploads) == 1 and 'input_selected' not in result


@pytest.mark.parametrize('observed', [
    {'matched': 1, 'count': 1, 'files': [{'name': 'sheet.xlsx', 'size': 6}]},
    {'matched': 1, 'count': 2, 'files': [{'name': '报价单.pdf', 'size': 0}, {'name': 'sheet.xlsx', 'size': 6}]},
    {'matched': 1, 'count': 0, 'files': []},
    {'matched': 0},
])
async def test_upload_does_not_trust_requested_count_over_actual_filelist(browsing, store, monkeypatch, observed):
    host, driver, (session, _, _) = browsing
    first, _ = await uploaded_record(host, store, session, '报价单.pdf', b'first')
    second, _ = await uploaded_record(host, store, session, 'sheet.xlsx', b'second')
    page = await open_page(host, session)
    real_run = driver.run
    async def changed_input(owner, command, **kwargs):
        value = await real_run(owner, command, **kwargs)
        if command[:2] == ['get', 'files'] and driver.uploads:
            return {'result': observed}
        return value
    monkeypatch.setattr(driver, 'run', changed_input)
    result = await act(host, session, page, 'upload', ref='@e3', file_ids=[first['id'], second['id']])
    assert not result['ok'] and not result['effect_known']
    assert result['error']['code'] == 'browser_upload_unverified' and 'input_selected' not in result
    assert len(driver.uploads) == 1
    assert (await store.rows('SELECT status FROM browser_actions WHERE id=?', (result['operation_id'],)))[0]['status'] == 'unknown'
    await store.initialize()
    assert len(driver.uploads) == 1


@pytest.mark.parametrize('probe', [{'matched': 0}, {'matched': 2}, {'matched': 1, 'multiple': False}])
async def test_upload_refuses_unverifiable_or_single_file_controls_before_dispatch(browsing, store, monkeypatch, probe):
    host, driver, (session, _, _) = browsing
    first, _ = await uploaded_record(host, store, session, 'first.pdf')
    second, _ = await uploaded_record(host, store, session, 'second.pdf')
    page = await open_page(host, session)
    real_run = driver.run
    async def unavailable_input(owner, command, **kwargs):
        value = await real_run(owner, command, **kwargs)
        return {'result': probe} if command[:2] == ['get', 'files'] else value
    monkeypatch.setattr(driver, 'run', unavailable_input)
    result = await act(host, session, page, 'upload', ref='@e3', file_ids=[first['id'], second['id']])
    assert not result['ok'] and result['effect_known'] and 'No upload was dispatched' in result['error']['message']
    assert not driver.uploads


@pytest.mark.parametrize('fields', [
    {'file_ids': []}, {'file_ids': ['same', 'same']}, {'file_ids': ['one'] * 11},
    {'file_id': '../secret'}, {'file_ids': ['/etc/passwd']}, {'file_id': 'one', 'file_ids': ['two']},
    {'path': '/private/file.pdf'}, {'file_ids': 'not-an-array'},
])
def test_upload_accepts_bounded_file_identities_not_paths(fields):
    with pytest.raises(ValueError):
        validate_arguments({'action': 'upload', 'ref': '@e1', **fields})


async def test_upload_no_follow_and_batch_limits_precede_dispatch(browsing, store, monkeypatch):
    from athena import browser_uploads
    host, driver, (session, _, _) = browsing
    record, source = await uploaded_record(host, store, session, content=b'12345')
    page = await open_page(host, session)
    monkeypatch.setattr(browser_uploads, 'MAX_UPLOAD_BYTES', 4)
    result = await act(host, session, page, 'upload', ref='@e3', file_id=record['id'])
    assert not result['ok'] and result['effect_known']
    owner = (await store.rows('SELECT user_id FROM sessions WHERE id=?', (session,)))[0]['user_id']
    assert browser_uploads.staged_operations(host.config, owner) == []
    monkeypatch.setattr(browser_uploads, 'MAX_UPLOAD_BYTES', 40 * 1024 * 1024)
    victim = source.parent / 'real.txt'
    victim.write_bytes(b'not selected')
    source.unlink()
    source.symlink_to(victim)
    current = await act(host, session, page, 'snapshot')
    result = await act(host, session, current, 'upload', ref='@e3', file_id=record['id'])
    assert not result['ok'] and result['effect_known']
    assert driver.uploads == [] and victim.read_bytes() == b'not selected'


async def test_upload_staging_rejects_hardlinks_and_destination_links(browsing, store, tmp_path):
    from athena.browser_uploads import stage_upload
    from fastapi import HTTPException
    host, _, (session, _, _) = browsing
    record, source = await uploaded_record(host, store, session, content=b'selected')
    owned = (await store.rows('SELECT * FROM file_entries WHERE id=?', (record['id'],)))[0]
    hardlink = source.with_name('hardlink.pdf')
    os.link(source, hardlink)
    with pytest.raises(HTTPException):
        stage_upload(host.config, owned['user_id'], 'hardlink-action', [owned])
    hardlink.unlink()
    area = host.config.data_dir / 'browser-profiles' / owned['user_id'] / 'uploads'
    area.rmdir()
    outside = tmp_path / 'outside-staging'
    outside.mkdir()
    area.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        stage_upload(host.config, owned['user_id'], 'symlink-action', [owned])
    assert list(outside.iterdir()) == [] and source.read_bytes() == b'selected'


async def test_upload_staging_quota_preserves_already_selected_files(browsing, store, monkeypatch):
    from athena import browser_uploads
    host, _, (session, _, _) = browsing
    record, _ = await uploaded_record(host, store, session, content=b'1234')
    owned = (await store.rows('SELECT * FROM file_entries WHERE id=?', (record['id'],)))[0]
    monkeypatch.setattr(browser_uploads, 'MAX_STAGED_BYTES', 6)
    existing = browser_uploads.stage_upload(host.config, owned['user_id'], 'first-action', [owned])
    with pytest.raises(ValueError, match='staging is full'):
        browser_uploads.stage_upload(host.config, owned['user_id'], 'second-action', [owned])
    assert browser_uploads.staged_operations(host.config, owned['user_id']) == ['first-action']
    assert Path(existing['paths'][0]).read_bytes() == b'1234'


@pytest.mark.parametrize('copy_fails', [False, True])
async def test_stop_during_upload_copy_drains_worker_and_never_dispatches(browsing, store, monkeypatch, copy_fails):
    import threading
    import athena.browser as browser
    from athena.browser_uploads import staged_operations
    host, driver, (session, _, _) = browsing
    record, _ = await uploaded_record(host, store, session)
    page = await open_page(host, session)
    entered, release = threading.Event(), threading.Event()
    real_copy = browser.stage_upload
    def slow_copy(*args):
        entered.set()
        assert release.wait(3)
        if copy_fails:
            raise ValueError('Synthetic copy failure during stop')
        return real_copy(*args)
    monkeypatch.setattr(browser, 'stage_upload', slow_copy)
    task = asyncio.create_task(act(host, session, page, 'upload', ref='@e3', file_id=record['id']))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()  # Repeated stop must not release ownership while a thread writes.
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    owner = (await store.rows('SELECT user_id FROM sessions WHERE id=?', (session,)))[0]['user_id']
    assert staged_operations(host.config, owner) == [] and driver.uploads == []
    rows = await store.rows("SELECT status FROM browser_actions WHERE action='upload'")
    assert rows == [{'status': 'interrupted'}]


def test_upload_native_protocol_does_not_turn_filenames_into_flags():
    packet = protocol_command(['upload', '@e2', '/owned/--profile.pdf', '/owned/汉字.pdf'])
    assert packet['action'] == 'upload' and packet['selector'] == '@e2'
    assert packet['files'] == ['/owned/--profile.pdf', '/owned/汉字.pdf']


def test_filelist_probe_json_encodes_dom_identifiers_and_exposes_no_generic_eval():
    identifier = '\";fetch("https://invalid.example");//\n\\雅典娜'
    packet = protocol_command(['get', 'files', identifier])
    encoded = packet['script'].split('const identifier = ', 1)[1].split(';\n', 1)[0]
    assert json.loads(encoded) == identifier
    assert packet['action'] == 'evaluate' and 'input.files' in packet['script']
    assert identifier not in packet['script']
    with pytest.raises(ValueError):
        protocol_command(['eval', identifier])
    with pytest.raises(ValueError):
        validate_arguments({'action': 'get', 'files': identifier})


async def test_recovery_invalidates_only_newly_interrupted_actions(browsing, store):
    host, _, (session, _, _) = browsing
    page = await open_page(host, session)
    await store.execute("UPDATE browser_actions SET status='running' WHERE id=?", (page['operation_id'],))
    await store.initialize()
    assert (await store.rows('SELECT status FROM browser_actions'))[0]['status'] == 'unknown_after_restart'
    refreshed = await act(host, session, page, 'snapshot')
    await store.initialize()
    assert (await store.rows('SELECT observation_id FROM browser_tabs'))[0]['observation_id'] == refreshed['observation_id']


def test_file_publication_rejects_links_oversize_and_overwrite(config, tmp_path):
    original = tmp_path / 'original'
    original.write_bytes(b'original')
    link = tmp_path / 'link'
    link.symlink_to(original)
    with pytest.raises(OSError):
        publish_browser_file(config, 'user', 'session', link, 'result.txt')
    link.unlink()
    os.link(original, link)
    with pytest.raises(ValueError, match='regular file'):
        publish_browser_file(config, 'user', 'session', link, 'result.txt')
    link.unlink()
    publish_browser_file(config, 'user', 'session', original, 'result.txt')
    original.write_bytes(b'new')
    with pytest.raises(FileExistsError):
        publish_browser_file(config, 'user', 'session', original, 'result.txt')
    assert (config.data_dir / 'files/user/session/result.txt').read_bytes() == b'original'
    with original.open('wb') as handle:
        handle.truncate(21 * 1024 * 1024)
    with pytest.raises(ValueError):
        publish_browser_file(config, 'user', 'session', original, 'large.txt')


def test_protocol_text_is_data_and_forbids_extra_driver_capabilities():
    text = '--profile /someone/else\n$(echo literal)'
    assert protocol_command(['fill', '@e1', text])['value'] == text
    with pytest.raises(ValueError):
        protocol_command(['eval', 'document.cookie'])
    for args in ({'action': 'open', 'url': 'file:///private/data'},
                 {'action': 'open', 'url': 'https://user:secret@example.com'},
                 {'action': 'download', 'ref': '@e1', 'name': '../escape'},
                 {'action': 'click', 'ref': 'button'}, {'action': 'status', 'profile': '/tmp'}):
        with pytest.raises(ValueError):
            validate_arguments(args)
    assert public_url('https://u:p@example.com:123/a?token=secret&search=hello#private') == 'https://example.com:123/a?token=%5Bredacted%5D&search=hello'


@pytest.mark.parametrize('reply', [b'', b'{invalid}\n'])
async def test_native_transport_never_resends_a_lost_response(config, socket_dir, monkeypatch, reply):
    driver = NativeBrowserDriver(config)
    monkeypatch.setattr(driver, 'paths', lambda user: (socket_dir, socket_dir))
    received = []
    async def handle(reader, writer):
        received.append(json.loads(await reader.readline()))
        writer.write(reply)
        await writer.drain()
        writer.close()
        await writer.wait_closed()
    async with await asyncio.start_unix_server(handle, path=socket_dir / 'athena.sock'):
        with pytest.raises(BrowserDriverError, match='could not be confirmed'):
            await driver.run('owner', ['click', '@e1'])
    assert len(received) == 1 and received[0]['action'] == 'click'


@pytest.fixture
def socket_dir():
    # macOS Unix sockets have a ~100-byte path limit; pytest's names are longer.
    with tempfile.TemporaryDirectory(prefix='athena-ipc-', dir='/tmp') as path:
        yield Path(path)


async def test_native_transport_cancellation_sends_once(config, socket_dir, monkeypatch):
    driver = NativeBrowserDriver(config)
    monkeypatch.setattr(driver, 'paths', lambda user: (socket_dir, socket_dir))
    received, entered, disconnected = [], asyncio.Event(), asyncio.Event()
    async def handle(reader, writer):
        received.append(json.loads(await reader.readline()))
        entered.set()
        await reader.read()
        writer.close()
        await writer.wait_closed()
        disconnected.set()
    async with await asyncio.start_unix_server(handle, path=socket_dir / 'athena.sock'):
        task = asyncio.create_task(driver.run('owner', ['click', '@e1']))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(disconnected.wait(), 2)
    assert len(received) == 1
