"""Pinned native browser transport. No Node/Python browser dependency at runtime."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import sys
import uuid

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
DRIVER_VERSION = '0.38.2'


def protocol_command(command):
    """Small, pinned native protocol adapter; never pass page text as CLI flags.

    agent-browser's CLI resends on EOF/read timeout. Sending one framed request
    ourselves is intentional: a lost response must not repeat a submitted form.
    See docs/browser-execution.md for the upstream protocol reference.
    """
    match command:
        case ['tab', 'list']:
            value = {'action': 'tab_list'}
        case ['tab', 'new', url]:
            value = {'action': 'tab_new', 'url': url}
        case ['tab', 'close', target]:
            value = {'action': 'tab_close', 'tabId': target}
        case ['tab', target]:
            value = {'action': 'tab_switch', 'tabId': target}
        case ['open', url]:
            value = {'action': 'navigate', 'url': url}
        case ['snapshot']:
            value = {'action': 'snapshot'}
        case ['snapshot', '--selector', ref]:
            value = {'action': 'snapshot', 'selector': ref}
        case ['click' | 'check' | 'uncheck' as action, ref]:
            value = {'action': action, 'selector': ref}
        case ['fill', ref, text]:
            value = {'action': 'fill', 'selector': ref, 'value': text}
        case ['select', ref, text]:
            value = {'action': 'select', 'selector': ref, 'values': text}
        case ['press', key]:
            value = {'action': 'press', 'key': key}
        case ['scroll', direction, amount]:
            value = {'action': 'scroll', 'direction': direction, 'amount': int(amount)}
        case ['screenshot', path]:
            value = {'action': 'screenshot', 'path': path, 'fullPage': False}
        case ['download', ref, path]:
            value = {'action': 'download', 'selector': ref, 'path': path}
        case ['upload', ref, *paths] if paths:
            value = {'action': 'upload', 'selector': ref, 'files': paths}
        case ['get', 'attr', ref, attribute]:
            value = {'action': 'getattribute', 'selector': ref, 'attribute': attribute}
        case ['back' | 'forward' | 'reload' | 'close' as action]:
            value = {'action': action}
        case _:
            raise ValueError('Unsupported managed browser command')
    return {'id': uuid.uuid4().hex, **value}


class BrowserDriverError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class NativeBrowserDriver:
    """The host supplies identity, paths and commands; pages never supply flags."""
    def __init__(self, config, *, headed=True):
        self.config, self.headed = config, headed
        arch = {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'x64'}.get(platform.machine(), '')
        bundled = config.repo_dir / 'browser-runtime' / 'agent-browser'
        development = config.repo_dir / 'node_modules' / 'agent-browser' / 'bin' / f'agent-browser-darwin-{arch}'
        self.binary = bundled if bundled.is_file() else development
        candidates = (config.repo_dir / 'browser-runtime/chrome/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing',
                      Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),
                      Path('/Applications/Chromium.app/Contents/MacOS/Chromium'))
        self.chrome = next((path for path in candidates if path.is_file()), None)
        self.node_id = hashlib.sha256(str(config.data_dir.resolve()).encode()).hexdigest()[:16]
        self.used_users = set()

    def available(self):
        return sys.platform == 'darwin' and self.binary.is_file() and os.access(self.binary, os.X_OK) and self.chrome is not None

    def paths(self, user_id):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', user_id):
            raise ValueError('Invalid browser owner')
        root = self.config.data_dir / 'browser-profiles' / user_id
        # Unix socket paths are bounded to ~100 bytes on macOS. This private IPC
        # directory contains driver bookkeeping, never browser cookies or files.
        ipc = Path('/private/tmp') / f'athena-browser-{self.node_id}' / hashlib.sha256(user_id.encode()).hexdigest()[:16]
        return root, ipc

    def _prepare(self, user_id):
        if not self.available():
            raise BrowserDriverError('browser_unavailable', 'The managed browser executor is unavailable on this node.')
        root, ipc = self.paths(user_id)
        for path in (root.parent, root, ipc.parent, ipc, root / 'profile', root / 'downloads', root / 'outputs'):
            if path.is_symlink():
                raise BrowserDriverError('browser_storage_invalid', 'Browser storage must be an owned directory.')
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.stat().st_uid != os.getuid():
                raise BrowserDriverError('browser_storage_invalid', 'Browser storage belongs to another OS user.')
            path.chmod(0o700)
        driver_config = root / 'driver-config.json'
        if driver_config.is_symlink():
            raise BrowserDriverError('browser_storage_invalid', 'Invalid browser configuration path.')
        # Generated adapter input only; SQLite remains the authoritative state.
        driver_config.write_text(json.dumps({'json': True, 'headed': self.headed,
            'executablePath': str(self.chrome),
            'profile': str(root / 'profile'), 'downloadPath': str(root / 'downloads'),
            'plugins': [], 'noAutoDialog': True, 'noWebmcp': True, 'idleTimeout': '1h'}))
        driver_config.chmod(0o600)
        return root, ipc, driver_config

    async def _control(self, user_id, command, *, timeout=35):
        # Only local inventory and launch of an empty browser use the CLI. Neither
        # can submit an external action even if the CLI retries internally.
        if command not in (['session', 'info'], ['tab', 'list']):
            raise ValueError('Only browser lifecycle commands may use the CLI')
        root, ipc, driver_config = await asyncio.to_thread(self._prepare, user_id)
        self.used_users.add(user_id)
        args = [str(self.binary), '--config', str(driver_config), '--session', 'athena', '--json',
                '--profile', str((root / 'profile').resolve()), '--executable-path', str(self.chrome), *command]
        environment = {key: value for key, value in os.environ.items()
                       if key in ('PATH', 'HOME', 'USER', 'LANG', 'LC_ALL', 'TMPDIR')}
        environment.update(AGENT_BROWSER_SOCKET_DIR=str(ipc), AGENT_BROWSER_DEFAULT_TIMEOUT='20000')
        # A fixed host service, not an agent-authored shell process. Chromium's
        # renderer/network sandboxes cannot initialize inside nested Seatbelt.
        # Keep Chromium's own sandbox enabled; no --no-sandbox, shell, custom
        # executable, profile name, flags, environment or CDP address from tools.
        process = await asyncio.create_subprocess_exec(*args, env=environment, cwd=root,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, start_new_session=True)

        async def collect():
            chunks, size = [], 0
            while chunk := await process.stdout.read(65536):
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise BrowserDriverError('browser_output_limit', 'The browser response exceeded its bounded transport limit.')
                chunks.append(chunk)
            await process.wait()
            return b''.join(chunks)

        try:
            raw = await asyncio.wait_for(collect(), timeout)
        except BaseException:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
            raise
        try:
            result = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BrowserDriverError('browser_protocol_error', 'The browser driver returned an invalid response.') from error
        if not isinstance(result, dict) or result.get('success') is not True or process.returncode != 0:
            # This is a local driver error, but it can quote untrusted page content.
            message = result.get('error', 'Browser action failed.') if isinstance(result, dict) else 'Browser action failed.'
            raise BrowserDriverError('browser_action_failed', str(message)[:1000])
        return result.get('data')

    async def run(self, user_id, command, *, timeout=35):
        payload = protocol_command(command)
        _, ipc = self.paths(user_id)
        async with asyncio.timeout(timeout):
            reader, writer = await asyncio.open_unix_connection(ipc / 'athena.sock', limit=MAX_RESPONSE_BYTES)
            try:
                writer.write(json.dumps(payload, ensure_ascii=False).encode() + b'\n')
                await writer.drain()
                # Exactly one send, including EOF, malformed replies, timeout and
                # cancellation. No reconnect, implicit relaunch or replay here.
                raw = await reader.readline()
                try:
                    response = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise BrowserDriverError('browser_protocol_error', 'The browser result could not be confirmed.') from error
                if not isinstance(response, dict) or response.get('success') is not True:
                    message = response.get('error') if isinstance(response, dict) else None
                    raise BrowserDriverError('browser_action_failed', str(message or 'Browser action failed.')[:1000])
                return response.get('data')
            finally:
                writer.close()
                await writer.wait_closed()

    async def start(self, user_id):
        # Bare `open` in 0.38.2 sends a second launch without profile options.
        # Tab inventory uses the fully configured initial launch exactly once.
        await self._control(user_id, ['tab', 'list'])

    async def identity(self, user_id):
        data = await self._control(user_id, ['session', 'info'], timeout=8)
        if not isinstance(data, dict) or not data.get('active') or not data.get('pid'):
            return None
        _, ipc = self.paths(user_id)
        try:
            stamp = (ipc / 'athena.pid').stat().st_mtime_ns
        except FileNotFoundError:
            return None
        return f"{data['pid']}:{stamp}"

    async def close(self, user_id):
        if await self.identity(user_id):
            await self.run(user_id, ['close'], timeout=8)

    async def shutdown(self):
        async def close_owner(user_id):
            try:
                await self.close(user_id)
            except (ValueError, OSError, TimeoutError):
                pass  # A later status check must revalidate live state, never replay.
        await asyncio.gather(*(close_owner(user_id) for user_id in tuple(self.used_users)))


class ContainerBrowserDriver(NativeBrowserDriver):
    """One Linux browser container per account; the same single-send protocol."""
    def available(self):
        return bool(self.config.execution_socket) and Path(self.config.execution_socket).is_socket()

    def paths(self, user_id):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', user_id):
            raise ValueError('Invalid browser owner')
        root = self.config.data_dir / 'browser-profiles' / user_id
        return root, root / 'ipc'

    async def _control(self, user_id, command, *, timeout=35):
        from .execution_client import control
        if command not in (['session', 'info'], ['tab', 'list']):
            raise ValueError('Only browser lifecycle commands may use the broker')
        self.used_users.add(user_id)
        return await control(self.config.execution_socket,
            {'op': 'browser_control', 'owner': user_id, 'command': command}, timeout=max(timeout, 45))


def browser_driver(config):
    return ContainerBrowserDriver(config, headed=False) if sys.platform == 'linux' and config.execution_socket else NativeBrowserDriver(config)
