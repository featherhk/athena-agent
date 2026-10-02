import asyncio
import json

import pytest

from athena.app_controls import (acknowledge_app_action, app_operation, cancel_app_actions,
                                 claim_app_action, list_app_actions)
from athena.auth import Auth, token_hash
from athena.errors import UserError
from athena.files import initialize_files
from athena.store import Store, now


@pytest.fixture
async def account(store):
    user, token = await Auth(store).register("controls", "test-password")
    session = await store.session(user_id=user["id"])
    return user["id"], session, token


async def test_app_actions_are_pending_until_claimed_and_idempotently_acknowledged(store, account):
    user, session, _ = account
    requested = await app_operation(store, session, None, {"action": "open", "target": "history"})
    assert requested["status"] == "requested" and requested["completed"] is False
    identifier = requested["request"]["id"]
    pending = await list_app_actions(store, session, user)
    assert [item["id"] for item in pending] == [identifier]
    event = (await store.rows("SELECT * FROM events WHERE session_id=? ORDER BY seq DESC LIMIT 1", (session,)))[0]
    assert event["type"] == "app_action" and json.loads(event["payload"])["status"] == "pending"
    results = await asyncio.gather(*(claim_app_action(store, session, user, identifier, client) for client in ("browser-one", "browser-two")))
    assert sum(result["accepted"] for result in results) == 1
    winner = "browser-one" if results[0]["accepted"] else "browser-two"
    assert await list_app_actions(store, session, user) == []
    assert not (await claim_app_action(store, session, user, identifier, winner))["accepted"]
    read = await app_operation(store, session, None, {"action": "read", "id": identifier})
    assert read["request"]["status"] == "dispatched" and not read["completed"]
    first = await acknowledge_app_action(store, session, user, identifier, winner, "applied")
    again = await acknowledge_app_action(store, session, user, identifier, winner, "applied")
    assert first["changed"] and first["completed"] and not again["changed"]
    with pytest.raises(UserError):
        await acknowledge_app_action(store, session, user, identifier, winner, "failed", "execution_failed")
    with pytest.raises(UserError):
        await acknowledge_app_action(store, session, user, identifier, "unclaimed-browser", "applied")


async def test_app_action_scope_file_ownership_and_catalog(store, account):
    user, session, _ = account
    other, _ = await Auth(store).register("other", "test-password")
    other_session = await store.session(user_id=other["id"])
    same_user_session = await store.session(user_id=user)
    for target in ("missing", other_session):
        with pytest.raises(UserError):
            await app_operation(store, session, None, {"action": "navigate", "session_id": target})
    navigate = await app_operation(store, session, None, {"action": "navigate", "session_id": same_user_session})
    with pytest.raises(UserError):
        await list_app_actions(store, session, other["id"])
    with pytest.raises(UserError):
        await app_operation(store, other_session, None, {"action": "read", "id": navigate["request"]["id"]})
    await initialize_files(store)
    for identifier, owner, sid in (("own-file", user, session), ("second-chat-file", user, same_user_session), ("foreign-file", other["id"], other_session)):
        await store.execute("INSERT INTO file_entries VALUES (?,?,?,?,?,?,?,?,?)", (identifier, owner, sid, "note.md", "note.md", 3, "text/markdown", "output", now()))
    for action in ("preview", "download", "attach", "detach"):
        with pytest.raises(UserError):
            await app_operation(store, session, None, {"action": action, "file_id": "foreign-file"})
    with pytest.raises(UserError):
        await app_operation(store, session, None, {"action": "attach", "file_id": "second-chat-file"})
    preview = await app_operation(store, session, None, {"action": "preview", "file_id": "second-chat-file"})
    assert preview["request"]["payload"]["file"]["id"] == "second-chat-file"
    detach = await app_operation(store, session, None, {"action": "detach", "file_id": "own-file"})
    assert detach["request"]["payload"]["file_id"] == "own-file"
    assert await store.rows("SELECT id FROM file_entries WHERE id='own-file'")
    await app_operation(store, session, None, {"action": "open", "target": "menu"})
    await app_operation(store, session, None, {"action": "close", "target": "preview"})
    with pytest.raises(ValueError):
        await app_operation(store, session, None, {"action": "open", "target": "preview"})
    picker = await app_operation(store, session, None, {"action": "attach"})
    assert picker["request"]["payload"] == {} and not picker["completed"]
    catalog = await app_operation(store, session, None, {"action": "list"})
    assert {item["action"] for item in catalog["operations"]} >= {"open", "close", "navigate", "sign_out", "download", "attach", "detach", "stop", "reset"}
    assert any(item["op"] == "sessions" and item["action"] == "fork" for item in catalog["related_operations"])
    with pytest.raises(ValueError):
        await app_operation(store, session, None, {"action": "open", "target": "password", "content": "do-not-store-a-password"})


