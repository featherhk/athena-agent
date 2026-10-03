"""Hosted execution contracts: ownership, fixed containers, stop and no replay."""
import asyncio
import base64
import json
from dataclasses import replace
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
from types import SimpleNamespace

import pytest

from athena.execution_broker import Broker, LABEL, ResourceLimitError, validate_request
from athena.execution_client import control, response_object
from athena.protection import protected_command
from athena.store import Store
from athena.browser_driver import ContainerBrowserDriver, browser_driver


def request(**changes):
    return {"op": "exec", "id": "one", "owner": "alice", "timeout": 2,
            "mode": "command", "cwd": "/data/files/alice/session", "command": "printf result", "stdin": "", **changes}


@pytest.fixture
def socket_path():
    with tempfile.TemporaryDirectory(prefix="athena-ipc-", dir="/tmp") as directory:
        yield str(Path(directory) / "broker.sock")


@pytest.mark.parametrize("changes", [
    {"owner": "../bob"}, {"id": "-bad/identity"}, {"cwd": "/data/files/bob/session"},
    {"cwd": "/data/files/alice/../bob"}, {"cwd": "/data/athena.sqlite3"},
    {"mount": "/var/run/docker.sock"}, {"image": "malicious"}, {"timeout": True},
    {"timeout": 301}, {"mode": "privileged"}, {"command": "a\0b"},
    {"stdin": None}, {"stdin": []}, {"stdin": "not base64!"},
    {"stdin": base64.b64encode(b"x" * (1024 * 1024 + 1)).decode()},
])
def test_worker_cannot_select_other_accounts_daemon_flags_or_unbounded_inputs(changes):
    with pytest.raises(ValueError):
        validate_request(request(**changes))


def test_policy_and_browser_have_distinct_fixed_scopes():
    validate_request(request(mode="policy", cwd="/data/extensions/alice/policy/a/1"))
    with pytest.raises(ValueError):
        validate_request(request(mode="policy"))
    for command in (["open", "http://example.invalid"], ["eval", "steal()"], ["--no-sandbox"]):
        with pytest.raises(ValueError):
            validate_request({"op": "browser_control", "owner": "alice", "command": command})


def test_container_boundary_has_no_host_or_control_plane_mounts(tmp_path):
    broker = Broker(tmp_path, "/host/athena-data", "fixed-image", "fixed-network", None)
    args = broker.limits("job")
    assert args[args.index("--user") + 1] == "10001:10001"
    assert "--read-only" in args and "no-new-privileges:true" in args
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert "--privileged" not in args and "host" not in args
    assert broker.mount("files", "alice") == "type=bind,src=/host/athena-data/files/alice,dst=/data/files/alice"
    assert broker.mount("extensions", "alice", readonly=True).endswith(",readonly")
    assert "none" in broker.limits("policy", policy=True)
    (tmp_path / "files/bob").symlink_to(tmp_path / "files/alice")
    with pytest.raises(ValueError, match="symbolic"):
        broker.mount("files", "bob")


async def test_broker_response_is_sent_once_and_lost_response_is_not_retried(socket_path):
    seen = []
    async def disconnected(reader, writer):
        seen.append(json.loads(await reader.readline()))
        writer.close()
        await writer.wait_closed()
    path = socket_path
    server = await asyncio.start_unix_server(disconnected, path)
    async with server:
        with pytest.raises(ValueError):
            await control(path, {"op": "browser_control", "owner": "alice", "command": ["tab", "list"]})
    assert len(seen) == 1


async def test_container_driver_uses_host_broker_not_native_macos_binary(config, monkeypatch, socket_path):
    seen = []
    path = socket_path
    async def respond(reader, writer):
        seen.append(json.loads(await reader.readline()))
        writer.write(b'{"ok":true,"data":{"active":false}}\n')
        await writer.drain()
        writer.close()
        await writer.wait_closed()
    server = await asyncio.start_unix_server(respond, path)
    monkeypatch.setattr(sys, "platform", "linux")
    driver = browser_driver(replace(config, execution_socket=path))
    assert isinstance(driver, ContainerBrowserDriver) and driver.available()
    async with server:
        assert await driver.identity("alice") is None
    assert seen == [{"op": "browser_control", "owner": "alice", "command": ["session", "info"]}]


