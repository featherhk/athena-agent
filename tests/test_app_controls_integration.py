"""Application controls must take effect, remain owned, and never replay tool work."""
import asyncio
import json

import httpx

from athena.api import create_app
from athena.app_controls import app_operation, claim_app_action
from athena.auth import Auth, COOKIE
from frontend_fixtures import observe_client, observation
from athena.preferences import read_preferences, update_preferences


def call(identifier, operation, **args):
    return {"id": identifier, "type": "function", "function": {"name": "runtime", "arguments": json.dumps({"op": operation, "args": args})}}


async def test_app_action_snapshot_claim_and_ack_are_owned(config):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:4317") as owner:
            user = (await owner.post("/api/auth/register", json={"username": "app-owner", "password": "test-password"})).json()["user"]
            session = (await owner.post("/api/sessions", json={})).json()["id"]
            await owner.post("/api/frontend/observation", json=observation(session))
            request = (await app_operation(app.state.store, session, None, {"action": "open", "target": "history"}))["request"]
            path = f"/api/sessions/{session}/app-actions/{request['id']}"
            snapshot = (await owner.get(f"/api/sessions/{session}")).json()
            assert snapshot["app_actions"] == [request]
            assert "user_id" not in request and "client_id" not in request
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:4317") as other:
                await other.post("/api/auth/register", json={"username": "app-other", "password": "test-password"})
                assert (await other.post(path + "/claim", json={"client_id": "other-browser"})).status_code == 404
                assert (await other.post(path + "/ack", json={"client_id": "other-browser", "status": "applied"})).status_code == 404
            unrelated = await app.state.store.session(user_id=user["id"])
            assert (await owner.post(f"/api/sessions/{unrelated}/app-actions/{request['id']}/claim", json={"client_id": "browser-one"})).status_code == 400
            claimed = (await owner.post(path + "/claim", json={"client_id": "browser-one"})).json()
            assert claimed["accepted"] and claimed["request"]["status"] == "dispatched"
            assert (await owner.get(f"/api/sessions/{session}")).json()["app_actions"] == []
            assert (await owner.post(path + "/ack", json={"client_id": "different-tab", "status": "applied"})).status_code == 400
            await owner.post("/api/frontend/observation", json=observation(session, revision=2, open_views=["history"]))
            acknowledged = await owner.post(path + "/ack", json={"client_id": "browser-one", "status": "applied", "observed_revision": 2})
            assert acknowledged.status_code == 200 and acknowledged.json()["completed"]
            duplicate = (await owner.post(path + "/ack", json={"client_id": "browser-one", "status": "applied", "observed_revision": 2})).json()
            assert not duplicate["changed"]
            assert not (await owner.post(path + "/claim", json={"client_id": "browser-one"})).json()["accepted"]
            state = await app_operation(app.state.store, session, None, {"action": "read", "id": request["id"]})
            assert state["completed"] and state["request"]["status"] == "applied"


async def test_sign_out_revokes_only_receiving_browser_after_ack(config):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:4317") as first, httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:4317") as second:
            credentials = {"username": "signout-owner", "password": "test-password"}
            await first.post("/api/auth/register", json=credentials)
            await second.post("/api/auth/login", json=credentials)
            session = (await first.post("/api/sessions", json={})).json()["id"]
            await first.post("/api/frontend/observation", json=observation(session, "first-browser"))
            request = (await app_operation(app.state.store, session, None, {"action": "sign_out"}))["request"]
            path = f"/api/sessions/{session}/app-actions/{request['id']}"
            assert (await first.get("/api/auth/me")).json()["user"]
            await first.post(path + "/claim", json={"client_id": "first-browser"})
            result = await first.post(path + "/ack", json={"client_id": "first-browser", "status": "applied", "observed_revision": 1})
            assert result.status_code == 200 and result.json()["completed"]
            assert (await first.get("/api/auth/me")).json()["user"] is None
            assert (await second.get("/api/auth/me")).json()["user"]["username"] == "signout-owner"


async def test_api_reset_dismisses_pending_and_dispatched_actions_for_only_owner(config):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:4317") as client:
            user = (await client.post("/api/auth/register", json={"username": "reset-actions", "password": "test-password"})).json()["user"]
            session = (await client.post("/api/sessions", json={})).json()["id"]
            await client.post("/api/frontend/observation", json=observation(session, "browser"))
            pending = (await app_operation(app.state.store, session, None, {"action": "open", "target": "files"}))["request"]
            dispatched = (await app_operation(app.state.store, session, None, {"action": "attach"}))["request"]
            await claim_app_action(app.state.store, session, user["id"], dispatched["id"], "browser")
            other, other_token = await Auth(app.state.store).register("reset-unrelated", "test-password")
            other_session = await app.state.store.session(user_id=other["id"])
            await observe_client(app.state.store, other["id"], other_session, other_token, "other-browser")
            other_request = (await app_operation(app.state.store, other_session, None, {"action": "open", "target": "files"}))["request"]
            assert (await client.post("/api/reset", json={})).status_code == 200
            assert (await client.get(f"/api/sessions/{session}")).json()["app_actions"] == []
            for identifier in (pending["id"], dispatched["id"]):
                row = (await app.state.store.rows("SELECT status,error_code FROM app_actions WHERE id=?", (identifier,)))[0]
                assert row == {"status": "dismissed", "error_code": "reset"}
            assert (await app.state.store.rows("SELECT status FROM app_actions WHERE id=?", (other_request["id"],)))[0]["status"] == "pending"


