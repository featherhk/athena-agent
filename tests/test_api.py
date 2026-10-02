import asyncio
import json
import httpx
from athena.api import create_app


def sse(delta, reason=None):
    return 'data: ' + json.dumps({"choices": [{"delta": delta, "finish_reason": reason}]}) + '\n\n'


async def test_api_tool_loop_and_durable_reconnect(config):
    received = []
    def provider(request):
        body = json.loads(request.content)
        received.append(body)
        if len(received) == 1:
            data = sse({"reasoning_content": "need a tool", "tool_calls": [{"index": 0, "id": "call-1", "type": "function", "function": {"name": "exec", "arguments": '{"command":'}}]})
            data += sse({"tool_calls": [{"index": 0, "function": {"arguments": '"printf hello"}'}}]}, "tool_calls")
        else:
            assert body["messages"][-1]["role"] == "tool"
            assert body["messages"][-2]["reasoning_content"] == "need a tool"
            data = sse({"content": "已完成"}, "stop")
        return httpx.Response(200, text=data + 'data: [DONE]\n\n', headers={"content-type": "text/event-stream"})
    app = create_app(config)
    async with app.router.lifespan_context(app):
        await app.state.engine.client.aclose()
        app.state.engine.client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:4317") as client:
            health = (await client.get('/api/health')).json()
            assert health["gil_enabled"] is False and health["model"] == "deepseek-flash"
            assert (await client.get('/api/sessions')).status_code == 401
            registered = await client.post('/api/auth/register', json={"username": "tester", "password": "local-test-password"})
            assert registered.status_code == 200
            session = (await client.post('/api/sessions', json={})).json()["id"]
            result = await client.post(f'/api/sessions/{session}/messages', json={"text": "hello"})
            assert result.status_code == 200
            await asyncio.wait_for(asyncio.gather(*app.state.engine.actors.values()), 3)
            snapshot = (await client.get(f'/api/sessions/{session}')).json()
            assert snapshot["messages"][-1]["content"] == "已完成"
            assert snapshot["runs"][0]["status"] == "complete"
            events = await app.state.store.rows("SELECT * FROM events WHERE session_id=? AND seq>? ORDER BY seq", (session, 0))
            assert events[-1]["seq"] == snapshot["cursor"]
            assert len(received) == 2
            assert all(row["status"] == "complete" for row in await app.state.store.rows("SELECT * FROM requests"))
            assert (await client.get('/api/health', headers={"origin": "https://evil.example"})).status_code == 403
            assert (await client.post(f'/api/sessions/{session}/messages', json={"text": "  "})).status_code == 400
            assert (await client.get(f'/api/sessions/{session}/events', headers={"last-event-id": "oops"})).status_code == 400


async def test_incomplete_provider_stream_cannot_execute_tools(config):
    from athena.model import complete
    partial = sse({"tool_calls": [{"index": 0, "id": "a", "function": {"name": "exec", "arguments": '{}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=partial))) as client:
        import pytest
        with pytest.raises(RuntimeError, match="连接中断"):
            await complete(client, config.api_base, config.api_key, {}, lambda text: asyncio.sleep(0))
