import asyncio
import json

import pytest

from athena.app_controls import (acknowledge_app_action, app_operation, cancel_app_actions,
                                 claim_app_action, list_app_actions)
from athena.auth import Auth, token_hash
from athena.errors import UserError
from athena.files import initialize_files
from athena.store import Store, now
from frontend_fixtures import observe_client


@pytest.fixture
async def account(store):
    user, token = await Auth(store).register("controls", "test-password")
    session = await store.session(user_id=user["id"])
    await observe_client(store, user["id"], session, token)
    return user["id"], session, token


async def test_app_actions_are_pending_until_claimed_and_idempotently_acknowledged(store, account):
    user, session, token = account
    requested = await app_operation(store, session, None, {"action": "open", "target": "history"})
    assert requested["status"] == "requested" and requested["completed"] is False
    identifier = requested["request"]["id"]
    pending = await list_app_actions(store, session, user)
    assert [item["id"] for item in pending] == [identifier]
    event = (await store.rows("SELECT * FROM events WHERE session_id=? ORDER BY seq DESC LIMIT 1", (session,)))[0]
    assert event["type"] == "app_action" and json.loads(event["payload"])["status"] == "pending"
    results = await asyncio.gather(*(claim_app_action(store, session, user, identifier, "browser-one") for _ in range(2)))
    assert sum(result["accepted"] for result in results) == 1
    winner = "browser-one"
    await observe_client(store, user, session, token, revision=2, open_views=["history"])
    assert await list_app_actions(store, session, user) == []
    assert not (await claim_app_action(store, session, user, identifier, winner))["accepted"]
    read = await app_operation(store, session, None, {"action": "read", "id": identifier})
    assert read["request"]["status"] == "dispatched" and not read["completed"]
    first = await acknowledge_app_action(store, session, user, identifier, winner, "applied", observed_revision=2)
    again = await acknowledge_app_action(store, session, user, identifier, winner, "applied", observed_revision=2)
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
    await claim_app_action(store, session, user, dispatched["request"]["id"], "browser-one")
    # A new connection models reopening; pending state and claims live in SQLite.
    reopened = Store(store.path)
    try:
        await reopened.initialize()
        assert [item["id"] for item in await list_app_actions(reopened, session, user)] == [pending["request"]["id"]]
        result = await claim_app_action(reopened, session, user, dispatched["request"]["id"], "browser-one")
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
    await claim_app_action(store, session, user, identifier, "browser-one")
    await acknowledge_app_action(store, session, user, identifier, "browser-one", "applied", logout_hash=token_hash(token), observed_revision=1)
    assert await Auth(store).current(token) is None
    assert (await Auth(store).current(second_token))["id"] == user
    for name in ("stop", "reset"):
        with pytest.raises(ValueError):
            await app_operation(store, session, None, {"action": name})
    run = "active-run"
    await store.execute("INSERT INTO runs(id,session_id,status,created_at) VALUES (?,?,'running',?)", (run, session, now()))
    assert (await app_operation(store, session, run, {"action": "stop"}))["control"] == {"stop_run": True}
    assert (await app_operation(store, session, run, {"action": "reset"}))["control"] == {"reset_user": True}


async def test_target_conversation_files_are_discovered_and_paged_without_changing_action_origin(config, store, account):
    user, origin, token = account
    target = await store.session(user_id=user)
    await observe_client(store, user, target, token, "target-browser")
    for session, names in ((origin, ["origin.md"]), (target, ["target.md", "target.csv"])):
        directory = config.data_dir / "files" / user / session
        directory.mkdir(parents=True)
        for name in names:
            (directory / name).write_text("Synthetic acceptance content")
    origin_action = (await app_operation(store, origin, None, {"action": "open", "target": "history"}))["request"]
    target_action = (await app_operation(store, target, None, {"action": "open", "target": "files"}))["request"]
    first = await app_operation(store, origin, None, {"action": "list", "session_id": target, "limit": 1}, config)
    assert first["files_session_id"] == target and first["files_total"] == 2 and first["has_more"]
    assert [item["id"] for item in first["pending"]] == [origin_action["id"]]
    assert target_action["id"] not in {item["id"] for item in first["recent"]}
    second = await app_operation(store, origin, None, {"action": "read", "session_id": target, "limit": 1, "offset": first["next_offset"]}, config)
    assert second["files_session_id"] == target and not second["has_more"]
    assert {first["files"][0]["name"], second["files"][0]["name"]} == {"target.md", "target.csv"}
    default = await app_operation(store, origin, None, {"action": "list"}, config)
    assert default["files_session_id"] == origin and [item["name"] for item in default["files"]] == ["origin.md"]
    indexed = await app_operation(store, origin, None, {"action": "read", "session_id": target})
    assert indexed["files_session_id"] == target and indexed["files_total"] == 2
    # Navigating the receiving client does not relocate the original run or its audit.
    navigation = (await app_operation(store, origin, None, {"action": "navigate", "session_id": target}))["request"]
    await claim_app_action(store, origin, user, navigation["id"], "browser-one")
    await observe_client(store, user, target, token, revision=2)
    await acknowledge_app_action(store, origin, user, navigation["id"], "browser-one", "applied", observed_revision=2)
    preview = (await app_operation(store, origin, None, {"action": "preview", "file_id": first["files"][0]["id"], "client_id": "browser-one"}))["request"]
    assert preview["session_id"] == origin and preview["payload"]["file"]["session_id"] == target
    assert preview["target_client_id"] == "browser-one"
    with pytest.raises(UserError):
        await app_operation(store, origin, None, {"action": "attach", "file_id": first["files"][0]["id"], "client_id": "browser-one"})