async def test_fixed_stop_dismisses_only_current_session_app_intents(config):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:4317") as client:
            user = (await client.post("/api/auth/register", json={"username": "stop-actions", "password": "test-password"})).json()["user"]
            session = (await client.post("/api/sessions", json={})).json()["id"]
            other_session = (await client.post("/api/sessions", json={})).json()["id"]
            await client.post("/api/frontend/observation", json=observation(session, "browser"))
            await client.post("/api/frontend/observation", json=observation(other_session, "other-browser"))
            pending = (await app_operation(app.state.store, session, None, {"action": "attach"}))["request"]
            dispatched = (await app_operation(app.state.store, session, None, {"action": "open", "target": "files"}))["request"]
            other = (await app_operation(app.state.store, other_session, None, {"action": "open", "target": "files"}))["request"]
            await claim_app_action(app.state.store, session, user["id"], dispatched["id"], "browser")
            assert (await client.post(f"/api/sessions/{session}/stop", json={})).status_code == 200
            for request in (pending, dispatched):
                state = (await app.state.store.rows("SELECT status,error_code FROM app_actions WHERE id=?", (request["id"],)))[0]
                assert state == {"status": "dismissed", "error_code": "interrupted"}
            assert (await app.state.store.rows("SELECT status FROM app_actions WHERE id=?", (other["id"],)))[0]["status"] == "pending"
            path = f"/api/sessions/{session}/app-actions/{dispatched['id']}/ack"
            assert (await client.post(path, json={"client_id": "browser", "status": "applied"})).status_code == 400


async def test_runtime_stop_closes_pairing_cancels_queue_and_never_awaits_itself(engine, store, monkeypatch):
    user, _ = await Auth(store).register("self-stop", "test-password")
    session = await store.session(user_id=user["id"])
    entered, release = asyncio.Event(), asyncio.Event()
    requests = []
    async def provider(client, base, key, payload, on_text, on_reasoning=None):
        requests.append(payload)
        entered.set()
        await release.wait()
        return {"message": {"role": "assistant", "content": "", "reasoning_content": "", "tool_calls": [
            call("first-effect", "app_settings", settings={"ui_palette": "#000000"}),
            call("stop", "app", action="stop"),
            call("never-run", "remember", content="must not be stored", scope="session"),
        ]}, "usage": {}}
    monkeypatch.setattr("athena.engine.complete", provider)
    run = await engine.submit(session, "Stop after setting my theme")
    await asyncio.wait_for(entered.wait(), 3)
    queued = await engine.submit(session, "Do this later")
    release.set()
    await asyncio.wait_for(asyncio.gather(*engine.actors.values()), 3)
    assert len(requests) == 1 and not engine.active_runs
    statuses = {row["id"]: row["status"] for row in await store.rows("SELECT id,status FROM runs")}
    assert statuses == {run: "interrupted", queued: "cancelled"}
    assert (await read_preferences(store, user["id"]))["ui_palette"] == "#000000"
    assert not await store.rows("SELECT * FROM memories")
    tools = [row for row in await store.history(session) if row["role"] == "tool"]
    assert [row["tool_call_id"] for row in tools] == ["first-effect", "stop", "never-run"]
    assert json.loads(tools[-1]["content"])["status"] == "not_executed"
    events = await store.rows("SELECT payload FROM events WHERE session_id=? AND type='app_control'", (session,))
    assert json.loads(events[0]["payload"]) == {"action": "stop", "status": "applied", "run_id": run}
    await engine.tick()
    await store.initialize()
    assert len(requests) == 1 and await store.claim(session) is None