def test_linux_commands_keep_protection_and_timeout_and_account_scope(config, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(ValueError, match="停用"):
        protected_command("whoami", config, [], [])
    config = replace(config, execution_socket="/private/broker.sock")
    files = config.data_dir / "files/alice"
    command = protected_command("printf hello", config, [files, config.data_dir / "extensions/alice"], [files], timeout=7)
    assert command[-5:] == ["/private/broker.sock", "alice", "7", "command", "printf hello"]
    with pytest.raises(ValueError, match="one account"):
        protected_command("whoami", config, [files, config.data_dir / "files/bob"], [files])


async def test_duplicate_dispatch_is_refused_and_restart_preserves_uncertainty(tmp_path):
    store = Store(tmp_path / "broker.sqlite3")
    broker = Broker(tmp_path, "/host", "image", "net", store)
    calls = []
    async def docker(*args, **kwargs):
        calls.append(args)
        return 0, b"leftover-id\n" if args[0] == "ps" else b""
    broker.docker_call = docker
    try:
        await broker.initialize()
        await store.execute("INSERT INTO executions VALUES ('one','alice','hash','running','before',NULL,NULL)")
        await store.execute("INSERT INTO executions VALUES ('two','alice','hash','cleanup_unknown','before',NULL,NULL)")
        reader = asyncio.StreamReader()
        with pytest.raises(sqlite3.IntegrityError):
            await broker.execute(request(), reader, None)
        assert not any(args[0] in ("create", "start") for args in calls)
        await broker.initialize()
        assert (await store.rows("SELECT status FROM executions WHERE id='one'"))[0]["status"] == "unknown_after_restart"
        assert (await store.rows("SELECT status FROM executions WHERE id='two'"))[0]["status"] == "unknown_after_restart"
        assert ("rm", "-f", "leftover-id") in calls
        assert LABEL in calls[0][calls[0].index("--filter") + 1]
    finally:
        await store.close()


class MemoryWriter:
    def __init__(self):
        self.frames = []
        self.output = asyncio.Event()

    def is_closing(self):
        return False

    def write(self, value):
        frame = json.loads(value)
        self.frames.append(frame)
        if frame.get("type") == "output":
            self.output.set()

    async def drain(self):
        pass


@pytest.fixture
async def broker(tmp_path):
    # macOS's /var aliases /private/var; the production broker uses /data.
    root = tmp_path.resolve()
    (root / "files/alice/session").mkdir(parents=True)
    store = Store(root / "broker.sqlite3")
    value = Broker(root, "/host", "image", "net", store)
    value.calls = []
    async def docker(*args, **kwargs):
        value.calls.append(args)
        return 0, b""
    value.docker_call = docker
    await value.initialize()
    try:
        yield value
    finally:
        await store.close()


async def test_disconnected_queued_command_never_dispatches(broker):
    broker.slots = asyncio.Semaphore(0)
    reader, writer = asyncio.StreamReader(), MemoryWriter()
    task = asyncio.create_task(broker.execute(request(), reader, writer))
    reader.feed_eof()
    await asyncio.wait_for(task, 1)
    assert not any(call[0] == "create" for call in broker.calls)
    assert (await broker.store.rows("SELECT status FROM executions"))[0]["status"] == "cancelled"


async def test_deadline_includes_time_waiting_for_capacity(broker):
    broker.slots = asyncio.Semaphore(0)
    reader, writer = asyncio.StreamReader(), MemoryWriter()
    await asyncio.wait_for(broker.execute(request(timeout=1), reader, writer), 2)
    assert writer.frames[-1]["code"] == 124
    assert not any(call[0] == "create" for call in broker.calls)
    assert (await broker.store.rows("SELECT status FROM executions"))[0]["status"] == "timeout"


async def test_cancel_during_create_waits_for_known_identity_before_removal(broker, monkeypatch):
    creating, created, removing, removed = [asyncio.Event() for _ in range(4)]
    broker.slots = asyncio.Semaphore(1)
    async def docker(*args, **kwargs):
        broker.calls.append(args)
        if args[0] == "create":
            creating.set()
            await created.wait()
        if args[0] == "rm":
            removing.set()
            await removed.wait()
        return 0, b""
    async def unexpected_start(*args, **kwargs):
        raise AssertionError("Cancelled command reached Docker start.")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", unexpected_start)
    broker.docker_call = docker
    reader, writer = asyncio.StreamReader(), MemoryWriter()
    task = asyncio.create_task(broker.execute(request(), reader, writer))
    await asyncio.wait_for(creating.wait(), 1)
    reader.feed_eof()
    await asyncio.sleep(0)
    assert not any(call[0] == "rm" for call in broker.calls)
    assert broker.slots.locked()
    created.set()
    await asyncio.wait_for(removing.wait(), 1)
    assert broker.slots.locked(), "Capacity must remain reserved until the container is removed."
    removed.set()
    await asyncio.wait_for(task, 1)
    assert not broker.slots.locked()
    assert (await broker.store.rows("SELECT status FROM executions"))[0]["status"] == "cancelled"


async def test_cleanup_failure_reaps_transport_and_blocks_new_side_effects(broker, monkeypatch):
    original_create = asyncio.create_subprocess_exec
    processes = []
    async def fake_start(*args, **kwargs):
        assert args[1] == "start"
        process = await original_create(sys.executable, "-u", "-c", "print('started'); import time; time.sleep(30)", **kwargs)
        processes.append(process)
        return process
    async def docker(*args, **kwargs):
        broker.calls.append(args)
        if args[0] == "rm":
            raise TimeoutError("Docker is unavailable")
        return 0, b""
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_start)
    broker.docker_call = docker
    reader, writer = asyncio.StreamReader(), MemoryWriter()
    task = asyncio.create_task(broker.execute(request(), reader, writer))
    await asyncio.wait_for(writer.output.wait(), 2)
    reader.feed_eof()
    await asyncio.wait_for(task, 2)
    assert processes[0].returncode is not None
    row = (await broker.store.rows("SELECT * FROM executions WHERE id='one'"))[0]
    assert row["status"] == "cleanup_unknown" and row["finished_at"] and row["exit_code"] == 125
    next_writer = MemoryWriter()
    await broker.execute(request(id="two"), asyncio.StreamReader(), next_writer)
    assert next_writer.frames[-1]["code"] == 125
    assert "recovery" in next_writer.frames[-1]["error"]
    assert len(processes) == 1