@pytest.mark.parametrize("action", ["list", "read"])
async def test_target_file_listing_rejects_foreign_or_missing_conversation_before_discovery(config, store, account, action):
    user, origin, _ = account
    other, _ = await Auth(store).register("foreign-file-catalog", "test-password")
    foreign = await store.session(user_id=other["id"])
    directory = config.data_dir / "files" / other["id"] / foreign
    directory.mkdir(parents=True)
    (directory / "private.md").write_text("Must not be discovered by another account")
    for target in (foreign, "missing-conversation"):
        with pytest.raises(UserError) as error:
            await app_operation(store, origin, None, {"action": action, "session_id": target}, config)
        assert error.value.code == "not_found"
    assert not await store.rows("SELECT * FROM file_entries WHERE session_id=?", (foreign,))
    assert not await store.rows("SELECT * FROM app_actions")


@pytest.mark.parametrize("action", ["list", "read"])
async def test_request_id_and_files_session_id_are_explicitly_mutually_exclusive(store, account, action):
    _, origin, _ = account
    requested = (await app_operation(store, origin, None, {"action": "open", "target": "history"}))["request"]
    for file_arguments in ({"session_id": origin}, {"scope": "session"}, {"scope": "account"}):
        with pytest.raises(ValueError, match="not both"):
            await app_operation(store, origin, None, {"action": action, "id": requested["id"], **file_arguments})
    read = await app_operation(store, origin, None, {"action": "read", "id": requested["id"]})
    assert read["request"]["id"] == requested["id"] and not read["completed"]


@pytest.mark.parametrize("action", ["list", "read"])
async def test_account_file_catalog_matches_registered_account_files_without_scanning(config, store, account, action):
    user, origin, _ = account
    sibling = await store.session(user_id=user)
    foreign_user, _ = await Auth(store).register("account-file-foreign", "test-password")
    foreign = await store.session(user_id=foreign_user["id"])
    await initialize_files(store)
    for file_id, owner, session in (("origin-registered", user, origin), ("sibling-registered", user, sibling),
                                   ("foreign-registered", foreign_user["id"], foreign)):
        await store.execute("INSERT INTO file_entries VALUES (?,?,?,?,?,?,?,?,?)", (file_id, owner, session, file_id + ".md", file_id + ".md", 3, "text/markdown", "output", now()))
    # Account listing is the existing /api/files inventory, not a filesystem crawl.
    for session in (origin, sibling):
        directory = config.data_dir / "files" / user / session
        directory.mkdir(parents=True)
        (directory / "not-yet-indexed.md").write_text("Do not discover this during account listing")
    requested = (await app_operation(store, origin, None, {"action": "open", "target": "files"}))["request"]
    first = await app_operation(store, origin, None, {"action": action, "scope": "account", "limit": 1}, config)
    second = await app_operation(store, origin, None, {"action": action, "scope": "account", "limit": 1, "offset": first["next_offset"]}, config)
    assert first["files_scope"] == "account" and first["files_session_id"] is None
    assert first["files_total"] == 2 and first["has_more"] and not second["has_more"]
    assert {first["files"][0]["id"], second["files"][0]["id"]} == {"origin-registered", "sibling-registered"}
    assert [item["id"] for item in first["pending"]] == [requested["id"]]
    assert not await store.rows("SELECT id FROM file_entries WHERE name='not-yet-indexed.md'")
    assert (await app_operation(store, origin, None, {"action": "read"}))["files_scope"] == "session"


@pytest.mark.parametrize("action", ["list", "read"])
async def test_account_file_scope_rejects_ambiguous_or_unsupported_combinations(store, account, action):
    _, origin, _ = account
    for extra in ({"session_id": origin}, {"id": "request"}, {"session_id": None}, {"id": None}):
        with pytest.raises(ValueError):
            await app_operation(store, origin, None, {"action": action, "scope": "account", **extra})
    for scope in ("global", "project", None, False):
        with pytest.raises(ValueError):
            await app_operation(store, origin, None, {"action": action, "scope": scope})
