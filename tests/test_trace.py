"""Activity is readable without dumping provider requests or breaking fork scope."""
import asyncio
import json

import httpx
import pytest
from fastapi import HTTPException

from athena.api import create_app
from athena.store import now, uid
from athena.trace import PREVIEW_CHARS, run_trace, trace_artifact, trace_field


def sse(delta, reason=None):
    return "data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": reason}]}) + "\n\n"


async def test_all_streamed_rounds_reasoning_and_tools_remain_readable(config):
    received = []
    def provider(request):
        received.append(json.loads(request.content))
        if len(received) == 1:
            data = sse({"reasoning_content": "先确认"})
            data += sse({"reasoning_content": "→检查设置。", "content": "我先"})
            data += sse({"content": "查看一下。", "tool_calls": [{"index": 0, "id": "inspect", "type": "function", "function": {"name": "runtime", "arguments": '{"op":'}}]})
            data += sse({"tool_calls": [{"index": 0, "function": {"arguments": '"app_settings"}'}}]}, "tool_calls")
        else:
            data = sse({"reasoning_content": "已经获得", "content": "结果"})
            data += sse({"reasoning_content": "实际状态。", "content": "在这里。"}, "stop")
        return httpx.Response(200, text=data + "data: [DONE]\n\n")
    app = create_app(config)
    async with app.router.lifespan_context(app):
        await app.state.engine.client.aclose()
        app.state.engine.client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:4317") as client:
            await client.post("/api/auth/register", json={"username": "trace-owner", "password": "test-password"})
            session = (await client.post("/api/sessions", json={})).json()["id"]
            run = (await client.post(f"/api/sessions/{session}/messages", json={"text": "Check the settings"})).json()["run_id"]
            await asyncio.wait_for(asyncio.gather(*app.state.engine.actors.values()), 5)
            path = f"/api/sessions/{session}/runs/{run}/trace"
            response = await client.get(path)
            assert response.status_code == 200
            trace = response.json()
            assert [entry["role"] for entry in trace["entries"]] == ["user", "assistant", "tool", "assistant"]
            first, tool, last = trace["entries"][1:]
            assert first["reasoning"] == "先确认→检查设置。" and first["content"] == "我先查看一下。"
            assert json.loads(first["tool_calls"])[0]["function"]["arguments"] == '{"op":"app_settings"}'
            assert json.loads(tool["content"])["settings"]["ui_palette"] == "dream"
            assert last["reasoning"] == "已经获得实际状态。" and last["content"] == "结果在这里。"
            assert config.api_key not in response.text
            assert "profile_snapshot" not in response.text and "instruction_role" not in response.text
            snapshot = (await client.get(f"/api/sessions/{session}")).json()
            assert all(message["run_id"] == run for message in snapshot["messages"])
            events = await app.state.store.rows("SELECT payload FROM events WHERE session_id=? AND type='message' ORDER BY seq", (session,))
            assert "先确认" in [json.loads(event["payload"])["reasoning"] for event in events]
            # A new reader can page back to the exact saved provider fields.
            restored = (await client.get(path + f"/{first['id']}?field=reasoning&offset=3&limit=4")).json()
            assert restored["content"] == first["reasoning"][3:7]


async def test_trace_ownership_paging_and_fork_boundary(config):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:4317") as owner:
            user = (await owner.post("/api/auth/register", json={"username": "trace-one", "password": "test-password"})).json()["user"]
            session = await app.state.store.session(user_id=user["id"])
            run = await app.state.store.enqueue(session, "original")
            await app.state.store.claim(session)
            long = "🙂🌸文本" * 600
            message = await app.state.store.message(session, run, "assistant", long)
            await app.state.store.execute("UPDATE messages SET reasoning=? WHERE id=?", (long[::-1], message))
            await app.state.store.execute("UPDATE runs SET status='complete' WHERE id=?", (run,))
            fork = await app.state.store.fork(session)
            late = await app.state.store.message(session, run, "assistant", "after fork")
            path = f"/api/sessions/{session}/runs/{run}/trace"
            first = (await owner.get(path + "?limit=1")).json()
            assert first["has_more"] and len(first["entries"]) == 1
            second = (await owner.get(path + f"?after={first['next_after']}&limit=1")).json()
            assert second["entries"][0]["id"] == message
            assert len(second["entries"][0]["content"]) == PREVIEW_CHARS
            page = await owner.get(path + f"/{message}?offset={PREVIEW_CHARS}&limit=20000")
            assert second["entries"][0]["content"] + page.json()["content"] == long
            for query in ("?limit=51", "?after=-1"):
                assert (await owner.get(path + query)).status_code == 422
            fork_path = f"/api/sessions/{fork}/runs/{run}/trace"
            inherited = (await owner.get(fork_path)).json()
            assert [item["id"] for item in inherited["entries"]] == [first["entries"][0]["id"], message]
            assert (await owner.get(fork_path + f"/{late}")).status_code == 404
            other_session = await app.state.store.session(user_id=user["id"])
            assert (await owner.get(f"/api/sessions/{other_session}/runs/{run}/trace")).status_code == 404
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:4317") as other:
                await other.post("/api/auth/register", json={"username": "trace-two", "password": "test-password"})
                assert (await other.get(path)).status_code == 404
                assert (await other.get(path + f"/{message}")).status_code == 404
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:4317") as anonymous:
                assert (await anonymous.get(path)).status_code == 401


