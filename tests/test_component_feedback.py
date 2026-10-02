import json
import httpx
import pytest

from athena.api import create_app
from athena.auth import Auth
from athena.registry import Registry


async def component(registry, session, **changes):
    return await registry.operate({"kind": "component", "action": "register", "id": "weather",
                                   "title": "Weather", "source": "<p>Waiting for a city</p>",
                                   "placement": "top-start", **changes}, session)


async def test_registration_is_not_rendering_and_reports_belong_to_exact_version(config, store):
    user, _ = await Auth(store).register("widgets", "test-password")
    session = await store.session(user_id=user["id"])
    registry = Registry(config, store)
    original = await component(registry, session)
    assert original["placement"] == "top-start"
    assert original["render"]["state"] == "unobserved"
    event = (await store.rows("SELECT * FROM events WHERE session_id=? ORDER BY seq DESC LIMIT 1", (session,)))[0]
    assert event["type"] == "registry_changed"
    assert json.loads(event["payload"])["version"] == original["version"]
    report = dict(instance="browser-one", state="mounted", has_content=True, in_view=False)
    accepted = await registry.report_component(session, "weather", 1, **report)
    assert accepted["accepted"] and accepted["render"]["functional_verification"] == "not_performed"
    assert accepted["render"]["in_view"] is False
    await registry.report_component(session, "weather", 1, **report)
    assert len(await store.rows("SELECT * FROM component_reports")) == 1
    await registry.report_component(session, "weather", 1, **{**report, "state": "failed", "error_code": "blocked_resource"})
    read = await registry.operate({"kind": "component", "action": "read", "id": "weather"}, session)
    assert read["render"]["state"] == "failed" and read["render"]["error_code"] == "blocked_resource"
    newer = await component(registry, session, source="<p>New version</p>")
    assert newer["render"]["state"] == "unobserved"
    assert (await registry.report_component(session, "weather", 1, **report))["accepted"] is False
    historical = await registry.operate({"kind": "component", "action": "read", "id": "weather", "version": 1}, session)
    assert historical["enabled"] is False and historical["render"]["state"] == "failed"
    restored = await registry.operate({"kind": "component", "action": "restore", "id": "weather", "version": 1}, session)
    assert restored["render"]["state"] == "unobserved" and restored["placement"] == "top-start"
    await registry.operate({"kind": "component", "action": "remove", "id": "weather"}, session)
    removal = (await store.rows("SELECT * FROM events WHERE session_id=? ORDER BY seq DESC LIMIT 1", (session,)))[0]
    assert removal["type"] == "registry_changed" and json.loads(removal["payload"])["enabled"] is False
    assert (await registry.report_component(session, "weather", restored["version"], **report))["accepted"] is False


async def test_fork_does_not_inherit_browser_observations(config, store):
    user, _ = await Auth(store).register("forkwidgets", "test-password")
    session = await store.session(user_id=user["id"])
    registry = Registry(config, store)
    await component(registry, session)
    await registry.report_component(session, "weather", 1, "original-browser", "mounted", True, True)
    fork = await store.fork(session)
    copied = await registry.operate({"kind": "component", "action": "read", "id": "weather"}, fork)
    assert copied["placement"] == "top-start" and copied["render"]["state"] == "unobserved"


@pytest.mark.parametrize("placement", ["overlay", "fullscreen", "presence", "sidebar-start", "sidebar-end"])
async def test_flexible_surfaces_are_versioned_and_restore_their_original_placement(config, store, placement):
    user, _ = await Auth(store).register("surfaces", "test-password")
    session = await store.session(user_id=user["id"])
    registry = Registry(config, store)
    original = await component(registry, session, placement=placement, source="<canvas></canvas>")
    await component(registry, session, placement="inline")
    restored = await registry.operate({"kind": "component", "action": "restore", "id": "weather", "version": original["version"]}, session)
    assert restored["placement"] == placement and restored["render"]["state"] == "unobserved"
    assert (await store.components(session))[0]["placement"] == placement


async def test_component_observations_require_session_owner_and_valid_state(config):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:4317") as client:
            await client.post("/api/auth/register", json={"username": "owner", "password": "test-password"})
            session = (await client.post("/api/sessions", json={})).json()["id"]
            await component(app.state.engine.tools.registry, session)
            endpoint = f"/api/sessions/{session}/components/weather/report"
            report = {"version": 1, "instance": "frame-one", "state": "mounted", "has_content": True, "in_view": True}
            assert (await client.post(endpoint, json=report)).status_code == 200
            assert (await client.post(endpoint, json={**report, "state": "failed"})).status_code == 400
            assert (await client.post(endpoint, json={**report, "error_code": "private raw exception"})).status_code == 422
            await client.post("/api/auth/logout", json={})
            assert (await client.post(endpoint, json=report)).status_code == 401
            await client.post("/api/auth/register", json={"username": "outsider", "password": "test-password"})
            assert (await client.post(endpoint, json=report)).status_code == 404
            assert len(await app.state.store.rows("SELECT * FROM component_reports")) == 1
