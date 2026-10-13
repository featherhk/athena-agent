import asyncio
from dataclasses import replace
import json

import httpx
import pytest

from athena.auth import Auth
from athena.errors import ServiceError
from athena.model_service import ModelProvider, ModelService
from athena.profiles import Profile
from athena.store import now
from athena.store import Store


def response(text="Done", calls=None):
    message = {"role": "assistant", "content": text, "reasoning_content": ""}
    if calls:
        message["tool_calls"] = calls
    return {"message": message, "usage": {}, "model": "synthetic"}


def provider(identifier, callback, version="1"):
    return ModelProvider(identifier, version, "chat_completions", callback)


async def noop(_):
    pass


async def test_activation_preserves_inflight_binding_and_rejects_invalid_candidate():
    service = ModelService()
    entered, release = asyncio.Event(), asyncio.Event()

    async def first(*args, **kwargs):
        entered.set()
        await release.wait()
        return response("old request")

    async def second(*args, **kwargs):
        return response("new request")

    service.activate(provider("test.first", first))
    binding = service.bind()
    task = asyncio.create_task(service.complete(binding, None, "", "", {}, noop, noop))
    await entered.wait()
    service.activate(provider("test.second", second))
    next_binding = service.bind()
    with pytest.raises(ValueError):
        service.activate(provider("invalid", lambda: None))
    with pytest.raises(ValueError, match="immutable"):
        service.activate(provider("test.first", second))
    assert service.bind() is next_binding
    release.set()
    assert (await task)["message"]["content"] == "old request"
    assert (await service.complete(next_binding, None, "", "", {}, noop, noop))["message"]["content"] == "new request"
    assert binding.snapshot()["generation"] == 1 and next_binding.snapshot()["generation"] == 2


async def test_old_failure_cannot_quarantine_new_activation_or_retry_request():
    calls = []
    entered, release = asyncio.Event(), asyncio.Event()

    async def failing(*args, **kwargs):
        calls.append("old")
        entered.set()
        await release.wait()
        raise RuntimeError("synthetic failure")

    async def healthy(*args, **kwargs):
        calls.append("new")
        return response()

    service = ModelService()
    service.activate(provider("test.old", failing))
    task = asyncio.create_task(service.complete(service.bind(), None, "", "", {}, noop, noop))
    await entered.wait()
    service.activate(provider("test.new", healthy))
    binding = service.bind()
    release.set()
    with pytest.raises(RuntimeError):
        await task
    assert service.bind() is binding and calls == ["old"]


async def test_provider_contract_failure_falls_back_only_for_later_binding():
    calls = []

    async def invalid(*args, **kwargs):
        calls.append("candidate")
        return response(calls=[{"id": "same", "type": "function", "function": {"name": "exec", "arguments": "{}"}}] * 2)

    async def default(*args, **kwargs):
        calls.append("default")
        return response()

    service = ModelService(default_complete=lambda: default)
    service.activate(provider("test.invalid", invalid, "7"))
    with pytest.raises(ServiceError) as caught:
        await service.complete(service.bind(), None, "", "", {}, noop, noop)
    assert caught.value.code == "provider_contract_error"
    assert calls == ["candidate"]
    binding = service.bind()
    assert binding.snapshot()["fallback_from"] == "test.invalid@7"
    assert binding.snapshot()["provider_id"] == "builtin.chat_completions"
    await service.complete(binding, None, "", "", {}, noop, noop)
    assert calls == ["candidate", "default"]


