"""Exercise real Linux policy execution only in an explicitly marked QA area.

Run inside a disposable web-like container with the execution broker configured.
No model API is called. Accounts, scripts, files and audit rows are synthetic.
"""
import asyncio
import json
import shlex
import sys
import uuid

from athena.auth import Auth
from athena.config import Config, require_free_threading
from athena.policies import PolicyRunner
from athena.registry import Registry
from athena.store import Store
from athena.tools import ToolRunner


ECHO = """import json, sys
payload = json.load(sys.stdin)
print(json.dumps(payload["echo"]))
"""

OCCUPY_SLOT = """from pathlib import Path
import time
Path('occupied').write_text('running')
deadline = time.monotonic() + 40
while not Path('release').exists():
    if time.monotonic() >= deadline:
        raise TimeoutError('Policy fixture was not released in time')
    time.sleep(0.05)
print('slot-released')
"""


async def main():
    require_free_threading()
    config = Config.from_env()
    if not (config.data_dir / ".athena-execution-fixture").is_file():
        raise SystemExit("This smoke must use a disposable, explicitly marked data directory.")
    if sys.platform != "linux" or not config.execution_socket:
        raise SystemExit("This smoke requires the real Linux execution broker.")
    config.prepare()
    # Keep this fixture's recovery/audit lifecycle separate from other smoke runs.
    store = Store(config.data_dir / "smoke-policies.sqlite3")
    await store.initialize()
    runner = ToolRunner(config, store)
    workers, workspaces = [], []
    try:
        user, _ = await Auth(store).register("fixture-policy-" + uuid.uuid4().hex[:12], "fixture-only-password")
        session = await store.session(user_id=user["id"])
        registry, policies = Registry(config, store), PolicyRunner(config, store)
        registered = await registry.operate({"kind": "policy", "action": "register", "name": "memory_recall",
            "description": "Synthetic hosted execution acceptance fixture", "source": ECHO}, session)
        assert registered["effective"] and registered["version"] == 1, registered
        echoed = ["executed-in-worker", uuid.uuid4().hex]
        payload = {"default": ["safe-default"], "echo": echoed}

        async def invoke():
            return await policies.apply("memory_recall", session, payload, lambda value: isinstance(value, list))

        async def versions():
            return await store.rows("SELECT version,active,file_path FROM harness_policies WHERE user_id=? AND name='memory_recall' ORDER BY version",
                (user["id"],))

        initial = await asyncio.wait_for(invoke(), 50)
        expected = {"value": echoed, "source": "plugin", "version": 1}
        assert initial == expected, initial
        original_versions = await versions()
        assert len(original_versions) == 1 and original_versions[0]["active"] == 1

        # Start actual commands, not broker stubs. Per-worker marker files prove
        # both containers are executing before the busy-policy observation.
        for _ in range(2):
            worker_session = await store.session(user_id=user["id"])
            workspaces.append(config.data_dir / "files" / user["id"] / worker_session)
            workers.append(asyncio.create_task(runner.execute("exec", {
                "command": "python -c " + shlex.quote(OCCUPY_SLOT), "timeout_seconds": 45,
            }, worker_session, None)))
        async with asyncio.timeout(15):
            while not all((workspace / "occupied").is_file() for workspace in workspaces):
                for worker in workers:
                    if worker.done():
                        raise AssertionError(f"A command ended before occupying both slots: {worker.result()}")
                await asyncio.sleep(0.05)
        assert all(not worker.done() for worker in workers)

        busy = await asyncio.wait_for(invoke(), 10)
        assert busy == {"value": payload["default"], "source": "default", "version": 1, "error": "executor_busy"}, busy
        assert await versions() == original_versions, "Busy infrastructure changed the registered plugin version."
        failures = await store.rows("SELECT version,error_code,disabled FROM policy_failures WHERE user_id=? ORDER BY created_at",
            (user["id"],))
        assert failures == [{"version": 1, "error_code": "executor_busy", "disabled": 0}], failures

        # Release naturally and wait for each broker exit receipt; this includes
        # removal of its container and release of its reserved execution slot.
        for workspace in workspaces:
            (workspace / "release").write_text("release")
        results = await asyncio.wait_for(asyncio.gather(*workers), 15)
        assert all(result["exit_code"] == 0 and "slot-released" in result["output"] for result in results), results
        recovered = await asyncio.wait_for(invoke(), 50)
        assert recovered == expected, recovered
        assert await versions() == original_versions, "Recovery must use the same active version without restoring or registering it."
        final_failures = await store.rows("SELECT version,error_code,disabled FROM policy_failures WHERE user_id=? ORDER BY created_at",
            (user["id"],))
        assert final_failures == failures, final_failures

        print(json.dumps({"real_policy_echo": True, "busy_fallback": True, "plugin_remained_active": True,
                          "same_version_recovered": True, "completed_command_workers": len(results),
                          "policy_version": registered["version"], "non_disabling_failure_receipts": len(final_failures)}))
    finally:
        # A failed assertion must not leave fixture commands consuming capacity.
        for workspace in workspaces:
            try:
                if workspace.is_dir():
                    (workspace / "release").write_text("release")
            except OSError:
                pass  # The bounded wait/cancellation below still frees capacity.
        if workers:
            _, pending = await asyncio.wait(workers, timeout=10)
            for worker in pending:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
        await runner.browser.close()
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
