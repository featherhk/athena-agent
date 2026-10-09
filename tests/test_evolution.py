import asyncio
import base64
import json

import jsonschema
import pytest

from athena.auth import Auth
from athena.registry import initialize_registry


PARAMETERS = {
    "type": "object",
    "properties": {"value": {"type": "integer"}},
    "required": ["value"],
    "additionalProperties": False,
}
WORKING_SOURCE = 'import json, sys\nprint(json.dumps({"value": json.load(sys.stdin)["value"] + 1}))\n'


@pytest.fixture
async def registry(engine, store):
    await initialize_registry(store)
    user, _ = await Auth(store).register("evolution", "local-test-password")
    session = await store.session(user_id=user["id"])
    return engine.tools, user, session


async def operation(tools, session, kind, action, **fields):
    return await tools.execute(
        "runtime", {"op": "registry", "kind": kind, "action": action, **fields}, session, None
    )


def tool_definition(name, **changes):
    return {"name": name, "description": "Increment an integer", "parameters": PARAMETERS,
            "source": WORKING_SOURCE, **changes}


async def test_tool_restore_executes_original_and_remove_preserves_history(registry, store, config):
    tools, user, session = registry
    first = await operation(tools, session, "tool", "register", **tool_definition("user_increment"))
    original_rows = await store.rows("SELECT * FROM extensions WHERE user_id=?", (user["id"],))
    original_path = config.data_dir / original_rows[0]["file_path"]
    broken = await operation(tools, session, "tool", "register", **tool_definition(
        "user_increment", source='raise RuntimeError("broken revision")\n'
    ))
    assert broken["version"] > first["version"]
    failure = await tools.execute("user_increment", {"value": 3}, session, None)
    assert failure["exit_code"] != 0

    restored = await operation(tools, session, "tool", "restore", name="user_increment", version=first["version"])
    assert restored["version"] > broken["version"]
    output = await tools.execute("user_increment", {"value": 3}, session, None)
    assert output["exit_code"] == 0, output
    assert json.loads(output["output"]) == {"value": 4}
    before_remove = await store.rows("SELECT * FROM extensions WHERE user_id=? ORDER BY version", (user["id"],))
    removed = await operation(tools, session, "tool", "remove", name="user_increment")
    after_remove = await store.rows("SELECT * FROM extensions WHERE user_id=? ORDER BY version", (user["id"],))
    assert removed["version"] > restored["version"]
    assert after_remove[:-1] == before_remove
    assert after_remove[-1]["active"] == 0
    assert after_remove[0] == original_rows[0]
    assert original_path.read_text() == WORKING_SOURCE
    with pytest.raises(ValueError, match="disabled"):
        await tools.execute("user_increment", {"value": 3}, session, None)
    assert "user_increment" not in {item["function"]["name"] for item in await tools.definitions(session)}
    historical = await operation(tools, session, "tool", "read", name="user_increment", version=first["version"])
    assert historical["source"] == WORKING_SOURCE
    assert historical["parameters"] == PARAMETERS
    history = await operation(tools, session, "tool", "list")
    assert {item["version"] for item in history["items"]} == {row["version"] for row in after_remove}

    await initialize_registry(store)
    assert await tools.registered(session) == []
    enabled = await operation(tools, session, "tool", "restore", name="user_increment", version=first["version"])
    assert enabled["version"] > removed["version"]
    assert json.loads((await tools.execute("user_increment", {"value": 8}, session, None))["output"]) == {"value": 9}


async def test_component_restore_keeps_history_and_session_scope(registry, store):
    tools, user, session = registry
    other_session = await store.session(user_id=user["id"])
    original = await tools.execute("update_frontend", {
        "op": "upsert", "id": "counter", "title": "Original", "html": "<p>one</p>"
    }, session, None)
    original_rows = await store.rows("SELECT * FROM components WHERE session_id=?", (session,))
    updated = await operation(tools, session, "component", "register", id="counter", title="Changed", source="<p>two</p>")
    restored = await operation(tools, session, "component", "restore", id="counter", version=original["version"])
    assert restored["version"] > updated["version"]
    current = (await store.components(session))[0]
    assert current["html"] == "<p>one</p>"
    assert current["title"] == "Original"
    assert await store.components(other_session) == []
    assert (await operation(tools, other_session, "component", "list"))["items"] == []
    with pytest.raises(ValueError):
        await operation(tools, other_session, "component", "restore", id="counter", version=original["version"])
    await tools.execute("update_frontend", {"op": "remove", "id": "counter"}, session, None)
    assert await store.components(session) == []
    assert (await store.rows("SELECT * FROM components WHERE session_id=? AND version=?", (session, original["version"]))) == original_rows
    historical = await operation(tools, session, "component", "read", id="counter", version=original["version"])
    assert historical["source"] == "<p>one</p>"
    await operation(tools, session, "component", "restore", id="counter", version=original["version"])
    assert (await store.components(session))[0]["html"] == "<p>one</p>"