async def test_control_transport_rejects_unbounded_output(tmp_path, monkeypatch):
    import athena.execution_broker as module
    monkeypatch.setattr(module, "FRAME_LIMIT", 64)
    value = Broker(tmp_path, "/host", "image", "net", None, docker=sys.executable)
    with pytest.raises(ValueError, match="limit"):
        await asyncio.wait_for(value.docker_call("-u", "-c", "print('x' * 1024); import time; time.sleep(30)"), 2)


@pytest.mark.parametrize("raw", [b"", b"[]\n", b"true\n", b"null\n", b'{"ok":true}', b"invalid\n"])
def test_execution_protocol_rejects_non_object_or_incomplete_frames(raw):
    with pytest.raises(ValueError, match="repeat"):
        response_object(raw)


def test_config_replacement_cannot_follow_a_worker_symlink(tmp_path):
    value = Broker(tmp_path, "/host", "image", "net", None)
    root = value.directory("browser-profiles", "alice")
    unrelated = tmp_path / "unrelated-private-data"
    unrelated.write_text("unchanged")
    (root / "driver-config.json").symlink_to(unrelated)
    value.browser_config(root)
    assert unrelated.read_text() == "unchanged"
    assert not (root / "driver-config.json").is_symlink()
    config = json.loads((root / "driver-config.json").read_text())
    assert config["profile"] == "/data/browser-profiles/alice/profile"
    assert (root / "driver-config.json").stat().st_mode & 0o777 == 0o600


def test_network_guard_requires_this_boot_and_explicit_ipv6_off(tmp_path, monkeypatch):
    proof = tmp_path / "network-ready.json"
    read_text = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda path, *args, **kwargs:
        "current-boot\n" if str(path) == "/proc/sys/kernel/random/boot_id" else read_text(path, *args, **kwargs))
    value = Broker(tmp_path, "/host", "image", "net", None, network_ready=proof)
    for data in (None, [], {"boot_id": "old-boot", "network": "net", "ipv6": False},
                 {"boot_id": "current-boot", "network": "other-net", "ipv6": False},
                 {"boot_id": "current-boot", "network": "net", "ipv6": 0}):
        if data is not None:
            proof.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="protection"):
            value.require_network()
    proof.write_text(json.dumps({"boot_id": "current-boot", "network": "net", "ipv6": False}))
    value.require_network()


async def test_network_guard_is_rechecked_before_command_dispatch(broker, tmp_path):
    broker.network_ready = tmp_path / "missing-proof.json"
    writer = MemoryWriter()
    await broker.execute(request(), asyncio.StreamReader(), writer)
    assert writer.frames[-1]["code"] == 125 and "protection" in writer.frames[-1]["error"]
    assert not any(call[0] == "create" for call in broker.calls)


