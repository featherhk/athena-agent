"""Requirement-driven cross-conversation continuity and durable read receipts."""
import asyncio
from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest

from athena.activity import acknowledge_activity, list_activity
from athena.api import create_app
from athena.auth import Auth
from athena.errors import UserError
from athena.store import Store, now, uid
from athena.tools import ToolRunner
from athena.user_help import activate_help, answer_help, request_help


async def account(store, name="traveler"):
    user, _ = await Auth(store).register(name, "activity-test-password")
    return user["id"]


async def run(store, owner, status="running", title="Plan a trip", stamp=None):
    session = await store.session(title=title, user_id=owner)
    identifier = await store.enqueue(session, "Find something useful")
    await store.execute("UPDATE runs SET status=?,phase=?,finished_at=? WHERE id=?",
                        (status, "thinking" if status == "running" else "idle",
                         stamp or now() if status not in ("running", "queued", "waiting_user", "waiting_agents") else None,
                         identifier))
    return session, identifier


async def test_feed_scopes_account_and_only_returns_safe_summary(store):
    owner, other = await account(store), await account(store, "stranger")
    _, active = await run(store, owner, title="A\ntrip\x00 with whitespace")
    _, failed = await run(store, owner, "failed")
    await store.execute("UPDATE runs SET error='secret-provider-response',error_code='step_limit' WHERE id=?", (failed,))
    await run(store, other, "failed", title="private stranger trip")
    feed = await list_activity(store, owner)
    assert feed["owner_id"] == owner and not feed["has_more"]
    assert {item["run_id"] for item in feed["items"]} == {active, failed}
    assert feed["items"][1]["title"] == "A trip with whitespace"
    assert feed["items"][0]["status"] == "failed" and feed["items"][0]["error_code"] == "step_limit"
    assert "secret-provider-response" not in json.dumps(feed)
    key = feed["items"][1]["attention_key"]
    with pytest.raises(UserError, match="not found"):
        await acknowledge_activity(store, other, active, key)
    with pytest.raises(UserError):
        await list_activity(store, None)


async def test_rollout_cutoff_hides_old_terminals_but_not_old_active_work(store):
    owner = await account(store)
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    _, finished_before = await run(store, owner, "complete", stamp=old)
    _, existing_active = await run(store, owner)
    await store.execute("UPDATE runs SET created_at=? WHERE id=?", (old, existing_active))
    assert [item["run_id"] for item in (await list_activity(store, owner))["items"]] == [existing_active]
    await store.execute("UPDATE runs SET status='complete',finished_at=? WHERE id=?", (now(), existing_active))
    assert [item["run_id"] for item in (await list_activity(store, owner))["items"]] == [existing_active]
    assert await store.rows("SELECT id FROM runs WHERE id=?", (finished_before,))


async def test_terminal_ack_racing_a_later_outcome_never_masks_the_new_outcome(store):
    owner = await account(store)
    _, identifier = await run(store, owner, "complete")
    item = (await list_activity(store, owner))["items"][0]
    await asyncio.gather(
        acknowledge_activity(store, owner, identifier, item["attention_key"]),
        store.execute("UPDATE runs SET status='failed',error_code='app_control_failed',finished_at=? WHERE id=?", (now(), identifier)),
    )
    current = (await list_activity(store, owner))["items"][0]
    assert current["status"] == "failed" and current["attention_key"] != item["attention_key"]


async def test_exact_ack_persists_restart_and_stale_ack_does_not_hide_later_outcome(config):
    path = config.data_dir / "activity-restart.sqlite3"
    store = Store(path)
    await store.initialize()
    owner = await account(store)
    _, identifier = await run(store, owner)
    first = (await list_activity(store, owner))["items"][0]
    assert (await acknowledge_activity(store, owner, identifier, first["attention_key"]))["item"] == first
    await store.execute("UPDATE runs SET status='complete',finished_at=? WHERE id=?", (now(), identifier))
    stale = await acknowledge_activity(store, owner, identifier, first["attention_key"])
    assert stale["acknowledged"] is False and stale["item"]["status"] == "complete"
    key = stale["item"]["attention_key"]
    results = await asyncio.gather(*(acknowledge_activity(store, owner, identifier, key) for _ in range(8)))
    assert all(result["acknowledged"] and result["item"] is None for result in results)
    assert not (await list_activity(store, owner))["items"]
    before = (await store.rows("SELECT value FROM settings WHERE key='activity_started_at'"))[0]
    await store.close()
    restored = Store(path)
    await restored.initialize()
    try:
        assert not (await list_activity(restored, owner))["items"]
        assert (await restored.rows("SELECT value FROM settings WHERE key='activity_started_at'"))[0] == before
        assert len(await restored.rows("SELECT * FROM activity_reads")) == 1
        assert len(await restored.history(first["session_id"])) == 1
    finally:
        await restored.close()


