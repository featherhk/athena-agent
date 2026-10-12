import asyncio
import json

import pytest

from athena.auth import Auth
from athena.computer import ComputerHost, validate
from athena.native_host import NativeHost
from athena.store import now, uid


class FakeNative:
    connected = True

    def __init__(self):
        self.calls = []
        self.fail = False
        self.pause = False
        self.entered = asyncio.Event()

    async def call(self, args):
        self.calls.append(args)
        self.entered.set()
        if self.pause:
            await asyncio.Event().wait()
        if self.fail:
            raise ConnectionError('lost reply')
        return {'status': 'complete', 'observation_id': uid()}


@pytest.fixture
async def computer(store):
    user, _ = await Auth(store).register('native-test', 'fixture-password')
    sessions = [await store.session(user_id=user['id']) for _ in range(2)]
    native = FakeNative()
    return ComputerHost(store, native), native, sessions


async def snapshot(host, session):
    return await host.operate({'action': 'snapshot', 'app_id': 'test.synthetic'}, session, None)


def click(observation):
    return {'action': 'invoke', 'observation_id': observation['observation_id'], 'ref': '@n1', 'native_action': 'AXPress'}


async def test_observed_action_consumes_reference_and_records_receipt(computer, store):
    host, native, (session, sibling) = computer
    view = await snapshot(host, session)
    with pytest.raises(ValueError, match='another conversation'):
        await host.operate(click(view), sibling, None)
    result = await host.operate(click(view), session, None)
    assert result['status'] == 'complete'
    with pytest.raises(ValueError, match='expired'):
        await host.operate(click(view), session, None)
    assert len(native.calls) == 2
    receipt = (await store.rows('SELECT * FROM computer_actions WHERE id=?', (result['operation_id'],)))[0]
    assert receipt['status'] == 'complete' and receipt['session_id'] == session
    assert len((await host.operate({'action': 'history'}, sibling, None))['actions']) == 0


async def test_lost_reply_is_durable_unknown_never_replayed(computer, store):
    host, native, (session, _) = computer
    view = await snapshot(host, session)
    native.fail = True
    result = await host.operate(click(view), session, None)
    assert result['status'] == 'unknown'
    with pytest.raises(ValueError, match='expired'):
        await host.operate(click(view), session, None)
    await store.initialize()
    assert len(native.calls) == 2
    assert (await store.rows('SELECT status FROM computer_actions WHERE id=?', (result['operation_id'],)))[0]['status'] == 'unknown'


async def test_stop_preserves_unknown_and_new_session_cannot_reuse(computer, store):
    host, native, (session, sibling) = computer
    view = await snapshot(host, session)
    native.pause = True
    native.entered.clear()
    task = asyncio.create_task(host.operate(click(view), session, None))
    await native.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await store.rows('SELECT status FROM computer_actions ORDER BY created_at DESC LIMIT 1'))[0]['status'] == 'unknown'
    with pytest.raises(ValueError, match='expired'):
        await host.operate(click(view), sibling, None)


async def test_expiry_and_interleaved_desktop_snapshots(computer):
    host, native, (session, sibling) = computer
    view = await snapshot(host, session)
    host.observations[view['observation_id']] = (session, -100)
    with pytest.raises(ValueError, match='expired'):
        await host.operate(click(view), session, None)
    view = await snapshot(host, session)
    await snapshot(host, sibling)
    with pytest.raises(ValueError, match='expired'):
        await host.operate(click(view), session, None)
    assert len(native.calls) == 3


async def test_restart_marks_admitted_native_action_unknown(computer, store):
    host, _, (session, _) = computer
    operation = uid()
    await store.execute('INSERT INTO computer_actions VALUES (?,?,?,?,?,?,?,?)', (operation, session, None, 'invoke', 'running', None, now(), None))
    await store.initialize()
    result = (await host.operate({'action': 'history'}, session, None))['actions'][0]
    assert result['status'] == 'unknown_after_restart'


async def test_no_native_host_does_not_claim_system_control(computer):
    host, _, (session, _) = computer
    host.native = None
    result = await host.operate({'action': 'status'}, session, None)
    assert result['supported'] is False and result['code'] == 'native_host_unavailable'


async def test_cancel_after_admission_commit_but_before_dispatch(computer, store, monkeypatch):
    host, native, (session, _) = computer
    committed = asyncio.Event()
    original = store.execute
    async def delayed(sql, args=()):
        value = await original(sql, args)
        if sql.startswith('INSERT INTO computer_actions'):
            committed.set()
            await asyncio.Event().wait()
        return value
    monkeypatch.setattr(store, 'execute', delayed)
    task = asyncio.create_task(snapshot(host, session))
    await committed.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert native.calls == []
    assert (await store.rows('SELECT status FROM computer_actions')) == [{'status': 'not_dispatched'}]


@pytest.mark.parametrize('args', [
    {'action': 'snapshot'}, {'action': 'snapshot', 'app_id': 'x', 'script': 'run'},
    {'action': 'invoke', 'ref': '@n1'}, {'action': 'click', 'x': 12, 'y': 20},
    {'action': 'status', 'request_permission': True},
])
def test_no_arbitrary_script_coordinate_or_permission_backdoor(args):
    with pytest.raises(ValueError):
        validate(args)


async def test_private_transport_pairs_out_of_order_replies_and_never_retries():
    reader, sent = asyncio.StreamReader(), []
    host = NativeHost(reader, sent.append)
    try:
        first = asyncio.create_task(host.call({'action': 'status'}))
        second = asyncio.create_task(host.call({'action': 'list'}))
        await asyncio.sleep(0)
        for request in reversed(sent[:]):
            reader.feed_data((json.dumps({'event': 'computer_result', 'id': request['id'], 'result': request['args']}) + '\n').encode())
        assert (await first)['action'] == 'status'
        assert (await second)['action'] == 'list'
        task = asyncio.create_task(host.call({'action': 'invoke'}))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert [v['event'] for v in sent] == ['computer_request'] * 3 + ['computer_cancel']
        assert sent[-1]['id'] == sent[-2]['id']
        assert host.pending == {}
    finally:
        await host.close()


async def test_transport_eof_fails_pending_without_resend():
    reader, sent = asyncio.StreamReader(), []
    host = NativeHost(reader, sent.append)
    task = asyncio.create_task(host.call({'action': 'invoke'}))
    await asyncio.sleep(0)
    reader.feed_eof()
    with pytest.raises(ConnectionError):
        await task
    assert len(sent) == 1 and not host.connected
    await host.close()
