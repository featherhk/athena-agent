import asyncio
import json

import pytest

from athena.auth import Auth


@pytest.mark.parametrize("field", ["file", "reference", "ref"])
async def test_skill_wrong_resource_fields_give_safe_repair_instructions(engine, store, field):
    session = await store.session()
    secret = "private-input-must-not-appear-in-validation-output"
    with pytest.raises(ValueError) as caught:
        await engine.tools.execute("runtime", {"op": "skill", "args": {"name": "desktop", field: secret}}, session, None)
    error = str(caught.value)
    assert "args" in error and "path" in error and "No action was executed" in error
    assert secret not in error and "ValidationError" not in error
    example = json.loads(error.split("through runtime ", 1)[1].split("; replace", 1)[0])
    resource = await engine.tools.execute("runtime", example, session, None)
    assert resource["path"] == "SKILL.md" and "references/surfaces.md" in resource["content"]


async def test_loaded_builtin_and_registered_skills_advertise_working_resource_calls(engine, store):
    user, _ = await Auth(store).register("skill-reader", "local-test-password")
    session = await store.session(user_id=user["id"])
    loaded = await engine.tools.execute("runtime", {"op": "skill", "args": {"name": "desktop"}}, session, None)
    call = loaded["read_resource"]
    assert call == {"op": "skill", "args": {"name": "desktop", "path": "SKILL.md"}}
    call["args"]["path"] = "references/surfaces.md"
    resource = await engine.tools.execute("runtime", call, session, None)
    assert 'placement:"desktop"' in resource["content"] and "Athena.interact([])" in resource["content"]
    await engine.tools.execute("runtime", {"op": "registry", "args": {
        "kind": "skill", "action": "register", "name": "user_reader", "files": {
            "SKILL.md": "---\nname: user_reader\ndescription: Local resource example\n---\nRead references/guide.txt.",
            "references/guide.txt": "Registered reference content",
        },
    }}, session, None)
    loaded = await engine.tools.execute("runtime", {"op": "skill", "args": {"name": "user_reader"}}, session, None)
    call = loaded["read_resource"]
    assert call["args"]["name"] == "user_reader"
    call["args"]["path"] = "references/guide.txt"
    resource = await engine.tools.execute("runtime", call, session, None)
    assert resource["content"] == "Registered reference content"


@pytest.mark.parametrize("arguments", [
    {"op": "skill", "args": {}},
    {"op": "skill", "args": {"name": ["secret-value"]}},
    {"op": "skill", "args": {"name": "desktop", "path": {"secret-field": "secret-value"}}},
    {"op": "skill", "args": {"name": "desktop", "secret-field": "secret-value"}},
    {"op": "skill", "args": ["secret-value"]},
    ["secret-value"],
])
async def test_invalid_contracts_never_echo_values_or_unrecognized_field_names(engine, store, arguments):
    session = await store.session()
    with pytest.raises(ValueError) as caught:
        await engine.tools.execute("runtime", arguments, session, None)
    error = str(caught.value)
    assert "Invalid tool arguments" in error
    assert "secret-value" not in error and "secret-field" not in error
    assert len(error) < 1000


async def test_engine_delivers_repairable_skill_error_and_can_continue_without_shell(engine, store, monkeypatch):
    session = await store.session()
    stages = []
    def response(identifier=None, arguments=None):
        calls = [] if identifier is None else [{"id": identifier, "type": "function", "function": {
            "name": "runtime", "arguments": json.dumps(arguments)}}]
        return {"message": {"role": "assistant", "content": "Done" if not calls else "",
                            "reasoning_content": "", "tool_calls": calls}, "usage": {}}
    async def provider(client, base, key, payload, *args, **kwargs):
        stage = len(stages); stages.append(payload)
        if stage == 0:
            return response("bad-field", {"op": "skill", "args": {"name": "desktop", "file": "references/surfaces.md"}})
        result = json.loads(payload["messages"][-1]["content"])
        if stage == 1:
            assert "path" in result["error"] and "ValidationError" not in result["error"]
            call = json.loads(result["error"].split("through runtime ", 1)[1].split("; replace", 1)[0])
            return response("discover", call)
        if stage == 2:
            call = result["read_resource"]
            call["args"]["path"] = "references/surfaces.md"
            return response("read-reference", call)
        assert "Athena.interact([])" in result["content"]
        return response()
    async def no_shell(*args, **kwargs):
        pytest.fail("Progressive skill recovery must not search the host filesystem")
    monkeypatch.setattr("athena.engine.complete", provider)
    monkeypatch.setattr(engine.tools, "shell", no_shell)
    run = await engine.submit(session, "Read the desktop guidance")
    await asyncio.wait_for(asyncio.gather(*engine.actors.values()), 5)
    assert (await store.rows("SELECT status FROM runs WHERE id=?", (run,)))[0]["status"] == "complete"
    outputs = [row for row in await store.history(session) if row["role"] == "tool"]
    assert [row["tool_call_id"] for row in outputs] == ["bad-field", "discover", "read-reference"]