async def test_custom_skill_restore_and_account_isolation(registry, store):
    tools, user, session = registry
    same_user_session = await store.session(user_id=user["id"])
    outsider, _ = await Auth(store).register("outsider", "local-test-password")
    outsider_session = await store.session(user_id=outsider["id"])
    original = await operation(tools, session, "skill", "register", name="user_planning",
                               description="Plan a task", source="# Planning\nWrite a short plan.\n")
    await operation(tools, session, "skill", "register", name="user_planning",
                    description="New plan", source="# Planning\nChanged instructions.\n")
    restored = await operation(tools, session, "skill", "restore", name="user_planning", version=original["version"])
    assert restored["version"] > original["version"]
    read = await operation(tools, same_user_session, "skill", "read", name="user_planning")
    assert "Write a short plan." in read["source"]
    assert read["description"] == "Plan a task"
    content = await tools.execute("runtime", {"op": "skill", "name": "user_planning"}, same_user_session, None)
    assert "Write a short plan." in content["content"]
    assert (await operation(tools, outsider_session, "skill", "list"))["items"] == []
    for action in ("read", "restore", "remove"):
        with pytest.raises(ValueError):
            await operation(tools, outsider_session, "skill", action, name="user_planning", version=original["version"])
    await operation(tools, session, "skill", "remove", name="user_planning")
    with pytest.raises(ValueError):
        await tools.execute("runtime", {"op": "skill", "name": "user_planning"}, session, None)
    historical = await operation(tools, session, "skill", "read", name="user_planning", version=original["version"])
    assert "Write a short plan." in historical["source"]


async def test_tool_limit_is_atomic_and_restore_cannot_bypass_it(registry):
    tools, _, session = registry
    for index in range(19):
        await operation(tools, session, "tool", "register", **tool_definition(f"user_slot_{index}"))
    attempts = await asyncio.gather(
        operation(tools, session, "tool", "register", **tool_definition("user_candidate_a")),
        tools.execute("runtime", {"op": "register_tool", **tool_definition("user_candidate_b")}, session, None),
        return_exceptions=True,
    )
    assert sum(isinstance(result, dict) for result in attempts) == 1, attempts
    assert sum(isinstance(result, ValueError) for result in attempts) == 1, attempts
    assert len(await tools.registered(session)) == 20
    await tools.execute("runtime", {"op": "remove_tool", "name": "user_slot_0"}, session, None)
    await operation(tools, session, "tool", "register", **tool_definition("user_replacement"))
    with pytest.raises(ValueError):
        await operation(tools, session, "tool", "restore", name="user_slot_0", version=1)
    assert len(await tools.registered(session)) == 20
    assert "user_slot_0" not in {row["name"] for row in await tools.registered(session)}


@pytest.mark.parametrize("changes", [
    {"parameters": {"type": "string"}},
    {"parameters": {"type": "object", "properties": {"value": {"$ref": "https://example.invalid/value"}}}},
    {"source": "def broken(:\n"},
    {"name": "runtime"},
])
async def test_registry_tool_validation_cannot_be_bypassed(registry, changes):
    tools, _, session = registry
    with pytest.raises((ValueError, SyntaxError, jsonschema.exceptions.SchemaError, jsonschema.exceptions.ValidationError)):
        await operation(tools, session, "tool", "register", **(tool_definition("user_invalid") | changes))
    assert await tools.registered(session) == []
    assert (await operation(tools, session, "tool", "list"))["items"] == []


async def test_request_binding_keeps_code_and_schema_until_next_request(registry):
    tools, _, session = registry
    await operation(tools, session, "tool", "register", **tool_definition("user_increment"))
    definitions, bindings = await tools.snapshot_definitions(session)
    assert definitions[-1]["function"]["parameters"] == PARAMETERS
    new_parameters = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
    await operation(tools, session, "tool", "register", **tool_definition(
        "user_increment", parameters=new_parameters, source='print("new version")\n'))
    output = await tools.execute("user_increment", {"value": 4}, session, None, bindings=bindings)
    assert json.loads(output["output"]) == {"value": 5}
    assert (await tools.definitions(session))[-1]["function"]["parameters"] == new_parameters
    with pytest.raises(ValueError, match="Required fields: text"):
        await tools.execute("user_increment", {"value": 4}, session, None)
    await operation(tools, session, "tool", "remove", name="user_increment")
    with pytest.raises(ValueError, match="disabled"):
        await tools.execute("user_increment", {"value": 4}, session, None, bindings=bindings)


