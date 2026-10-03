import asyncio
import json
import pytest
from athena.preferences import preference_schema, read_preferences, update_preferences
from athena.store import now


BASE = {"ui_language": "system", "ui_font": "system", "ui_palette": "dream", "reduced_motion": "system", "presence_size": 84, "developer_mode": False}


async def account(store, identifier):
    await store.execute(
        "INSERT INTO users(id,username,password_hash,role,created_at) VALUES (?,?,?,'user',?)",
        (identifier, identifier, "unused-test-hash", now()),
    )
    return identifier


async def test_preferences_are_account_scoped_persisted_and_audited(store):
    first = await account(store, "first")
    second = await account(store, "second")
    sessions = [await store.session(user_id=first) for _ in range(2)]
    other = await store.session(user_id=second)
    run = await store.enqueue(sessions[0], "Use the Arabic interface")
    assert await read_preferences(store, first) == BASE

    updated = await update_preferences(store, first, {"ui_language": "ar"}, source_run_id=run)
    assert updated == {**BASE, "ui_language": "ar"}
    assert await read_preferences(store, first) == updated
    assert await read_preferences(store, second) == BASE
    rows = await store.rows("SELECT value FROM settings WHERE key=?", (f"user_preferences:{first}",))
    assert json.loads(rows[0]["value"]) == updated
    events = await store.rows("SELECT * FROM events WHERE type='preferences_changed' ORDER BY seq")
    assert {event["session_id"] for event in events} == set(sessions)
    assert all(json.loads(event["payload"]) == {**BASE, "ui_language": "ar", "source_run_id": run} for event in events)
    assert await store.rows("SELECT * FROM events WHERE session_id=?", (other,)) == []

    await update_preferences(store, first, {"ui_language": "ar"})
    assert len(await store.rows("SELECT * FROM events WHERE type='preferences_changed'")) == 2
    assert await update_preferences(store, first, {"ui_language": "system"}) == BASE


@pytest.mark.parametrize("changes", [
    {"ui_language": "de"}, {"ui_language": "zh-CN"}, {"ui_language": None}, {"ui_language": []},
    {"ui_language": "en", "api_key": "test-untrusted-credential"},
    {"provider": "not-an-app-preference"}, {"api_base": "https://example.invalid"},
    {"key": "not-a-preference"}, {"reply_language": "en"},
    {"ui_font": "serif", "ui_palette": "unknown"}, {"ui_font": "custom.ttf"},
    {"ui_palette": "#12345"}, {"ui_palette": "#00000000"}, {"ui_palette": "#abcd"},
    {"ui_palette": "#fff;display:none"}, {"ui_palette": "url(https://example.invalid)"},
    {"presence_size": 31}, {"presence_size": 241}, {"presence_size": True}, {"presence_size": "84"}, {"presence_size": 84.5},
    {"developer_mode": 1}, {"developer_mode": "true"},
    {"reduced_motion": True}, [], None,
])
async def test_invalid_preferences_cannot_partially_apply_or_change_provider_settings(store, changes):
    user = await account(store, "first")
    await store.session(user_id=user)
    await store.execute("INSERT INTO settings VALUES ('deepseek_api_key','test-service-credential')")
    settings_before = await store.rows("SELECT * FROM settings ORDER BY key")
    with pytest.raises(ValueError):
        await update_preferences(store, user, changes)
    assert await read_preferences(store, user) == BASE
    assert await store.rows("SELECT * FROM events") == []
    assert await store.rows("SELECT * FROM settings ORDER BY key") == settings_before


async def test_preferences_reject_unowned_provenance_without_writing(store):
    first = await account(store, "first")
    second = await account(store, "second")
    await store.session(user_id=first)
    other = await store.session(user_id=second)
    foreign_run = await store.enqueue(other, "A separate account's request")
    with pytest.raises(ValueError, match="source"):
        await update_preferences(store, first, {"ui_language": "pt"}, source_run_id=foreign_run)
    with pytest.raises(ValueError, match="account"):
        await update_preferences(store, None, {"ui_language": "en"})
    with pytest.raises(ValueError, match="account"):
        await read_preferences(store, "missing")
    assert await read_preferences(store, first) == BASE
    assert await store.rows("SELECT * FROM events") == []


