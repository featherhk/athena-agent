import asyncio
import json
import shutil
import sys

import pytest

from athena.auth import Auth
from athena.policies import PolicyRunner, initialize_policies
from athena.registry import Registry, reset_registry


SANDBOX = sys.platform == "darwin" and bool(shutil.which("sandbox-exec"))
ECHO = 'import json, sys\npayload = json.load(sys.stdin)\nprint(json.dumps(payload["default"]))\n'


@pytest.fixture
async def policy(config, store):
    await initialize_policies(store)
    user, _ = await Auth(store).register("policy-user", "synthetic-test-password")
    session = await store.session(user_id=user["id"])
    return Registry(config, store), PolicyRunner(config, store), user, session


async def register(registry, session, name="memory_recall", source=ECHO):
    return await registry.operate({"kind": "policy", "action": "register", "name": name,
                                   "description": "Test transform", "source": source}, session)


async def test_policy_default_and_future_consumer_registration(policy, store):
    registry, runner, user, session = policy
    fallback = await runner.apply("memory_recall", session, {"default": ["memory-one"]}, lambda value: isinstance(value, list))
    assert fallback == {"value": ["memory-one"], "source": "default", "version": None}
    future = await register(registry, session, name="planner.custom-service")
    assert future["state"] == "waiting_for_consumer" and not future["effective"]
    listing = await registry.operate({"kind": "policy", "action": "list"}, session)
    assert {item["name"] for item in listing["consumers"]} == {"memory_recall", "context_compression", "execution"}
    assert listing["items"][0]["name"] == "planner.custom-service"
    assert not listing["items"][0]["effective"]
    read = await registry.operate({"kind": "policy", "action": "read", "name": "planner.custom-service"}, session)
    assert read["source"] == ECHO and read["state"] == "waiting_for_consumer"
    outsider, _ = await Auth(store).register("other-policy-user", "synthetic-test-password")
    other_session = await store.session(user_id=outsider["id"])
    assert (await registry.operate({"kind": "policy", "action": "list"}, other_session))["items"] == []
    with pytest.raises(ValueError):
        await registry.operate({"kind": "policy", "action": "read", "name": "planner.custom-service"}, other_session)
    for name in ("../bad", "Bad", "a" * 65):
        with pytest.raises(ValueError):
            await register(registry, session, name=name)


async def test_new_consumer_can_invoke_a_canonical_service_without_enum_changes(policy, monkeypatch):
    registry, runner, _, session = policy
    await register(registry, session, name="planner.custom-service")
    monkeypatch.setattr("athena.policies._supported", lambda: True)
    async def transform(row, payload):
        assert row["name"] == "planner.custom-service"
        return {"selected": payload["candidates"][0]}
    monkeypatch.setattr(runner, "_run", transform)
    result = await runner.apply("planner.custom-service", session,
        {"default": {}, "candidates": ["first", "second"]}, lambda value: value == {"selected": "first"})
    assert result == {"value": {"selected": "first"}, "source": "plugin", "version": 1}


async def test_policy_failure_disables_only_failing_version_and_can_restore(policy, store, monkeypatch):
    registry, runner, user, session = policy
    await register(registry, session)
    first = (await store.rows("SELECT * FROM harness_policies"))[0]
    monkeypatch.setattr("athena.policies._supported", lambda: True)
    async def invalid(row, payload):
        return {"unexpected": "not a memory id list"}
    monkeypatch.setattr(runner, "_run", invalid)
    result = await runner.apply("memory_recall", session, {"default": ["safe-id"]}, lambda value: isinstance(value, list))
    assert result == {"value": ["safe-id"], "source": "default", "version": 1, "error": "validation_error"}
    rows = await store.rows("SELECT * FROM harness_policies ORDER BY version")
    assert rows[0] == first and rows[1]["version"] == 2 and rows[1]["active"] == 0
    assert (await store.rows("SELECT * FROM policy_failures"))[0]["disabled"] == 1
    assert (await store.rows("SELECT * FROM events WHERE type='policy_fallback'"))
    again = await runner.apply("memory_recall", session, {"default": []}, lambda value: True)
    assert again["version"] is None and "error" not in again
    restored = await registry.operate({"kind": "policy", "action": "restore", "name": "memory_recall", "version": 1}, session)
    assert restored["version"] == 3 and restored["effective"]
    before_reset = await store.rows("SELECT * FROM harness_policies ORDER BY version")
    await store.call(lambda db: reset_registry(db, user["id"]))
    after_reset = await store.rows("SELECT * FROM harness_policies ORDER BY version")
    assert after_reset[:-1] == before_reset and after_reset[-1]["active"] == 0
    assert (await runner.apply("memory_recall", session, {"default": []}, lambda value: True))["source"] == "default"