def skill_files(name="user_package", body="Load references/notes.txt."):
    return {"SKILL.md": f"---\nname: {name}\ndescription: Test package\n---\n\n{body}",
            "references/notes.txt": "甲乙丙丁", "scripts/calculate.py": "print(2 + 2)\n",
            "templates/sample.bin": {"encoding": "base64", "content": base64.b64encode(b"\x00\xff\xfe").decode()}}


async def test_skill_package_resources_and_restore_are_complete(registry):
    tools, _, session = registry
    await operation(tools, session, "skill", "register", name="user_package", files=skill_files())
    metadata = await tools.registry.skill_catalog(session)
    assert metadata == [{"name": "user_package", "description": "Test package", "version": 1}]
    page = await tools.execute("runtime", {"op": "skill", "name": "user_package",
        "path": "references/notes.txt", "offset": 1, "limit": 2}, session, None)
    assert page["content"] == "乙丙" and page["total_chars"] == 4 and page["next_offset"] == 3
    binary = await operation(tools, session, "skill", "read", name="user_package", path="templates/sample.bin")
    assert binary["encoding"] == "base64" and base64.b64decode(binary["content"]) == b"\x00\xff\xfe"
    changed = skill_files(body="Changed content")
    changed["scripts/calculate.py"] = "raise RuntimeError('broken')\n"
    await operation(tools, session, "skill", "register", name="user_package", files=changed)
    restored = await operation(tools, session, "skill", "restore", name="user_package", version=1)
    assert restored["version"] == 3
    resource = await operation(tools, session, "skill", "read", name="user_package", path="scripts/calculate.py")
    assert resource["content"] == "print(2 + 2)\n"
    await operation(tools, session, "skill", "remove", name="user_package")
    assert await tools.registry.skill_catalog(session) == []


@pytest.mark.parametrize("bad_path", ["../outside", "/absolute", "a/../outside", "scripts\\escape.py", "C:/escape"])
async def test_skill_package_rejects_unsafe_paths_on_register_and_read(registry, bad_path):
    tools, _, session = registry
    invalid = {**skill_files(), bad_path: "unsafe"}
    with pytest.raises(ValueError):
        await operation(tools, session, "skill", "register", name="user_package", files=invalid)
    assert (await operation(tools, session, "skill", "list"))["items"] == []
    await operation(tools, session, "skill", "register", name="user_package", files=skill_files())
    with pytest.raises(ValueError):
        await operation(tools, session, "skill", "read", name="user_package", path=bad_path)


async def test_skill_package_identity_limits_and_symlinks(registry, store, config):
    tools, user, session = registry
    for files in ({"references/only.txt": "missing SKILL.md"}, skill_files(name="other_name"),
                  {**skill_files(), "large.txt": "x" * (4 * 1024 * 1024)},
                  {**skill_files(), "references": "conflicts with directory"}):
        with pytest.raises(ValueError):
            await operation(tools, session, "skill", "register", name="user_package", files=files)
    await operation(tools, session, "skill", "register", name="user_package", files=skill_files())
    row = (await store.rows("SELECT file_path FROM user_skills WHERE user_id=?", (user["id"],)))[0]
    package = config.data_dir / row["file_path"]
    outside = config.data_dir / "not-a-skill-resource.txt"
    outside.write_text("private fixture")
    (package / "references" / "linked.txt").symlink_to(outside)
    with pytest.raises(ValueError, match="symlinks"):
        await operation(tools, session, "skill", "read", name="user_package", path="references/linked.txt")
    with pytest.raises(ValueError, match="symlinks"):
        await operation(tools, session, "skill", "restore", name="user_package", version=1)


async def test_reset_appends_tombstones_without_resurrecting_old_versions(registry, engine, store):
    tools, user, session = registry
    await operation(tools, session, "tool", "register", **tool_definition("user_increment"))
    await operation(tools, session, "skill", "register", name="user_package", files=skill_files())
    await operation(tools, session, "component", "register", id="panel", source="<p>Original</p>")
    original = {table: await store.rows(f"SELECT * FROM {table}") for table in ("extensions", "user_skills", "components")}
    await engine.reset_user(user["id"])
    assert await tools.registered(session) == []
    assert await tools.registry.skill_catalog(session) == []
    assert await store.components(session) == []
    for table, rows in original.items():
        history = await store.rows(f"SELECT * FROM {table} ORDER BY version")
        assert history[:-1] == rows and history[-1]["active"] == 0
    await initialize_registry(store)
    await engine.reset_user(user["id"])
    for table in original:
        assert len(await store.rows(f"SELECT * FROM {table}")) == 2
    assert (await operation(tools, session, "tool", "read", name="user_increment", version=1))["source"] == WORKING_SOURCE