async def test_concurrent_preference_updates_keep_state_and_events_consistent(store):
    user = await account(store, "first")
    sessions = [await store.session(user_id=user) for _ in range(2)]
    updates = ["en", "fr", "pt", "ja"] * 5
    await asyncio.gather(*(update_preferences(store, user, {"ui_language": locale}) for locale in updates))
    event_values = []
    for session in sessions:
        events = await store.rows("SELECT payload FROM events WHERE session_id=? ORDER BY seq", (session,))
        event_values.append([json.loads(event["payload"])["ui_language"] for event in events])
    assert event_values == [updates, updates]
    assert await read_preferences(store, user) == {**BASE, "ui_language": updates[-1]}


async def test_application_language_and_reply_memory_are_independent(engine, store, monkeypatch):
    user = await account(store, "first")
    session = await store.session(user_id=user)
    run = await store.enqueue(session, "Use Portuguese for future replies")

    async def forbid_shell(*args, **kwargs):
        raise AssertionError("Application preferences must not use external execution")
    monkeypatch.setattr(engine.tools, "shell", forbid_shell)

    result = await engine.tools.execute("runtime", {"op": "app_settings", "settings": {"ui_language": "zh-Hant"}}, session, run)
    assert result["settings"] == {**BASE, "ui_language": "zh-Hant"}
    assert result["schema"] == preference_schema()
    assert await store.rows("SELECT * FROM memories") == []
    await engine.tools.execute("runtime", {"op": "remember", "scope": "global", "content": "The user explicitly prefers future replies in Portuguese."}, session, run)
    assert await engine.tools.execute("runtime", {"op": "app_settings"}, session, run) == result
    memory = (await engine.tools.execute("runtime", {"op": "recall", "query": "Portuguese"}, session, run))[0]
    assert memory["scope"] == "global" and memory["owner_id"] == f"global:{user}"
    assert memory["source_run_id"] == run
    await engine.tools.execute("runtime", {"op": "app_settings", "settings": {"ui_language": "en"}}, session, run)
    stored = (await store.rows("SELECT * FROM memories"))[0]
    assert all(memory[key] == value for key, value in stored.items())
    assert memory['version'] == 1 and memory['status'] == 'active'


async def test_partial_update_preserves_legacy_language_and_other_preferences(store):
    user = await account(store, "first")
    await store.session(user_id=user)
    await store.execute("INSERT INTO settings VALUES (?,?)", (f"user_preferences:{user}", json.dumps({"ui_language": "ja"})))
    assert await read_preferences(store, user) == {**BASE, "ui_language": "ja"}
    customized = await update_preferences(store, user, {"ui_font": "humanist", "ui_palette": "forest", "reduced_motion": "reduce"})
    assert customized == {**BASE, "ui_language": "ja", "ui_font": "humanist", "ui_palette": "forest", "reduced_motion": "reduce"}
    assert await update_preferences(store, user, {"ui_font": "mono"}) == {**customized, "ui_font": "mono"}
    events = await store.rows("SELECT payload FROM events ORDER BY seq")
    assert json.loads(events[0]["payload"]) == {**customized, "source_run_id": None}
    assert json.loads(events[1]["payload"]) == {**customized, "ui_font": "mono", "source_run_id": None}


async def test_preference_discovery_can_drive_a_runtime_update(engine, store):
    user = await account(store, "first")
    session = await store.session(user_id=user)
    response = await engine.tools.execute("runtime", {"op": "app_settings"}, session, None)
    assert response["settings"] == BASE
    assert response["schema"]["additionalProperties"] is False
    fields = response["schema"]["properties"]
    assert set(fields) == set(BASE)
    assert fields["ui_font"]["enum"] == ["system", "humanist", "serif", "mono"]
    assert "enum" not in fields["ui_palette"]
    assert fields["ui_palette"]["anyOf"][0]["enum"] == ["dream", "rose", "ocean", "forest"]
    assert fields["ui_palette"]["anyOf"][1]["pattern"]
    assert fields["reduced_motion"]["enum"] == ["system", "reduce"]
    assert all(field["description"] and field["default"] == BASE[name] for name, field in fields.items())
    response = await engine.tools.execute("runtime", {"op": "app_settings", "settings": {"ui_font": "serif", "ui_palette": "ocean", "reduced_motion": "reduce"}}, session, None)
    assert response["settings"] == {**BASE, "ui_font": "serif", "ui_palette": "ocean", "reduced_motion": "reduce"}
    fields["ui_font"]["enum"].append("unregistered-font")
    with pytest.raises(ValueError):
        await update_preferences(store, user, {"ui_font": "unregistered-font"})