async def test_cancellation_keeps_provider_and_does_not_replay():
    entered = asyncio.Event()

    async def slow(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    service = ModelService()
    service.activate(provider("test.slow", slow))
    binding = service.bind()
    task = asyncio.create_task(service.complete(binding, None, "", "", {}, noop, noop))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert service.bind() is binding


async def test_service_rejects_cross_loop_access_and_foreign_bindings():
    calls = []

    async def callback(*args, **kwargs):
        calls.append(1)
        return response()

    service, other = ModelService(), ModelService()
    service.activate(provider("test.bound", callback))
    binding = service.bind()
    with pytest.raises(ValueError, match="different host"):
        await other.complete(binding, None, "", "", {}, noop, noop)
    with pytest.raises(ValueError, match="different host"):
        await other.record(None, "never-recorded", binding)

    async def foreign_loop():
        for action in (service.bind, service.restore_default, lambda: service.activate(provider("test.other", callback))):
            with pytest.raises(RuntimeError, match="event loop"):
                action()
        with pytest.raises(RuntimeError, match="event loop"):
            await service.complete(binding, None, "", "", {}, noop, noop)
        with pytest.raises(RuntimeError, match="event loop"):
            await service.record(None, "never-recorded", binding)

    await asyncio.to_thread(lambda: asyncio.run(foreign_loop()))
    assert calls == [] and service.bind() is binding


async def test_second_wire_adapter_uses_json_body_and_preserves_tools_reasoning_usage():
    requests, text, reasoning = [], [], []
    call = {"id": "one", "type": "function", "function": {"name": "runtime", "arguments": '{"op":"context"}'}}

    def endpoint(request):
        requests.append(request)
        return httpx.Response(200, json={"model": "actual-model", "choices": [{"finish_reason": "tool_calls",
            "message": {"content": "Looking", "reasoning_content": "Provider reasoning", "tool_calls": [call]}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 4}})

    async def receive_text(value):
        text.append(value)

    async def receive_reasoning(value):
        reasoning.append(value)

    profile = Profile(id="json", provider="Local", model="model", base_model="Model",
                      api_base="http://localhost:9999/v1", protocol="chat_completions_json")
    service = ModelService()
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        result = await service.complete(service.bind(profile.protocol), client, profile.api_base, "synthetic",
                                        profile.payload([], []), receive_text, receive_reasoning)
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/chat/completions"
    body = json.loads(requests[0].content)
    assert body["stream"] is False and "stream_options" not in body
    assert result["message"]["tool_calls"] == [call]
    assert result["usage"] == {"prompt_tokens": 11, "completion_tokens": 4}
    assert result["model"] == "actual-model" and text == ["Looking"] and reasoning == ["Provider reasoning"]
    with pytest.raises(ServiceError, match="unavailable"):
        service.bind(profile.protocol, hosted=True)


@pytest.mark.parametrize("code", [401, 402, 429, 503])
async def test_json_adapter_http_failure_does_not_disclose_body_or_retry(code):
    requests = []

    def endpoint(request):
        requests.append(request)
        return httpx.Response(code, json={"error": "private-provider-message"})

    service = ModelService()
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        with pytest.raises(ServiceError) as caught:
            await service.complete(service.bind("chat_completions_json"), client, "https://example.invalid", "synthetic", {}, noop, noop)
    assert "private-provider-message" not in str(caught.value) and len(requests) == 1


async def test_engine_provider_swap_binds_real_requests_and_retains_prefix(engine, store):
    entered, release = asyncio.Event(), asyncio.Event()
    payloads = []

    async def first(client, base, key, payload, on_text, on_reasoning, *, request_id=None):
        payloads.append(payload)
        entered.set()
        await release.wait()
        await on_text("First")
        return response("First")

    async def second(client, base, key, payload, on_text, on_reasoning, *, request_id=None):
        payloads.append(payload)
        await on_text("Second")
        return response("Second")

    session = await store.session()
    engine.model_service.activate(provider("test.first", first))
    first_run = await engine.submit(session, "First request")
    await entered.wait()
    engine.model_service.activate(provider("test.second", second))
    release.set()
    await engine.actors[session]
    second_run = await engine.submit(session, "Second request")
    await engine.actors[session]
    rows = await store.rows("SELECT r.run_id,r.status,b.snapshot FROM requests r JOIN model_provider_bindings b ON b.request_id=r.id ORDER BY r.rowid")
    assert [row["run_id"] for row in rows] == [first_run, second_run]
    assert [row["status"] for row in rows] == ["complete", "complete"]
    assert [json.loads(row["snapshot"])["provider_id"] for row in rows] == ["test.first", "test.second"]
    assert payloads[0]["messages"][0] == payloads[1]["messages"][0]
    assert "test-provider-key" not in json.dumps(rows)


async def test_failure_after_tool_keeps_durable_effect_once_then_new_request_falls_back(engine, store, monkeypatch):
    user, _ = await Auth(store).register("provider-test", "synthetic-password")
    session = await store.session(user_id=user["id"])
    invocations, fallback_payloads = [], []
    call = {"id": "remember-once", "type": "function", "function": {"name": "runtime", "arguments": json.dumps({
        "op": "memory", "args": {"action": "add", "scope": "session", "content": "Synthetic remembered preference."}})}}

    async def candidate(client, base, key, payload, on_text, on_reasoning, *, request_id=None):
        invocations.append(payload)
        if len(invocations) == 1:
            return response("", [call])
        assert payload["messages"][-1]["tool_call_id"] == "remember-once"
        await on_text("Partial")
        raise ServiceError("provider_stream_error", "Synthetic stream failure")

    async def default(client, base, key, payload, on_text, on_reasoning, **kwargs):
        fallback_payloads.append(payload)
        await on_text("Recovered on the new request")
        return response("Recovered on the new request")

    monkeypatch.setattr("athena.engine.complete", default)
    engine.model_service.activate(provider("test.candidate", candidate))
    failed = await engine.submit(session, "Remember one preference")
    await engine.actors[session]
    assert len(invocations) == 2 and fallback_payloads == []
    assert (await store.rows("SELECT status FROM runs WHERE id=?", (failed,)))[0]["status"] == "failed"
    memories = await engine.tools.runtime({"op": "memory", "action": "list", "scope": "session"}, session, None)
    assert len(memories["items"]) == 1
    await engine.submit(session, "Continue without repeating the memory write")
    await engine.actors[session]
    assert len(fallback_payloads) == 1
    assert sum(item.get("tool_call_id") == "remember-once" for item in fallback_payloads[0]["messages"]) == 1
    assert len((await engine.tools.runtime({"op": "memory", "action": "list", "scope": "session"}, session, None))["items"]) == 1
    snapshots = [json.loads(row["snapshot"]) for row in await store.rows("SELECT snapshot FROM model_provider_bindings ORDER BY rowid")]
    assert [item["provider_id"] for item in snapshots] == ["test.candidate", "test.candidate", "builtin.chat_completions"]
    assert snapshots[-1]["fallback_from"] == "test.candidate@1"


async def test_json_profile_switch_is_audited_and_preserves_old_request(engine, store):
    profile = Profile(id="json", provider="Local", model="local", base_model="Local",
                      api_base="https://example.invalid/v1", protocol="chat_completions_json")
    await store.execute("INSERT INTO model_profiles VALUES (?,?,1,?)", (profile.id, json.dumps(profile.snapshot()), now()))
    await store.execute("INSERT INTO settings VALUES (?,?)", ("provider_key:json", "synthetic"))
    sent = []

    def endpoint(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "JSON ready"}}]})

    await engine.client.aclose()
    engine.client = httpx.AsyncClient(transport=httpx.MockTransport(endpoint))
    session = await store.session()
    await engine.models.switch(session, profile.id, "Synthetic protocol acceptance", None)
    run = await engine.submit(session, "Use the JSON server")
    await engine.actors[session]
    row = (await store.rows("SELECT * FROM requests WHERE run_id=?", (run,)))[0]
    assert row["status"] == "complete" and row["context_epoch"] == 1
    assert json.loads(row["profile_snapshot"])["protocol"] == "chat_completions_json"
    assert json.loads(row["input"]) == sent[0] and sent[0]["stream"] is False
    assert json.loads((await store.rows("SELECT snapshot FROM model_provider_bindings"))[0]["snapshot"])["protocol"] == "chat_completions_json"
    assert replace(profile).snapshot() == profile.snapshot()


