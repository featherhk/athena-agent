"""Exercise the real harness consumers, including the policy subprocess boundary."""
import json
import shutil
import sys

import pytest

from athena.auth import Auth
from athena.policies import initialize_policies


pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or not shutil.which("sandbox-exec"),
    reason="Policy and command consumers currently require macOS Seatbelt",
)


@pytest.fixture
async def consumer(engine, store):
    await initialize_policies(store)
    user, _ = await Auth(store).register("consumer-test", "synthetic-test-password")
    session = await store.session(user_id=user["id"])
    return engine, store, user, session


async def register(engine, session, name, source):
    return await engine.tools.execute("runtime", {
        "op": "registry", "args": {"kind": "policy", "action": "register", "name": name,
            "description": "Consumer integration test", "source": source},
    }, session, None)


async def assert_policy_failure(store, name):
    failures = await store.rows("SELECT name,version,error_code,disabled FROM policy_failures")
    assert failures == [{"name": name, "version": 1, "error_code": "validation_error", "disabled": 1}]
    versions = await store.rows("SELECT version,active FROM harness_policies WHERE name=? ORDER BY version", (name,))
    assert versions == [{"version": 1, "active": 1}, {"version": 2, "active": 0}]


@pytest.mark.parametrize("invalid", [False, True])
async def test_memory_policy_changes_the_actual_model_request_or_recalls_defaults(consumer, monkeypatch, invalid):
    engine, store, _, session = consumer
    for content in ("KEEP: the preferred memory", "OMIT: irrelevant memory"):
        await engine.tools.runtime({"op": "remember", "scope": "session", "content": content}, session, None)
    before = await store.rows("SELECT * FROM memories ORDER BY id")
    source = '''import json, sys
p = json.load(sys.stdin)
selected = [item["id"] for item in p["candidates"] if item["content"].startswith("KEEP:")]
print(json.dumps(selected))
'''
    if invalid:
        source = 'print("[\\"not-a-candidate-id\\"]")\n'
    await register(engine, session, "memory_recall", source)
    payloads = []
    async def provider(client, base, key, payload, on_text, on_reasoning):
        payloads.append(payload)
        return {"message": {"content": "Ready", "reasoning_content": ""}, "usage": {}, "model": "synthetic-model"}
    monkeypatch.setattr("athena.engine.complete", provider)
    run = await engine.submit(session, "Use the relevant saved context.")
    await engine.actors[session]
    assert (await store.rows("SELECT status FROM runs WHERE id=?", (run,)))[0]["status"] == "complete"
    assert len(payloads) == 1
    user_input = next(item["content"] for item in payloads[0]["messages"] if item["role"] == "user")
    assert "KEEP: the preferred memory" in user_input
    assert ("OMIT: irrelevant memory" in user_input) is invalid
    note = (await store.rows("SELECT context_note FROM messages WHERE run_id=? AND role='user'", (run,)))[0]["context_note"]
    selected = json.loads(note.split("\n", 1)[1])
    assert len(selected) == (2 if invalid else 1)
    assert await store.rows("SELECT * FROM memories ORDER BY id") == before
    if invalid:
        await assert_policy_failure(store, "memory_recall")
    else:
        assert await store.rows("SELECT * FROM policy_failures") == []


async def completed_tool_history(store, session):
    original = "Beginning of recoverable output\n" + "important result detail\n" * 400
    await store.message(session, None, "user", "Inspect this result.")
    await store.message(session, None, "assistant", "", tool_calls=[{
        "id": "call-consumer", "type": "function", "function": {"name": "exec", "arguments": '{"command":"printf data"}'},
    }])
    identifier = await store.message(session, None, "tool", original, tool_call_id="call-consumer")
    return identifier, original