@pytest.mark.parametrize(("requested", "normalized"), [
    ("#000", "#000000"), ("#fff", "#ffffff"), ("#1A2b3C", "#1a2b3c"),
    ("#456789", "#456789"), ("#Fb0", "#ffbb00"),
])
async def test_arbitrary_colors_apply_through_runtime_and_survive_reread(engine, store, requested, normalized):
    user = await account(store, "theme-owner")
    session = await store.session(user_id=user)
    run = await store.enqueue(session, "Use this exact background color")
    result = await engine.tools.execute("runtime", {
        "op": "app_settings", "args": {"settings": {"ui_palette": requested}},
    }, session, run)
    assert result["settings"]["ui_palette"] == normalized
    assert (await read_preferences(store, user))["ui_palette"] == normalized
    saved = await store.rows("SELECT value FROM settings WHERE key=?", (f"user_preferences:{user}",))
    assert json.loads(saved[0]["value"])["ui_palette"] == normalized
    events = await store.rows("SELECT payload FROM events WHERE type='preferences_changed' AND session_id=?", (session,))
    assert json.loads(events[-1]["payload"])["ui_palette"] == normalized


async def test_reset_clears_custom_theme_without_losing_chat(engine, store):
    user = await account(store, "theme-owner")
    session = await store.session(user_id=user)
    original = await store.message(session, None, "user", "Keep my conversation")
    await update_preferences(store, user, {"ui_palette": "#000000"})
    await engine.reset_user(user)
    assert await read_preferences(store, user) == BASE
    assert (await store.history(session))[0]["id"] == original


async def test_presence_size_is_discoverable_persisted_and_resettable(engine, store):
    user = await account(store, "presence-owner")
    session = await store.session(user_id=user)
    run = await store.enqueue(session, "Make your emblem smaller")
    response = await engine.tools.execute("runtime", {
        "op": "app_settings", "args": {"settings": {"presence_size": 48}},
    }, session, run)
    assert response["settings"]["presence_size"] == 48
    assert (await read_preferences(store, user))["presence_size"] == 48
    assert response["schema"]["properties"]["presence_size"]["type"] == "integer"
    assert (await update_preferences(store, user, {"ui_palette": "#000000"}))["presence_size"] == 48
    await engine.reset_user(user)
    assert (await read_preferences(store, user))["presence_size"] == 84


async def test_developer_mode_changes_the_authoritative_account_and_broadcasts(engine, store):
    user = await account(store, "developer-owner")
    session = await store.session(user_id=user)
    run = await store.enqueue(session, "Open your developer panel")
    result = await engine.tools.execute("runtime", {"op": "app_settings", "args": {"settings": {"developer_mode": True}}}, session, run)
    assert result["settings"]["developer_mode"] is True
    assert result["schema"]["properties"]["developer_mode"]["type"] == "boolean"
    assert (await store.rows("SELECT developer_mode FROM users WHERE id=?", (user,)))[0]["developer_mode"] == 1
    assert (await read_preferences(store, user))["developer_mode"] is True
    event = (await store.rows("SELECT payload FROM events WHERE type='preferences_changed' ORDER BY seq DESC LIMIT 1"))[0]
    assert json.loads(event["payload"])["developer_mode"] is True
    assert "password_hash" not in event["payload"] and "api_key" not in event["payload"]
    # The account flag remains authoritative even when changed by an older client.
    await store.execute("UPDATE users SET developer_mode=0 WHERE id=?", (user,))
    assert (await read_preferences(store, user))["developer_mode"] is False