async def test_withdrawn_network_proof_after_create_prevents_container_start(broker, monkeypatch):
    checks = 0
    def guard():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise ValueError("Execution network protection was withdrawn.")
    async def unexpected_start(*args, **kwargs):
        raise AssertionError("Network protection was not checked before starting the container.")
    broker.require_network = guard
    monkeypatch.setattr(asyncio, "create_subprocess_exec", unexpected_start)
    writer = MemoryWriter()
    await broker.execute(request(), asyncio.StreamReader(), writer)
    assert any(call[0] == "create" for call in broker.calls)
    assert ("rm", "-f", "athena-1-exec-one") in broker.calls
    assert checks == 2 and writer.frames[-1]["code"] == 125


class BrowserContainers:
    def __init__(self):
        self.containers, self.removed, self.launches = {}, [], []

    async def __call__(self, *args, **kwargs):
        await asyncio.sleep(0)  # Make separate-account admission races observable.
        if args[0] == "inspect":
            return (0, b"true") if args[-1] in self.containers else (1, b"")
        if args[0] == "ps":
            return 0, "\n".join(self.containers).encode()
        if args[0] == "run":
            self.launches.append(args)
            name = args[args.index("--name") + 1]
            self.containers[name] = False
            return 0, name.encode()
        if args[0] == "rm":
            self.removed.append(args[-1])
            self.containers.pop(args[-1])
            return 0, b""
        assert args[0] == "exec"
        name = args[1]
        if args[-2:] == ("tab", "list"):
            self.containers[name] = True
            data = {"tabs": []}
        else:
            assert args[-2:] == ("session", "info")
            data = {"active": self.containers[name]}
        return 0, json.dumps({"success": True, "data": data}).encode()


async def test_browser_capacity_is_atomic_across_accounts_and_closed_slots_reclaimed(tmp_path):
    value = Broker(tmp_path, "/host", "image", "net", None)
    docker = BrowserContainers()
    value.docker_call = docker
    def start(owner):
        return value.browser({"op": "browser_control", "owner": owner, "command": ["tab", "list"]})
    results = await asyncio.gather(*(start(owner) for owner in ("alice", "bob", "carol")), return_exceptions=True)
    assert sum(isinstance(result, ValueError) for result in results) == 1
    assert len(docker.containers) == 2
    docker.containers["athena-1-browser-alice"] = False  # Explicit close or driver idle timeout.
    await start("carol")
    assert set(docker.containers) == {"athena-1-browser-bob", "athena-1-browser-carol"}
    assert docker.removed == ["athena-1-browser-alice"]
    # Chromium resolves non-ASCII upload filenames using its process locale.
    # A POSIX locale silently omitted the Chinese PDF in the Linux fixture.
    assert all('LANG=C.UTF-8' in call and 'LC_ALL=C.UTF-8' in call for call in docker.launches)


async def test_uncertain_browser_identity_does_not_authorize_reclamation(tmp_path):
    value = Broker(tmp_path, "/host", "image", "net", None)
    docker = BrowserContainers()
    docker.containers = {"athena-1-browser-alice": True, "athena-1-browser-bob": True}
    value.docker_call = docker
    async def unavailable(*args):
        raise TimeoutError("Uncertain driver state")
    value.browser_command = unavailable
    with pytest.raises(ValueError, match="slots"):
        await value.browser({"op": "browser_control", "owner": "carol", "command": ["tab", "list"]})
    assert not docker.removed and len(docker.containers) == 2


def test_resource_scan_does_not_follow_links_and_bounds_bytes_entries_and_depth(tmp_path, monkeypatch):
    import athena.execution_broker as module
    monkeypatch.setattr(module, "MIN_FREE_BYTES", 0)
    monkeypatch.setattr(module, "ACCOUNT_FILE_BYTES", 8)
    value = Broker(tmp_path, "/host", "image", "net", None)
    root = value.directory("files", "alice")
    outside = tmp_path / "outside"
    outside.write_bytes(b"x" * 20)
    (root / "link").symlink_to(outside)
    (root / "small").write_bytes(b"123")
    value.check_resources("alice")
    (root / "small").write_bytes(b"123456789")
    with pytest.raises(ResourceLimitError, match="512 MiB"):
        value.check_resources("alice")
    (root / "small").unlink()
    monkeypatch.setattr(module, "ACCOUNT_FILE_ENTRIES", 1)
    (root / "second").touch()
    with pytest.raises(ResourceLimitError, match="entry"):
        value.check_resources("alice")
    (root / "second").unlink()
    monkeypatch.setattr(module, "ACCOUNT_FILE_ENTRIES", 100)
    monkeypatch.setattr(module, "ACCOUNT_FILE_DEPTH", 2)
    (root / "a/b/c").mkdir(parents=True)
    with pytest.raises(ResourceLimitError, match="directory"):
        value.check_resources("alice")