@pytest.mark.parametrize("invalid", [False, True])
async def test_compression_policy_changes_projection_keeps_original_and_falls_back(consumer, invalid):
    engine, store, _, session = consumer
    identifier, original = await completed_tool_history(store, session)
    defaults = await engine.tools.introspection.compaction_plan(session, target_tokens=1000)
    assert identifier in defaults
    source = '''import json, sys
p = json.load(sys.stdin)
item = p["messages"][0]
print(json.dumps({item["id"]: "Selected evidence; message_id=" + item["id"]}))
'''
    if invalid:
        source = '''import json, sys
p = json.load(sys.stdin)
print(json.dumps({p["messages"][0]["id"]: "Missing the required recovery marker"}))
'''
    await register(engine, session, "context_compression", source)
    result = await engine.tools.compact(session, target_tokens=1000)
    assert result["policy"]["source"] == ("default" if invalid else "plugin")
    assert result["changed_tool_messages"] == [identifier]
    expected = defaults[identifier] if invalid else "Selected evidence; message_id=" + identifier
    clips = await store.rows("SELECT message_id,content FROM context_clips WHERE session_id=?", (session,))
    assert clips == [{"message_id": identifier, "content": expected}]
    context = await engine.context(session)
    projected = next(item for item in context if item.get("tool_call_id") == "call-consumer")
    assert projected["content"] == expected
    assistant = next(item for item in context if item.get("tool_calls"))
    assert assistant["tool_calls"][0]["id"] == projected["tool_call_id"]
    recovered = await engine.tools.runtime({"op": "read_message", "message_id": identifier, "limit": 20000}, session, None)
    assert recovered["content"] == original
    assert len(await store.rows("SELECT * FROM compactions WHERE session_id=?", (session,))) == 1
    if invalid:
        await assert_policy_failure(store, "context_compression")
    else:
        assert await store.rows("SELECT * FROM policy_failures") == []


@pytest.mark.parametrize("invalid", [False, True])
async def test_execution_policy_replaces_or_falls_back_to_one_actual_execution(consumer, config, invalid):
    engine, store, user, session = consumer
    source = '''import json, sys
p = json.load(sys.stdin)
result = dict(p["default"])
result["command"] = "printf transformed >> executions.txt; cat executions.txt"
result["timeout_seconds"] = TIMEOUT
print(json.dumps(result))
'''.replace("TIMEOUT", "301" if invalid else "3")
    await register(engine, session, "execution", source)
    result = await engine.tools.execute("exec", {
        "command": "printf original >> executions.txt; cat executions.txt", "timeout_seconds": 3,
    }, session, None)
    assert result["exit_code"] == 0, result
    expected = "original" if invalid else "transformed"
    assert result["output"] == expected
    target = config.data_dir / "files" / user["id"] / session / "executions.txt"
    # Appending detects both duplicate execution and execution before validation.
    assert target.read_text() == expected
    assert len(await store.rows("SELECT * FROM artifacts WHERE session_id=?", (session,))) == 1
    if invalid:
        await assert_policy_failure(store, "execution")
    else:
        assert await store.rows("SELECT * FROM policy_failures") == []


async def test_registered_tool_stdin_and_binding_bypass_exec_parameter_policy(consumer):
    engine, store, _, session = consumer
    await register(engine, session, "execution", 'raise RuntimeError("This policy must not run for a registered tool")\n')
    await engine.tools.runtime({"op": "register_tool", "name": "user_echo", "description": "Echo structured input",
        "parameters": {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]},
        "source": 'import json,sys\nprint(json.dumps({"received":json.load(sys.stdin)["value"]}))\n'}, session, None)
    _, bindings = await engine.tools.snapshot_definitions(session)
    result = await engine.tools.execute("user_echo", {"value": 19}, session, None, bindings=bindings)
    assert result["exit_code"] == 0 and json.loads(result["output"]) == {"received": 19}
    assert await store.rows("SELECT * FROM policy_failures") == []
    assert (await store.rows("SELECT active FROM harness_policies WHERE name='execution'")) == [{"active": 1}]