async def test_trace_reads_original_tool_artifact_without_large_response(store, config):
    session = await store.session()
    run = await store.enqueue(session, "Read a long output")
    await store.claim(session)
    original = "数据🙂\n" * 30000
    artifact_id = uid()
    filename = artifact_id + ".txt"
    (config.data_dir / "artifacts" / filename).write_text(original)
    await store.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?)", (artifact_id, session, run, filename, len(original.encode()), now()))
    message = await store.message(session, run, "tool", json.dumps({"artifact_id": artifact_id, "output": "clipped"}), tool_call_id="one")
    trace = await run_trace(store, session, run)
    assert trace["entries"][-1]["artifact_id"] == artifact_id
    collected, offset = [], 0
    while True:
        page = await trace_artifact(store, config, session, run, message, offset=offset, limit=11003)
        assert len(page["content"]) <= 11003
        collected.append(page["content"])
        offset += len(page["content"])
        if offset >= page["total_chars"]:
            break
    assert "".join(collected) == original
    assert (await trace_field(store, session, run, message, "content"))["content"] == json.dumps({"artifact_id": artifact_id, "output": "clipped"})


async def test_trace_artifact_rejects_forged_link_and_symlinks(store, config, tmp_path):
    session = await store.session()
    run = await store.enqueue(session, "one")
    await store.claim(session)
    second = await store.session()
    other = await store.enqueue(second, "two")
    other_artifact = uid()
    await store.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?)", (other_artifact, second, other, "not-read.txt", 10, now()))
    message = await store.message(session, run, "tool", json.dumps({"artifact_id": other_artifact}), tool_call_id="one")
    with pytest.raises(HTTPException) as error:
        await trace_artifact(store, config, session, run, message)
    assert error.value.status_code == 404
    target = tmp_path / "private.txt"
    target.write_text("do not follow")
    (config.data_dir / "artifacts" / "link.txt").symlink_to(target)
    await store.execute("UPDATE artifacts SET session_id=?,run_id=?,path='link.txt' WHERE id=?", (session, run, other_artifact))
    with pytest.raises(HTTPException):
        await trace_artifact(store, config, session, run, message)
    await store.execute("UPDATE artifacts SET path='../private.txt' WHERE id=?", (other_artifact,))
    with pytest.raises(HTTPException):
        await trace_artifact(store, config, session, run, message)


async def test_interrupted_stream_keeps_partial_reasoning_and_text(config):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        await app.state.engine.client.aclose()
        data = sse({"reasoning_content": "partial thought", "content": "partial reply"})
        app.state.engine.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=data)))
        session = await app.state.store.session()
        run = await app.state.engine.submit(session, "hello")
        await asyncio.wait_for(asyncio.gather(*app.state.engine.actors.values()), 5)
        trace = await run_trace(app.state.store, session, run)
        assert trace["run"]["status"] == "failed" and trace["run"]["error_code"] == "stream_interrupted"
        assert trace["entries"][-1]["content"] == "partial reply"
        assert trace["entries"][-1]["reasoning"] == "partial thought"
        assert trace["entries"][-1]["status"] == "failed"


async def test_tool_only_round_remains_a_snapshot_trace_anchor(config):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:4317") as client:
            user = (await client.post("/api/auth/register", json={"username": "tool-only-owner", "password": "test-password"})).json()["user"]
            session = await app.state.store.session(user_id=user["id"])
            run = await app.state.store.enqueue(session, "Run a tool")
            await app.state.store.claim(session)
            message = await app.state.store.message(session, run, "assistant", tool_calls=[{"id": "one", "type": "function", "function": {"name": "runtime", "arguments": '{"op":"state"}'}}])
            await app.state.store.message(session, run, "tool", '{"error":"test failure"}', tool_call_id="one")
            await app.state.store.execute("UPDATE runs SET status='failed',error_code='test_failure' WHERE id=?", (run,))
            snapshot = (await client.get(f"/api/sessions/{session}")).json()
            assert message in [item["id"] for item in snapshot["messages"]]
            trace = (await client.get(f"/api/sessions/{session}/runs/{run}/trace")).json()
            assert trace["run"]["error_code"] == "test_failure"
            assert trace["entries"][-1]["content"] == '{"error":"test failure"}'