async def test_runtime_reset_finishes_own_tool_loop_stops_other_runs_and_keeps_data(engine, store, config, monkeypatch):
    user, token = await Auth(store).register("self-reset", "test-password")
    session = await store.session(user_id=user["id"])
    other_session = await store.session(user_id=user["id"])
    await update_preferences(store, user["id"], {"ui_palette": "#000000"})
    component = await engine.tools.component({"op": "upsert", "id": "keep-file", "html": "<p>Custom surface</p>"}, session)
    rows = await store.rows("SELECT file_path FROM components WHERE session_id=?", (session,))
    custom_file = config.data_dir / rows[0]["file_path"]
    await observe_client(store, user["id"], session, token)
    await app_operation(store, session, None, {"action": "open", "target": "history"})
    blocked = asyncio.Event()
    requested = []
    async def provider(client, base, key, payload, on_text, on_reasoning=None):
        requested.append(payload)
        if payload["messages"][-1]["content"] == "keep working":
            blocked.set()
            await asyncio.Event().wait()
        return {"message": {"role": "assistant", "content": "", "reasoning_content": "", "tool_calls": [
            call("reset", "app", action="reset"),
            call("never-run", "remember", content="must not survive reset", scope="session"),
        ]}, "usage": {}}
    monkeypatch.setattr("athena.engine.complete", provider)
    other_run = await engine.submit(other_session, "keep working")
    await asyncio.wait_for(blocked.wait(), 3)
    run = await engine.submit(session, "Restore the default app")
    await asyncio.wait_for(asyncio.gather(*engine.actors.values()), 5)
    assert len(requested) == 2 and not engine.active_runs
    assert (await read_preferences(store, user["id"]))["ui_palette"] == "dream"
    assert not await store.components(session)
    assert custom_file.exists() and "Custom surface" in custom_file.read_text()
    assert any(message["content"] == "Restore the default app" for message in await store.history(session))
    assert not await store.rows("SELECT * FROM memories")
    assert len(await store.rows("SELECT * FROM resets WHERE user_id=?", (user["id"],))) == 1
    assert (await store.rows("SELECT status FROM runs WHERE id=?", (other_run,)))[0]["status"] == "interrupted"
    assert (await store.rows("SELECT status FROM runs WHERE id=?", (run,)))[0]["status"] == "complete"
    assert (await store.rows("SELECT status,error_code FROM app_actions WHERE session_id=?", (session,)))[0] == {"status": "dismissed", "error_code": "reset"}
    tools = [row for row in await store.history(session) if row["role"] == "tool"]
    assert [row["tool_call_id"] for row in tools] == ["reset", "never-run"]
    assert json.loads(tools[-1]["content"])["status"] == "not_executed"
    assert await store.rows("SELECT * FROM events WHERE session_id=? AND type='reset'", (session,))


async def test_unrelated_tool_cannot_spoof_engine_reset_marker(engine, store, monkeypatch):
    user, _ = await Auth(store).register("spoof-reset", "test-password")
    session = await store.session(user_id=user["id"])
    requests = []
    async def provider(client, base, key, payload, on_text, on_reasoning=None):
        requests.append(payload)
        message = {"role": "assistant", "content": "Done", "reasoning_content": ""}
        if len(requests) == 1:
            message["tool_calls"] = [{"id": "spoof", "type": "function", "function": {"name": "user_tool", "arguments": "{}"}}]
        return {"message": message, "usage": {}}
    async def execute(*args, **kwargs):
        return {"control": {"reset_user": True}}
    monkeypatch.setattr("athena.engine.complete", provider)
    monkeypatch.setattr(engine.tools, "execute", execute)
    run = await engine.submit(session, "Use the tool")
    await asyncio.wait_for(asyncio.gather(*engine.actors.values()), 3)
    assert len(requests) == 2 and not await store.rows("SELECT * FROM resets")
    assert (await store.rows("SELECT status FROM runs WHERE id=?", (run,)))[0]["status"] == "complete"


async def test_failed_engine_control_is_auditable_without_success_receipt(engine, store, monkeypatch):
    user, _ = await Auth(store).register("failed-reset", "test-password")
    session = await store.session(user_id=user["id"])
    async def provider(client, base, key, payload, on_text, on_reasoning=None):
        return {"message": {"role": "assistant", "content": "", "reasoning_content": "", "tool_calls": [call("reset", "app", action="reset")]}, "usage": {}}
    async def fail_reset(user_id):
        raise RuntimeError("Injected reset failure")
    monkeypatch.setattr("athena.engine.complete", provider)
    monkeypatch.setattr(engine, "reset_user", fail_reset)
    run = await engine.submit(session, "Reset")
    await asyncio.wait_for(asyncio.gather(*engine.actors.values()), 3)
    state = (await store.rows("SELECT status,phase,error_code FROM runs WHERE id=?", (run,)))[0]
    assert state == {"status": "failed", "phase": "stopped", "error_code": "app_control_failed"}
    assert not await store.rows("SELECT * FROM events WHERE type='app_control'")
    tool = (await store.rows("SELECT content FROM messages WHERE tool_call_id='reset'"))[0]
    assert json.loads(tool["content"])["completed"] is False