async def test_operator_can_register_json_protocol_without_editing_database(config, monkeypatch, capsys):
    from athena.admin import main
    database = Store(config.data_dir / "athena.sqlite3")
    await database.initialize()
    try:
        monkeypatch.setattr("athena.admin.Config.from_env", lambda: config)
        monkeypatch.setattr("sys.argv", ["athena.admin", "register-model", "--id", "json", "--provider", "Local",
                                       "--model", "local", "--base-model", "Local", "--api-base", "http://localhost:9999/v1",
                                       "--protocol", "chat_completions_json"])
        await asyncio.to_thread(main)
        profile = json.loads((await database.rows("SELECT definition FROM model_profiles WHERE id='json'"))[0]["definition"])
        assert profile["protocol"] == "chat_completions_json"
        assert "registered" in capsys.readouterr().out
        with pytest.raises(SystemExit, match="already exists"):
            await asyncio.to_thread(main)
        assert len(await database.rows("SELECT * FROM model_profiles WHERE id='json'")) == 1
    finally:
        await database.close()


@pytest.mark.parametrize("known_service_error", [False, True])
async def test_provider_exception_cannot_persist_credential_or_raw_message(engine, store, known_service_error):
    async def failing(client, endpoint, key, *args, **kwargs):
        raw = "private-adapter-diagnostic=" + key
        if known_service_error:
            raise ServiceError("provider_auth_failed", raw)
        raise RuntimeError(raw)

    engine.model_service.activate(provider("test.redacted", failing))
    session = await store.session()
    run = await engine.submit(session, "Synthetic provider failure")
    await engine.actors[session]
    request = (await store.rows("SELECT * FROM requests WHERE run_id=?", (run,)))[0]
    rows = {"request": request, "runs": await store.rows("SELECT * FROM runs WHERE id=?", (run,)),
            "messages": await store.history(session), "bindings": await store.rows("SELECT * FROM model_provider_bindings")}
    encoded = json.dumps(rows)
    assert "test-provider-key" not in encoded and "private-adapter-diagnostic" not in encoded
    assert request["status"] == "failed"
    assert request["error_code"] == ("provider_auth_failed" if known_service_error else "provider_adapter_failed")
