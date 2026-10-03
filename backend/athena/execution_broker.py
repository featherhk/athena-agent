"""Trusted Linux container supervisor, reachable only over a private Unix socket.

No model credentials, arbitrary mounts, Docker flags, images, or daemon packets
are accepted. Workers receive only one account's files or browser profile.
"""
import asyncio
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import uuid

from .store import Store, now

IDENTIFIER = re.compile(r"[a-zA-Z0-9_-]{1,64}\Z")
FRAME_LIMIT = 2 * 1024 * 1024
OUTPUT_LIMIT = 32 * 1024 * 1024
LABEL = "com.featherhk.athena.execution=1"
MIN_FREE_BYTES = 2 * 1024 * 1024 * 1024
ACCOUNT_FILE_BYTES = 512 * 1024 * 1024
ACCOUNT_FILE_ENTRIES = 10000
ACCOUNT_FILE_DEPTH = 32
POLICY_SETUP_TIMEOUT = 5


class ResourceLimitError(ValueError):
    pass


class ExecutionBusyError(ValueError):
    pass


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("Invalid execution identity.")
    return value


def validate_request(request):
    if not isinstance(request, dict):
        raise ValueError("Invalid execution request.")
    owner = identifier(request.get("owner"))
    op = request.get("op")
    if op == "browser_control":
        if set(request) != {"op", "owner", "command"} or request["command"] not in (["session", "info"], ["tab", "list"]):
            raise ValueError("Unsupported browser lifecycle operation.")
    elif op == "exec":
        if set(request) != {"op", "id", "owner", "timeout", "mode", "cwd", "command", "stdin"}:
            raise ValueError("Unsupported execution fields.")
        identifier(request["id"])
        if type(request["timeout"]) is not int or not 1 <= request["timeout"] <= 300:
            raise ValueError("Execution deadline must be 1–300 seconds.")
        if request["mode"] not in ("command", "policy"):
            raise ValueError("Invalid execution mode.")
        if not isinstance(request["command"], str) or not 1 <= len(request["command"]) <= 30000 or "\0" in request["command"]:
            raise ValueError("Invalid command.")
        if not isinstance(request["cwd"], str) or "\0" in request["cwd"]:
            raise ValueError("Invalid working directory.")
        area = "files" if request["mode"] == "command" else "extensions"
        root, cwd = Path("/data") / area / owner, Path(request["cwd"])
        if not cwd.is_absolute() or ".." in cwd.parts or not cwd.is_relative_to(root):
            raise ValueError("Working directory is outside the account workspace.")
        if not isinstance(request["stdin"], str):
            raise ValueError("Invalid extension input.")
        try:
            raw = base64.b64decode(request["stdin"], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Invalid extension input.") from exc
        if len(raw) > 1024 * 1024:
            raise ValueError("Extension input exceeds 1 MiB.")
    else:
        raise ValueError("Unsupported execution operation.")
    return owner, op


class Broker:
    def __init__(self, data, host_data, image, network, store, *, docker="docker", namespace="1", network_ready=None):
        self.data, self.host_data = Path(data), Path(host_data)
        self.image, self.network, self.store, self.docker = image, network, store, docker
        self.namespace = identifier(namespace)
        self.network_ready = Path(network_ready) if network_ready else None
        self.label = "com.featherhk.athena.execution=" + self.namespace
        self.slots = asyncio.Semaphore(2)
        # Serialize launches and capacity checks across every account, including
        # the initial driver start before an inactive container can be reclaimed.
        self.browser_admission = asyncio.Lock()
        self.connections = set()
        self.cleanup_uncertain = False

    def require_network(self):
        if self.network_ready is None:
            return
        try:
            with self.network_ready.open() as source:
                proof = json.loads(source.read(4097))
            boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            if (not isinstance(proof, dict) or not boot_id or proof.get("boot_id") != boot_id
                    or proof.get("network") != self.network or proof.get("ipv6") is not False):
                raise ValueError("Stale execution network proof.")
        except (OSError, ValueError) as exc:
            raise ValueError("Execution network protection has not been verified for this boot.") from exc

    def check_resources(self, owner):
        """Bounded, non-following soft limit; called off the event loop.

        Periodic checks cannot enforce a hard quota or reclaim user-owned files.
        Other processes (including browsers) can consume disk between checks.
        """
        try:
            space = os.statvfs(self.data)
            if space.f_bavail * space.f_frsize < MIN_FREE_BYTES:
                raise ResourceLimitError("Execution stopped because the data volume has less than 2 GiB free. Free disk space before continuing.")
            try:
                root = os.open(self.data / "files" / owner, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            except FileNotFoundError:
                return
            size = count = 0
            def scan(directory, depth):
                nonlocal size, count
                with os.scandir(directory) as entries:
                    for entry in entries:
                        count += 1
                        if count > ACCOUNT_FILE_ENTRIES:
                            raise ResourceLimitError("The account file area exceeded its 10,000-entry soft limit. Remove unneeded files before continuing.")
                        try:
                            metadata = entry.stat(follow_symlinks=False)
                            if stat.S_ISDIR(metadata.st_mode):
                                if depth >= ACCOUNT_FILE_DEPTH:
                                    raise ResourceLimitError("The account file area exceeded its 32-level directory limit.")
                                child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                                try:
                                    scan(child, depth + 1)
                                finally:
                                    os.close(child)
                            elif stat.S_ISREG(metadata.st_mode):
                                size += metadata.st_size
                                if size > ACCOUNT_FILE_BYTES:
                                    raise ResourceLimitError("The account file area exceeded its 512 MiB soft limit. Remove unneeded files before continuing.")
                        except FileNotFoundError:
                            continue  # Concurrently removed files no longer consume this account's area.
            try:
                scan(root, 0)
            finally:
                os.close(root)
        except OSError as exc:
            raise ResourceLimitError("Execution stopped because account disk usage could not be verified.") from exc

    async def docker_call(self, *args, timeout=40, check=True):
        process = await asyncio.create_subprocess_exec(self.docker, *args,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        async def read_bounded(stream):
            chunks, size = [], 0
            while chunk := await stream.read(65536):
                size += len(chunk)
                if size > FRAME_LIMIT:
                    raise ValueError("Execution control response exceeded its limit.")
                chunks.append(chunk)
            return b"".join(chunks)
        readers = [asyncio.create_task(read_bounded(stream)) for stream in (process.stdout, process.stderr)]
        try:
            async with asyncio.timeout(timeout):
                output, error = await asyncio.gather(*readers)
                await process.wait()
            if process.returncode and check:
                raise ValueError("Execution container operation failed: " + error.decode(errors="replace")[:500])
            return process.returncode, output
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
            for task in readers:
                task.cancel()
            await asyncio.gather(*readers, return_exceptions=True)

    def directory(self, *parts):
        # Walk using descriptors, not check-then-open pathname operations. A
        # worker may rename entries inside its mount while this code runs.
        path = self.data
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts:
                if part in ("", ".", "..") or "/" in part:
                    raise ValueError("Invalid execution storage path.")
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                try:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                except OSError as exc:
                    raise ValueError("Execution storage must be a directory, not a symbolic link.") from exc
                os.close(descriptor)
                descriptor = child
                if os.geteuid() == 0:
                    os.fchown(descriptor, 10001, 10001)
                path = path / part
        finally:
            os.close(descriptor)
        return path

    def browser_config(self, root):
        # The browser owns this directory. Never follow its config path as the
        # privileged broker, and never expose a partially written config.
        container_root = Path("/data/browser-profiles") / root.name
        config = {"json": True, "headed": False, "executablePath": "/usr/bin/chromium",
                  "profile": str(container_root / "profile"), "downloadPath": str(container_root / "downloads"),
                  "plugins": [], "noAutoDialog": True, "noWebmcp": True, "idleTimeout": "20m"}
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        name = ".driver-config-" + uuid.uuid4().hex
        try:
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            with os.fdopen(descriptor, "w") as output:
                if os.geteuid() == 0:
                    os.fchown(output.fileno(), 10001, 10001)
                output.write(json.dumps(config))
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, "driver-config.json", src_dir_fd=directory, dst_dir_fd=directory)
        finally:
            try:
                os.unlink(name, dir_fd=directory)
            except FileNotFoundError:
                pass
            os.close(directory)

    def mount(self, area, owner, *, readonly=False):
        self.directory(area, owner)
        return f"type=bind,src={self.host_data / area / owner},dst=/data/{area}/{owner}" + (",readonly" if readonly else "")

    def limits(self, name, *, browser=False, policy=False):
        return ["--name", name, "--label", self.label, "--label", "athena.kind=" + ("browser" if browser else "command"),
                "--init", "--user", "10001:10001", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges:true", "--pids-limit", "192" if browser else "64",
                "--memory", "512m" if browser else "384m", "--memory-swap", "512m" if browser else "384m",
                "--cpus", "0.75", "--ulimit", "nofile=1024:1024", "--ulimit", "fsize=67108864:67108864",
                "--network", "none" if policy else self.network,
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
                "--shm-size", "128m", "--log-driver", "none"]

    async def initialize(self):
        self.require_network()
        await self.store.call(lambda db: db.execute("""CREATE TABLE IF NOT EXISTS executions (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, command_hash TEXT NOT NULL, status TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT, exit_code INTEGER)"""))
        # A broker restart never reconnects to or repeats an unfinished command.
        _, output = await self.docker_call("ps", "-aq", "--filter", "label=" + self.label, "--filter", "label=athena.kind=command")
        for container in output.decode().split():
            await self.docker_call("rm", "-f", container)
        await self.store.execute("UPDATE executions SET status='unknown_after_restart',finished_at=? WHERE status IN ('running','cleanup_unknown')", (now(),))

    async def execute(self, request, reader, writer):
        owner, _ = validate_request(request)
        name = "athena-" + self.namespace + "-exec-" + request["id"]
        digest = hashlib.sha256(request["command"].encode()).hexdigest()
        # A duplicate ID is refused, even if the first caller lost its reply.
        await self.store.execute("INSERT INTO executions VALUES (?,?,?,'running',?,NULL,NULL)", (request["id"], owner, digest, now()))
        code, status, error, process, creating = 125, "failed", None, None, None
        acquired = False
        policy = request["mode"] == "policy"
        resource_checks_started = asyncio.Event()
        started = asyncio.Event()

        async def run():
            nonlocal process, creating, acquired
            if policy and self.slots.locked():
                raise ExecutionBusyError("Execution capacity is busy; use the default policy for this request.")
            await self.slots.acquire()
            acquired = True
            if self.cleanup_uncertain:
                raise ValueError("Execution cleanup needs operator recovery before another command can start.")
            if reader.at_eof():
                raise ConnectionResetError("Execution caller disconnected before dispatch.")
            await asyncio.to_thread(self.check_resources, owner)
            resource_checks_started.set()
            # Reject a symlink working directory before Docker resolves it.
            cwd = Path(request["cwd"])
            local_cwd = self.data / cwd.relative_to("/data")
            if local_cwd.resolve() != local_cwd or not local_cwd.is_dir():
                raise ValueError("Working directory is not an owned directory.")
            args = self.limits(name, policy=policy)
            if not policy:
                self.directory("files", owner, ".home")
                args += ["--mount", self.mount("files", owner)]
            args += ["--mount", self.mount("extensions", owner, readonly=True),
                     "--workdir", request["cwd"], "--env", f"HOME=/data/files/{owner}/.home" if not policy else "HOME=/tmp",
                     "--env", "ATHENA_WORKSPACE=" + request["cwd"], "--env", "LANG=C.UTF-8"]
            self.require_network()
            # Cancellation must first learn the create outcome: killing the
            # Docker CLI can leave a daemon-side create racing with removal.
            creating = asyncio.create_task(self.docker_call("create", "-i", *args, self.image,
                "/bin/sh", "-c", request["command"], timeout=20))
            await asyncio.shield(creating)
            if reader.at_eof():
                raise ConnectionResetError("Execution caller disconnected before dispatch.")
            self.require_network()
            process = await asyncio.create_subprocess_exec(self.docker, "start", "-a", "-i", name,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            started.set()
            async def feed():
                try:
                    process.stdin.write(base64.b64decode(request["stdin"]))
                    await process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    process.stdin.close()
            feeding = asyncio.create_task(feed())
            size = 0
            try:
                while chunk := await process.stdout.read(16384):
                    size += len(chunk)
                    if size > OUTPUT_LIMIT:
                        raise ValueError("Command output exceeded 32 MiB; execution stopped.")
                    writer.write(json.dumps({"type": "output", "data": base64.b64encode(chunk).decode()}).encode() + b"\n")
                    await writer.drain()
                await feeding
                result = await process.wait()
                if policy:
                    # Distinguish an actual script exit from Docker CLI/daemon
                    # failure. The latter must not deactivate a valid plugin.
                    _, raw_state = await self.docker_call("inspect", "--format", "{{json .State}}", name)
                    state = json.loads(raw_state)
                    if (not isinstance(state, dict) or state.get("Status") != "exited"
                            or state.get("Running") is not False or state.get("Error")
                            or not isinstance(state.get("StartedAt"), str) or state["StartedAt"].startswith("0001-")
                            or type(state.get("ExitCode")) is not int):
                        raise ValueError("Policy container execution could not be confirmed.")
                    result = state["ExitCode"]
                await asyncio.to_thread(self.check_resources, owner)
                return result
            finally:
                feeding.cancel()
                await asyncio.gather(feeding, return_exceptions=True)

        async def monitor_resources():
            await resource_checks_started.wait()
            while True:
                await asyncio.sleep(1)
                await asyncio.to_thread(self.check_resources, owner)

        work = asyncio.create_task(run())
        disconnected = asyncio.create_task(reader.read(1))
        resources = asyncio.create_task(monitor_resources())
        tasks = [work, disconnected, resources]
        started_wait = None
        try:
            # Include time waiting for capacity and creating the container. A
            # disconnected queued caller must never start a delayed side effect.
            if policy:
                started_wait = asyncio.create_task(started.wait())
                done, _ = await asyncio.wait([*tasks, started_wait], timeout=POLICY_SETUP_TIMEOUT,
                                             return_when=asyncio.FIRST_COMPLETED)
                if started_wait in done:
                    done, _ = await asyncio.wait(tasks, timeout=request["timeout"], return_when=asyncio.FIRST_COMPLETED)
                elif not done:
                    code, status, error = 125, "setup_timeout", "Policy executor setup timed out; use the default policy for this request."
                    return
            else:
                done, _ = await asyncio.wait(tasks, timeout=request["timeout"], return_when=asyncio.FIRST_COMPLETED)
            if resources in done:
                resources.result()
            elif work in done:
                code, status = work.result(), "complete"
            elif disconnected in done:
                status = "cancelled"
            elif policy and process is not None and process.returncode is not None:
                code, status, error = 125, "setup_timeout", "Policy execution status could not be verified in time."
            else:
                code, status, error = 124, "timeout", "Command timed out; check its effects before repeating it."
        except asyncio.CancelledError:
            status = "interrupted"
            raise
        except ResourceLimitError as exc:
            code, status, error = 125, "resource_limit", str(exc)[:1000]
        except ExecutionBusyError as exc:
            code, status, error = 125, "busy", str(exc)[:1000]
        except (ValueError, OSError, TimeoutError) as exc:
            error = str(exc)[:1000]
        finally:
            if started_wait is not None:
                tasks.append(started_wait)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if creating is not None:
                await asyncio.gather(creating, return_exceptions=True)
                # Removal kills all descendants, including detached processes.
                # Still reap local pipes and persist uncertainty if Docker fails.
                try:
                    removed, _ = await self.docker_call("rm", "-f", name, check=False, timeout=10)
                    if removed:
                        raise ValueError("Container removal was not confirmed.")
                except (ValueError, OSError, TimeoutError):
                    code, status = 125, "cleanup_unknown"
                    self.cleanup_uncertain = True
                    error = "Execution cleanup could not be confirmed. Do not automatically repeat the command."
            if process and process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
            if acquired:
                self.slots.release()
            await self.store.execute("UPDATE executions SET status=?,finished_at=?,exit_code=? WHERE id=?", (status, now(), code, request["id"]))
            if not writer.is_closing() and not reader.at_eof():
                try:
                    writer.write(json.dumps({"type": "exit", "code": code, "error": error, "status": status}).encode() + b"\n")
                    await writer.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass

    async def browser_command(self, name, owner, command):
        root = Path("/data/browser-profiles") / owner
        _, result = await self.docker_call("exec", name, "/usr/local/bin/agent-browser",
            "--config", str(root / "driver-config.json"), "--session", "athena", "--json", *command)
        try:
            data = json.loads(result)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("Browser initialization returned an invalid response.") from exc
        if not isinstance(data, dict) or data.get("success") is not True:
            message = data.get("error", "Browser initialization failed.") if isinstance(data, dict) else "Invalid browser response."
            raise ValueError(str(message)[:1000])
        return data.get("data")

    async def free_browser_capacity(self):
        _, output = await self.docker_call("ps", "--filter", "label=" + self.label,
            "--filter", "label=athena.kind=browser", "--format", "{{.Names}}")
        names = output.decode().split()
        if len(names) < 2:
            return
        prefix = "athena-" + self.namespace + "-browser-"
        remaining = len(names)
        for name in names:
            # Only confirmed closed/expired drivers can yield their container.
            # A timeout or malformed reply is not evidence that a browser is idle.
            if not name.startswith(prefix) or not IDENTIFIER.fullmatch(name[len(prefix):]):
                continue
            try:
                identity = await self.browser_command(name, name[len(prefix):], ["session", "info"])
                if not isinstance(identity, dict) or identity.get("active") is not False:
                    continue
                await self.docker_call("rm", "-f", name)
                remaining -= 1
                if remaining < 2:
                    return
            except (ValueError, OSError, TimeoutError):
                continue
        raise ValueError("Both browser slots are busy. Close an idle browser before starting another account.")

    async def browser(self, request):
        owner, _ = validate_request(request)
        async with self.browser_admission:
            name = "athena-" + self.namespace + "-browser-" + owner
            code, running = await self.docker_call("inspect", "--format", "{{.State.Running}}", name, check=False)
            if request["command"] != ["session", "info"]:
                self.require_network()
            if code or running.strip() != b"true":
                if request["command"] == ["session", "info"]:
                    return {"active": False}
                if not code:
                    await self.docker_call("rm", name)
                await self.free_browser_capacity()
                root = self.directory("browser-profiles", owner)
                for part in ("profile", "downloads", "outputs", "ipc", "home"):
                    self.directory("browser-profiles", owner, part)
                self.browser_config(root)
                self.require_network()
                container_root = Path("/data/browser-profiles") / owner
                await self.docker_call("run", "-d", *self.limits(name, browser=True),
                    "--mount", self.mount("browser-profiles", owner),
                    "--env", f"HOME={container_root}/home", "--env", f"AGENT_BROWSER_SOCKET_DIR={container_root}/ipc",
                    "--env", "AGENT_BROWSER_DEFAULT_TIMEOUT=20000", self.image, "/bin/sleep", "infinity")
            return await self.browser_command(name, owner, request["command"])

    async def connection(self, reader, writer):
        self.connections.add(asyncio.current_task())
        try:
            line = await asyncio.wait_for(reader.readline(), 5)
            if not line:
                return
            if len(line) > FRAME_LIMIT:
                raise ValueError("Execution request exceeded its frame limit.")
            request = json.loads(line)
            _, op = validate_request(request)
            if op == "exec":
                await self.execute(request, reader, writer)
            else:
                data = await self.browser(request)
                reply = json.dumps({"ok": True, "data": data}).encode() + b"\n"
                if len(reply) > FRAME_LIMIT:
                    raise ValueError("Browser response exceeded its frame limit.")
                writer.write(reply)
                await writer.drain()
        except Exception as exc:
            if not writer.is_closing():
                writer.write(json.dumps({"ok": False, "error": str(exc)[:1000]}).encode() + b"\n")
                try:
                    await writer.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
        finally:
            self.connections.discard(asyncio.current_task())
            writer.close()
            try:
                await writer.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass


async def main():
    from .config import require_free_threading
    require_free_threading()
    path = Path(os.environ.get("ATHENA_EXECUTION_SOCKET", "/run/athena-execution/broker.sock"))
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not stat.S_ISSOCK(path.lstat().st_mode):
            raise ValueError("Refusing to replace a non-socket broker path.")
        path.unlink()
    store = Store(Path("/state/execution.sqlite3"))
    broker = Broker("/data", os.environ["ATHENA_EXECUTION_HOST_DATA"], os.environ["ATHENA_EXECUTION_IMAGE"],
                    os.environ.get("ATHENA_EXECUTION_NETWORK", "athena-execution"), store,
                    namespace=os.environ.get("ATHENA_EXECUTION_NAMESPACE", "1"),
                    network_ready=os.environ.get("ATHENA_EXECUTION_NETWORK_READY"))
    await broker.initialize()
    server = await asyncio.start_unix_server(broker.connection, str(path), limit=FRAME_LIMIT)
    os.chmod(path, 0o600)
    os.chown(path, 10001, 10001)
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    async with server:
        await stop.wait()
    connections = tuple(broker.connections)
    for task in connections:
        task.cancel()
    await asyncio.gather(*connections, return_exceptions=True)
    await store.close()


if __name__ == "__main__":
    asyncio.run(main())