async def test_low_disk_space_prevents_dispatch_with_durable_resource_status(broker, monkeypatch):
    import athena.execution_broker as module
    monkeypatch.setattr(module.os, "statvfs", lambda _: SimpleNamespace(f_bavail=0, f_frsize=4096))
    writer = MemoryWriter()
    await broker.execute(request(), asyncio.StreamReader(), writer)
    assert not any(call[0] == "create" for call in broker.calls)
    row = (await broker.store.rows("SELECT status,exit_code FROM executions"))[0]
    assert row == {"status": "resource_limit", "exit_code": 125}
    assert "2 GiB" in writer.frames[-1]["error"]


async def test_periodic_low_disk_check_stops_running_container_off_event_loop(broker, monkeypatch):
    import athena.execution_broker as module
    low_space = threading.Event()
    event_loop_thread, checking_threads = threading.get_ident(), []
    def space(_):
        checking_threads.append(threading.get_ident())
        return SimpleNamespace(f_bavail=0 if low_space.is_set() else 1024 * 1024, f_frsize=4096)
    monkeypatch.setattr(module.os, "statvfs", space)
    create = asyncio.create_subprocess_exec
    processes = []
    async def fake_start(*args, **kwargs):
        process = await create(sys.executable, "-u", "-c", "print('started'); import time; time.sleep(30)", **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_start)
    writer = MemoryWriter()
    task = asyncio.create_task(broker.execute(request(timeout=5), asyncio.StreamReader(), writer))
    await asyncio.wait_for(writer.output.wait(), 2)
    low_space.set()
    await asyncio.wait_for(task, 2)
    assert len(checking_threads) >= 2 and event_loop_thread not in checking_threads
    assert processes[0].returncode is not None
    assert ("rm", "-f", "athena-1-exec-one") in broker.calls
    assert (await broker.store.rows("SELECT status FROM executions"))[0]["status"] == "resource_limit"
    assert "2 GiB" in writer.frames[-1]["error"]


async def test_disk_verification_failure_stops_conservatively(broker, monkeypatch):
    import athena.execution_broker as module
    def unreadable(_):
        raise PermissionError("Cannot read disk metadata")
    monkeypatch.setattr(module.os, "statvfs", unreadable)
    writer = MemoryWriter()
    await broker.execute(request(), asyncio.StreamReader(), writer)
    assert not any(call[0] == "create" for call in broker.calls)
    assert "could not be verified" in writer.frames[-1]["error"]
    assert (await broker.store.rows("SELECT status FROM executions"))[0]["status"] == "resource_limit"


async def test_policy_busy_does_not_wait_for_command_slots(broker):
    broker.slots = asyncio.Semaphore(0)
    writer = MemoryWriter()
    await asyncio.wait_for(broker.execute(request(mode="policy", cwd="/data/extensions/alice/policy/x/1"),
        asyncio.StreamReader(), writer), 1)
    assert writer.frames[-1]["status"] == "busy"
    assert not any(call[0] == "create" for call in broker.calls)


async def test_policy_startup_does_not_consume_script_deadline(broker, monkeypatch):
    (broker.data / "extensions/alice/policy/x/1").mkdir(parents=True)
    original_create = asyncio.create_subprocess_exec
    async def start(*args, **kwargs):
        return await original_create(sys.executable, "-c", "import time; time.sleep(0.5); print('[]')", **kwargs)
    async def docker(*args, **kwargs):
        broker.calls.append(args)
        if args[0] == "create":
            await asyncio.sleep(0.7)
        if args[0] == "inspect":
            return 0, json.dumps({"Status": "exited", "Running": False, "Error": "",
                                  "StartedAt": "2026-10-03T00:00:00Z", "ExitCode": 0}).encode()
        return 0, b""
    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
    broker.docker_call = docker
    writer = MemoryWriter()
    await asyncio.wait_for(broker.execute(request(mode="policy", cwd="/data/extensions/alice/policy/x/1", timeout=1),
        asyncio.StreamReader(), writer), 3)
    assert writer.frames[-1]["code"] == 0 and writer.frames[-1]["status"] == "complete"