async def test_help_remains_visible_after_read_and_resume_has_a_new_key(store):
    owner = await account(store)
    session, identifier = await run(store, owner)
    question = await request_help(store, session, identifier, {"questions": [{"id": "city", "question": "Which city?"}]})
    help_id = question["request"]["id"]
    await activate_help(store, session, identifier, help_id, [])
    item = (await list_activity(store, owner))["items"][0]
    assert item["needs_user"] and item["help_id"] == help_id and item["status"] == "waiting_user"
    await acknowledge_activity(store, owner, identifier, item["attention_key"])
    assert (await list_activity(store, owner))["items"][0] == item
    await answer_help(store, session, owner, help_id, {"city": "Tokyo"})
    queued = (await list_activity(store, owner))["items"][0]
    assert queued["status"] == "queued" and not queued["needs_user"]
    assert queued["attention_key"] != item["attention_key"]
    assert not (await acknowledge_activity(store, owner, identifier, item["attention_key"]))["acknowledged"]
    await store.claim(session)
    second = await request_help(store, session, identifier, {"questions": [{"id": "date", "question": "What date?"}]})
    await activate_help(store, session, identifier, second["request"]["id"], [])
    assert (await list_activity(store, owner))["items"][0]["attention_key"] != item["attention_key"]


async def test_delegated_help_surfaces_only_under_owned_root_and_revises_key(store):
    owner, other = await account(store), await account(store, "otherperson")
    parent_session, parent_run = await run(store, owner, "waiting_agents")
    child_session, child_run = await run(store, owner)
    await store.execute("""INSERT INTO agent_jobs(id,parent_session_id,parent_run_id,child_session_id,child_run_id,
        name,title,definition,profile_id,profile_snapshot,depth,created_at)
        VALUES (?,?,?,?,?,'research','Research','{}','default','{}',1,?)""",
        (uid(), parent_session, parent_run, child_session, child_run, now()))
    initial = (await list_activity(store, owner))["items"]
    assert len(initial) == 1 and initial[0]["run_id"] == parent_run
    help_request = await request_help(store, child_session, child_run, {"questions": [{"id": "detail", "question": "A detail?"}]})
    await activate_help(store, child_session, child_run, help_request["request"]["id"], [])
    updated = (await list_activity(store, owner))["items"][0]
    assert updated["session_id"] == parent_session and updated["status"] == "waiting_agents" and updated["needs_user"]
    assert updated["attention_key"] != initial[0]["attention_key"]
    await store.execute("UPDATE sessions SET user_id=? WHERE id=?", (other, child_session))
    assert not (await list_activity(store, owner))["items"][0]["needs_user"]


async def test_limit_applies_after_reads_and_waiting_is_prioritized(store):
    owner = await account(store)
    for _ in range(4):
        _, identifier = await run(store, owner, "complete")
        item = next(item for item in (await list_activity(store, owner))["items"] if item["run_id"] == identifier)
        await acknowledge_activity(store, owner, identifier, item["attention_key"])
    await run(store, owner, "failed")
    await run(store, owner, "queued")
    result = await list_activity(store, owner, 1)
    assert result["has_more"] and len(result["items"]) == 1 and result["items"][0]["status"] == "failed"
    for limit in (0, 101, True):
        with pytest.raises(ValueError):
            await list_activity(store, owner, limit)


async def test_runtime_activity_matches_http_and_no_other_accounts_or_extra_fields(config):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:4317") as client:
            assert (await client.get("/api/activity")).status_code == 401
            owner = (await client.post("/api/auth/register", json={"username": "apitest", "password": "activity-test-password"})).json()["user"]["id"]
            session, identifier = await run(app.state.store, owner, "failed")
            response = await client.get("/api/activity")
            assert response.status_code == 200 and response.json()["owner_id"] == owner
            tools = ToolRunner(config, app.state.store)
            result = await tools.execute("runtime", {"op": "activity", "args": {"action": "list"}}, session, identifier)
            assert result == response.json()
            item = result["items"][0]
            acknowledgement = {"run_id": identifier, "attention_key": item["attention_key"]}
            assert (await client.post("/api/activity/ack", json={**acknowledgement, "user_id": "other"})).status_code == 422
            assert (await client.post("/api/activity/ack", json=acknowledgement)).json() == {"owner_id": owner, "acknowledged": True, "item": None}
            assert not (await tools.execute("runtime", {"op": "activity", "args": {"action": "list"}}, session, identifier))["items"]
            with pytest.raises(ValueError, match="Unexpected"):
                await tools.execute("runtime", {"op": "activity", "args": {"action": "list", "session_id": session}}, session, identifier)
            assert (await client.get("/api/activity?limit=101")).status_code == 400


async def test_unread_result_survives_days_away_and_restart_reports_interruption(config):
    path = config.data_dir / "activity-away.sqlite3"
    store = Store(path)
    await store.initialize()
    owner = await account(store)
    earlier = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    await store.execute("UPDATE settings SET value=? WHERE key='activity_started_at'", (earlier,))
    yesterday = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    _, finished = await run(store, owner, "complete", stamp=yesterday)
    _, active = await run(store, owner)
    await store.close()
    restored = Store(path)
    await restored.initialize()
    try:
        items = (await list_activity(restored, owner))["items"]
        assert {item["run_id"] for item in items} == {finished, active}
        assert next(item for item in items if item["run_id"] == active)["status"] == "interrupted"
    finally:
        await restored.close()