async def test_pending_actions_and_uncertain_dispatch_survive_reopen_without_replay(store, account):
    user, session, _ = account
    pending = await app_operation(store, session, None, {"action": "open", "target": "files"})
    dispatched = await app_operation(store, session, None, {"action": "open", "target": "account"})
    await claim_app_action(store, session, user, dispatched["request"]["id"], "original-browser")
    # A new connection models reopening; pending state and claims live in SQLite.
    reopened = Store(store.path)
    try:
        await reopened.initialize()
        assert [item["id"] for item in await list_app_actions(reopened, session, user)] == [pending["request"]["id"]]
        result = await claim_app_action(reopened, session, user, dispatched["request"]["id"], "new-browser")
        assert not result["accepted"] and result["request"]["status"] == "dispatched"
        await reopened.call(lambda db: cancel_app_actions(db, user))
        assert await list_app_actions(reopened, session, user) == []
        rows = await reopened.rows("SELECT status,error_code FROM app_actions")
        assert all(row == {"status": "dismissed", "error_code": "reset"} for row in rows)
    finally:
        await reopened.close()


async def test_app_catalog_discovers_outputs_without_a_browser_and_bounds_listing(config, store, account):
    user, session, _ = account
    directory = config.data_dir / "files" / user / session
    directory.mkdir(parents=True)
    (directory / "report.md").write_text("# Report")
    (directory / "table.csv").write_text("value\n1\n")
    result = await app_operation(store, session, None, {"action": "list", "limit": 1}, config)
    assert result["files_total"] == 2 and len(result["files"]) == 1 and result["has_more"]
    next_page = await app_operation(store, session, None, {"action": "read", "limit": 1, "offset": result["next_offset"]}, config)
    assert next_page["files"][0]["id"] != result["files"][0]["id"] and not next_page["has_more"]
    preview = await app_operation(store, session, None, {"action": "preview", "file_id": result["files"][0]["id"]}, config)
    assert preview["request"]["payload"]["file"]["name"] in {"report.md", "table.csv"}
    for invalid in (0, 201, True):
        with pytest.raises(ValueError):
            await app_operation(store, session, None, {"action": "read", "limit": invalid}, config)


async def test_signout_ack_only_revokes_receiving_browser_and_control_requires_active_run(store, account):
    user, session, token = account
    second_token = await Auth(store).issue(user)
    action = await app_operation(store, session, None, {"action": "sign_out"})
    identifier = action["request"]["id"]
    await claim_app_action(store, session, user, identifier, "receiving-browser")
    await acknowledge_app_action(store, session, user, identifier, "receiving-browser", "applied", logout_hash=token_hash(token))
    assert await Auth(store).current(token) is None
    assert (await Auth(store).current(second_token))["id"] == user
    for name in ("stop", "reset"):
        with pytest.raises(ValueError):
            await app_operation(store, session, None, {"action": name})
    run = "active-run"
    await store.execute("INSERT INTO runs(id,session_id,status,created_at) VALUES (?,?,'running',?)", (run, session, now()))
    assert (await app_operation(store, session, run, {"action": "stop"}))["control"] == {"stop_run": True}
    assert (await app_operation(store, session, run, {"action": "reset"}))["control"] == {"reset_user": True}