async def test_old_policy_failure_does_not_disable_concurrent_upgrade(policy, store, monkeypatch):
    registry, runner, _, session = policy
    await register(registry, session)
    entered, resume = asyncio.Event(), asyncio.Event()
    async def delayed(row, payload):
        entered.set()
        await resume.wait()
        raise RuntimeError("sensitive fixture value must not be recorded")
    monkeypatch.setattr("athena.policies._supported", lambda: True)
    monkeypatch.setattr(runner, "_run", delayed)
    pending = asyncio.create_task(runner.apply("memory_recall", session, {"default": []}, lambda value: True))
    await entered.wait()
    await register(registry, session, source=ECHO + "# improved revision\n")
    resume.set()
    assert (await pending)["source"] == "default"
    versions = await store.rows("SELECT version,active FROM harness_policies ORDER BY version")
    assert versions == [{"version": 1, "active": 1}, {"version": 2, "active": 1}]
    failures = await store.rows("SELECT * FROM policy_failures")
    assert failures[0]["version"] == 1 and failures[0]["disabled"] == 0
    assert "sensitive" not in json.dumps(failures)


async def test_policy_timeout_cancellation_and_unsupported_platform(policy, store, monkeypatch):
    registry, runner, _, session = policy
    await register(registry, session)
    monkeypatch.setattr("athena.policies._supported", lambda: True)
    monkeypatch.setattr("athena.policies.POLICY_TIMEOUT", 0.02)
    async def block(row, payload):
        await asyncio.Event().wait()
    monkeypatch.setattr(runner, "_run", block)
    result = await runner.apply("memory_recall", session, {"default": []}, lambda value: True)
    assert result["error"] == "timeout"
    await registry.operate({"kind": "policy", "action": "restore", "name": "memory_recall", "version": 1}, session)
    task = asyncio.create_task(runner.apply("memory_recall", session, {"default": []}, lambda value: True))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(await store.rows("SELECT * FROM policy_failures")) == 1
    monkeypatch.setattr("athena.policies._supported", lambda: False)
    result = await runner.apply("memory_recall", session, {"default": []}, lambda value: True)
    assert result["error"] == "unsupported_platform" and result["source"] == "default"


@pytest.mark.skipif(not SANDBOX, reason="Real policy isolation currently requires macOS Seatbelt")
async def test_real_policy_process_returns_validated_data_without_environment_secrets(policy, monkeypatch):
    registry, runner, _, session = policy
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-hidden-provider-key")
    source = 'import json,sys,os\np=json.load(sys.stdin)\nprint(json.dumps({"selected":p["candidates"][:1],"has_key":"DEEPSEEK_API_KEY" in os.environ}))\n'
    await register(registry, session, source=source)
    result = await runner.apply("memory_recall", session, {"default": {}, "candidates": ["one", "two"]},
                                lambda value: value == {"selected": ["one"], "has_key": False})
    assert result == {"value": {"selected": ["one"], "has_key": False}, "source": "plugin", "version": 1}


@pytest.mark.skipif(not SANDBOX, reason="Real policy isolation currently requires macOS Seatbelt")
@pytest.mark.parametrize(("source", "code"), [
    ("print('not JSON')\n", "invalid_json"),
    ("print('x' * (256 * 1024 + 1))\n", "output_limit"),
    ("raise RuntimeError('synthetic confidential error')\n", "process_failed"),
])
async def test_real_policy_bad_output_falls_back_and_records_only_error_code(policy, store, source, code):
    registry, runner, _, session = policy
    await register(registry, session, source=source)
    result = await runner.apply("memory_recall", session, {"default": ["safe"]}, lambda value: True)
    assert result["error"] == code and result["value"] == ["safe"]
    assert "confidential" not in json.dumps(await store.rows("SELECT * FROM policy_failures"))


@pytest.mark.skipif(not SANDBOX, reason="Real policy isolation currently requires macOS Seatbelt")
async def test_policy_cannot_write_files_or_connect_network(policy, tmp_path):
    registry, runner, _, session = policy
    target = tmp_path / "must-not-exist.txt"
    source = '''import json,sys,socket
p=json.load(sys.stdin)
denied=[]
try:
    open(p["target"],"w").write("modified")
except PermissionError:
    denied.append("write")
try:
    socket.socket().connect(("127.0.0.1",9))
except PermissionError:
    denied.append("network")
print(json.dumps(denied))
'''
    await register(registry, session, name="execution", source=source)
    result = await runner.apply("execution", session, {"default": [], "target": str(target)},
                                lambda value: value == ["write", "network"])
    assert result["source"] == "plugin" and not target.exists()


async def test_component_placement_is_versioned_and_restored(policy, store):
    registry, _, _, session = policy
    first = await registry.operate({"kind": "component", "action": "register", "id": "panel",
                                   "placement": "workspace", "source": "<p>Full workspace</p>"}, session)
    await registry.operate({"kind": "component", "action": "register", "id": "panel",
                            "placement": "banner", "source": "<p>Banner</p>"}, session)
    await registry.operate({"kind": "component", "action": "restore", "id": "panel", "version": first["version"]}, session)
    read = await registry.operate({"kind": "component", "action": "read", "id": "panel"}, session)
    assert read["placement"] == "workspace" and read["source"] == "<p>Full workspace</p>"
    assert (await store.components(session))[0]["placement"] == "workspace"