def tool_call(identifier, operation, **args):
    return {"id": identifier, "type": "function", "function": {
        "name": "runtime", "arguments": json.dumps({"op": operation, "args": args})}}


def model_response(*calls):
    return {"message": {"role": "assistant", "content": "" if calls else "Done.",
                        "reasoning_content": "", "tool_calls": list(calls)}, "usage": {}}


async def test_runtime_activity_self_stop_preserves_pairing_and_does_not_deadlock(engine, store, monkeypatch):
    owner = await account(store)
    session = await store.session(user_id=owner)
    async def provider(*args, **kwargs):
        return model_response(tool_call("stop", "activity", action="stop", session_id=session),
                              tool_call("later", "remember", content="must not run", scope="session"))
    monkeypatch.setattr("athena.engine.complete", provider)
    identifier = await engine.submit(session, "Stop here.")
    await asyncio.wait_for(asyncio.gather(*engine.actors.values()), 3)
    assert (await store.rows("SELECT status FROM runs WHERE id=?", (identifier,)))[0]["status"] == "interrupted"
    tools = [row for row in await store.history(session) if row["role"] == "tool"]
    assert [row["tool_call_id"] for row in tools] == ["stop", "later"]
    assert json.loads(tools[1]["content"])["status"] == "not_executed"
    assert not await store.rows("SELECT * FROM memories")


async def test_runtime_activity_stops_other_live_run_and_queue_before_confirming(engine, store, monkeypatch):
    owner = await account(store)
    target, source = await store.session(user_id=owner), await store.session(user_id=owner)
    target_entered = asyncio.Event()
    async def provider(client, base, key, payload, *args, **kwargs):
        message = next(item["content"] for item in payload["messages"] if item["role"] == "user")
        if message == "Long research":
            target_entered.set()
            await asyncio.Event().wait()
        if payload["messages"][-1]["role"] == "tool":
            return model_response()
        return model_response(tool_call("stop-other", "activity", action="stop", session_id=target))
    monkeypatch.setattr("athena.engine.complete", provider)
    target_run = await engine.submit(target, "Long research")
    await asyncio.wait_for(target_entered.wait(), 3)
    queued = await engine.submit(target, "Follow-up research")
    source_run = await engine.submit(source, "Stop that research")
    await asyncio.wait_for(asyncio.gather(*engine.actors.values()), 3)
    states = {row["id"]: row["status"] for row in await store.rows("SELECT id,status FROM runs")}
    assert states == {target_run: "interrupted", queued: "cancelled", source_run: "complete"}
    receipt = json.loads(next(row["content"] for row in await store.history(source) if row["role"] == "tool"))
    assert receipt == {"status": "applied", "completed": True, "action": "stop", "session_id": target, "remaining": []}
    assert not engine.tool_controls


async def test_runtime_activity_stop_is_owned_and_requires_active_source(engine, store):
    owner, other = await account(store), await account(store, "stranger")
    source, source_run = await run(store, owner)
    target, target_run = await run(store, other, "queued")
    with pytest.raises(UserError):
        await engine.tools.execute("runtime", {"op": "activity", "args": {"action": "stop", "session_id": target}}, source, source_run)
    assert (await store.rows("SELECT status FROM runs WHERE id=?", (target_run,)))[0]["status"] == "queued"
    await store.execute("UPDATE runs SET status='complete' WHERE id=?", (source_run,))
    with pytest.raises(ValueError, match="active engine"):
        await engine.tools.execute("runtime", {"op": "activity", "args": {"action": "stop", "session_id": source}}, source, source_run)


async def test_two_model_runs_can_stop_one_another_without_cancellation_cycle(engine, store, monkeypatch):
    owner = await account(store)
    sessions = [await store.session(user_id=owner) for _ in range(2)]
    entered = [asyncio.Event(), asyncio.Event()]
    async def provider(client, base, key, payload, *args, **kwargs):
        index = int(next(item["content"] for item in payload["messages"] if item["role"] == "user"))
        entered[index].set()
        await entered[1-index].wait()
        return model_response(tool_call(f"stop-{index}", "activity", action="stop", session_id=sessions[1-index]))
    monkeypatch.setattr("athena.engine.complete", provider)
    # Force both validated controls to be admitted before either cancellation.
    stop = engine.tools.stop_session
    stops_entered = [asyncio.Event(), asyncio.Event()]
    async def synchronized_stop(source, source_run, target):
        index = sessions.index(source)
        stops_entered[index].set()
        await stops_entered[1-index].wait()
        return await stop(source, source_run, target)
    engine.tools.stop_session = synchronized_stop
    for index, session in enumerate(sessions):
        await engine.submit(session, str(index))
    await asyncio.wait_for(asyncio.gather(*engine.actors.values()), 3)
    await asyncio.wait_for(asyncio.gather(*engine.tool_controls), 3)
    assert all(row["status"] == "interrupted" for row in await store.rows("SELECT status FROM runs"))
    assert not engine.active_runs and not engine.tool_controls
