"""Bounded subprocess transforms with append-only failure recovery.

Plugins receive only their hook payload; the parent owns validation, persistence
and side effects. A failing version is disabled without modifying its history.
"""
import asyncio
import json
import os
import shlex
import shutil
import signal
import sys

from .protection import protected_command
from .registry import POLICY_NAME, Registry, initialize_registry
from .store import now, uid


POLICY_TIMEOUT = 3.0
MAX_OUTPUT_BYTES = 256 * 1024


def _supported():
    return sys.platform == "darwin" and bool(shutil.which("sandbox-exec"))


async def initialize_policies(store):
    await initialize_registry(store)


class _PolicyFailure(Exception):
    def __init__(self, code):
        self.code = code


class PolicyRunner:
    def __init__(self, config, store):
        self.config, self.store = config, store
        self.registry = Registry(config, store)

    async def apply(self, slot, session_id, payload, validator):
        if not isinstance(slot, str) or not POLICY_NAME.fullmatch(slot):
            raise ValueError("Invalid harness policy service id")
        fallback = {"value": payload.get("default"), "source": "default", "version": None}
        await initialize_policies(self.store)
        def select(db):
            row = db.execute("SELECT p.* FROM harness_policies p JOIN sessions s ON s.user_id=p.user_id "
                             "WHERE s.id=? AND p.name=? ORDER BY p.version DESC LIMIT 1", (session_id, slot)).fetchone()
            return dict(row) if row and row["active"] else None
        row = await self.store.call(select)
        if row is None:
            return fallback
        fallback["version"] = row["version"]
        try:
            if not _supported():
                raise _PolicyFailure("unsupported_platform")
            value = await asyncio.wait_for(self._run(row, payload), timeout=POLICY_TIMEOUT)
            try:
                valid = validator(value)
                if valid is False:
                    raise ValueError("Rejected policy result")
            except Exception as exc:
                raise _PolicyFailure("validation_error") from exc
            return {"value": value, "source": "plugin", "version": row["version"]}
        except asyncio.CancelledError:
            # Stop/reset must cancel computation, without pretending the policy failed.
            raise
        except TimeoutError:
            code = "timeout"
        except _PolicyFailure as exc:
            code = exc.code
        except Exception:
            code = "execution_error"
        await self._disable_failed(row, session_id, code)
        return {**fallback, "error": code}

    async def _run(self, row, payload):
        path = self.registry._path(row, row["user_id"])
        # Isolated Python loads only the standard library, and never imports the
        # user file in the server process or inherits provider environment keys.
        command = "exec " + " ".join(shlex.quote(part) for part in (sys.executable, "-I", "-S", "-B", str(path)))
        argv = protected_command(command, self.config, [path.parent], [])
        argv[2] += "\n(deny network*)\n(deny file-write*)\n(deny process-fork)\n(deny signal)\n"
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT") if key in os.environ}
        process = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=env,
            cwd=path.parent, start_new_session=True)
        async def feed():
            try:
                process.stdin.write(encoded)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()
        feeding = asyncio.create_task(feed())
        try:
            chunks, total = [], 0
            while chunk := await process.stdout.read(16384):
                total += len(chunk)
                if total > MAX_OUTPUT_BYTES:
                    raise _PolicyFailure("output_limit")
                chunks.append(chunk)
            await feeding
            if await process.wait() != 0:
                raise _PolicyFailure("process_failed")
            try:
                def invalid_constant(value):
                    raise ValueError("Non-JSON number")
                return json.loads(b"".join(chunks).decode("utf-8"), parse_constant=invalid_constant)
            except (ValueError, UnicodeDecodeError, RecursionError) as exc:
                raise _PolicyFailure("invalid_json") from exc
        finally:
            if not feeding.done():
                feeding.cancel()
            await asyncio.gather(feeding, return_exceptions=True)
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()

    async def _disable_failed(self, row, session_id, code):
        def fail(db):
            current = db.execute("SELECT version,active FROM harness_policies WHERE user_id=? AND name=? ORDER BY version DESC LIMIT 1",
                                 (row["user_id"], row["name"])).fetchone()
            disabled = bool(current and current["active"] and current["version"] == row["version"])
            if disabled:
                Registry._insert(db, "policy", row["user_id"], row["name"], row["version"] + 1, row, active=False)
            db.execute("INSERT INTO policy_failures VALUES (?,?,?,?,?,?,?,?)",
                       (uid(), row["user_id"], session_id, row["name"], row["version"], code, int(disabled), now()))
            db.execute("INSERT INTO events(session_id,type,payload,created_at) VALUES (?,?,?,?)",
                       (session_id, "policy_fallback", json.dumps({"name": row["name"], "version": row["version"], "error_code": code}), now()))
        await self.store.call(fail)
