import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
import sys
import threading
import pytest
from athena.config import Config, require_free_threading
from athena.store import Store, now, uid
from athena.tools import ToolRunner


def test_runtime_is_free_threaded_after_imports():
    require_free_threading()
    assert not sys._is_gil_enabled()


def test_data_cannot_live_in_checkout(tmp_path):
    with pytest.raises(ValueError, match="outside"):
        Config(tmp_path / "repo/data", tmp_path / "repo").prepare()


async def test_database_owner_and_atomic_claim(store):
    owners = await asyncio.gather(*(store.call(lambda db: threading.get_ident()) for _ in range(40)))
    assert len(set(owners)) == 1 and owners[0] != threading.get_ident()
    session = await store.session()
    await asyncio.gather(*(store.enqueue(session, str(i)) for i in range(20)))
    claims = await asyncio.gather(*(store.claim(session) for _ in range(20)))
    assert sum(item is not None for item in claims) == 1
    assert len(await store.rows("SELECT * FROM runs WHERE status='queued'")) == 19


async def test_fork_is_snapshot_and_pauses_tasks(store, config):
    session = await store.session()
    run = await store.enqueue(session, "original")
    await store.claim(session)
    runner = ToolRunner(config, store)
    output = await runner.shell({"command": "printf 'original artifact'"}, session, run)
    await store.execute("UPDATE runs SET status='complete' WHERE id=?", (run,))
    await runner.component({"op": "upsert", "id": "card", "html": "old"}, session)
    await runner.runtime({"op": "schedule", "title": "reminder", "prompt": "hello", "due_at": now()}, session, run)
    fork = await store.fork(session)
    await store.message(session, run, "assistant", "after fork")
    await runner.component({"op": "upsert", "id": "card", "html": "new"}, session)
    assert [m["content"] for m in await store.history(fork)] == ["original"]
    assert (await store.components(fork))[0]["html"] == "old"
    assert (await store.rows("SELECT status FROM tasks WHERE session_id=?", (fork,)))[0]["status"] == "paused"
    restored = await runner.runtime({"op": "read_artifact", "artifact_id": output["artifact_id"]}, fork, None)
    assert restored["content"] == "original artifact"
    unrelated = await store.session()
    assert await store.artifact(unrelated, output["artifact_id"]) is None


async def test_active_fork_rejected(store):
    session = await store.session()
    await store.enqueue(session, "pending")
    with pytest.raises(ValueError, match="停下"):
        await store.fork(session)


async def test_tool_result_idempotent_on_cancellation_boundary(store):
    session = await store.session()
    run = await store.enqueue(session, "go")
    first = await store.message(session, run, "tool", "executed", tool_call_id="call-1")
    second = await store.message(session, run, "tool", "interrupted", tool_call_id="call-1")
    assert first == second
    assert (await store.rows("SELECT content FROM messages WHERE tool_call_id='call-1'"))[0]["content"] == "executed"


async def test_restart_closes_missing_tool_result_and_never_replays(store):
    session = await store.session()
    run = await store.enqueue(session, "go")
    await store.claim(session)
    await store.message(session, run, "assistant", tool_calls=[{"id": "call-1", "type": "function", "function": {"name": "exec", "arguments": "{}"}}])
    await store.message(session, run, "assistant", "partial", status="streaming")
    await store.initialize()
    await store.initialize()
    rows = await store.history(session)
    assert rows[-1]["role"] == "tool"
    assert "unknown_after_restart" in rows[-1]["content"]
    assert len([row for row in rows if row["tool_call_id"] == "call-1"]) == 1
    assert (await store.rows("SELECT status FROM runs"))[0]["status"] == "interrupted"
    assert await store.claim(session) is None


async def test_compaction_is_recoverable_and_stable(engine, store):
    session = await store.session()
    run = await store.enqueue(session, "keep this")
    await store.claim(session)
    original = "a" * 210000
    await store.message(session, run, 'assistant', '', tool_calls=[{'id': 'a', 'type': 'function', 'function': {'name': 'exec', 'arguments': '{}'}}])
    message = await store.message(session, run, "tool", original, tool_call_id="a")
    await store.message(session, run, 'assistant', '', tool_calls=[{'id': 'b', 'type': 'function', 'function': {'name': 'exec', 'arguments': '{}'}}])
    await store.message(session, run, 'tool', 'The new observation', tool_call_id='b')
    context = await engine.context(session)
    clipped = next(item for item in context if item.get('tool_call_id') == 'a')
    assert len(clipped['content']) < 20000
    assert message in clipped['content']
    assert context[-1]['content'] == 'The new observation'
    assert next(item for item in await store.history(session) if item['id'] == message)['content'] == original
    assert await engine.context(session) == context
    await store.message(session, run, "user", "next")
    assert (await engine.context(session))[:-1] == context
    assert len(await store.rows("SELECT * FROM compactions")) == 1
    page = await engine.tools.runtime({"op": "read_message", "message_id": message, "offset": 200000, "limit": 10000}, session, run)
    assert len(page["content"]) == 10000


async def test_command_clipping_and_timeout(engine, store):
    session = await store.session()
    result = await engine.tools.shell({"command": "printf '%020000d' 0"}, session, None)
    assert len(result["output"]) < 13000
    restored = await engine.tools.runtime({"op": "read_artifact", "artifact_id": result["artifact_id"], "limit": 20000}, session, None)
    assert len(restored["content"]) == 20000
    result = await engine.tools.shell({"command": "sleep 20", "timeout_seconds": 1}, session, None)
    assert result["error"] and result["exit_code"] != 0


async def test_timers_deduplicate_and_condition_checks_do_not_call_model(engine, store, monkeypatch):
    session = await store.session()
    woken = []
    monkeypatch.setattr(engine, "kick", woken.append)
    await engine.tools.runtime({"op": "schedule", "title": "timer", "prompt": "do it", "due_at": now()}, session, None)
    await engine.tick()
    await engine.tick()
    assert len(await store.rows("SELECT * FROM wakes")) == 1
    conditional = await engine.tools.runtime({"op": "schedule", "title": "watch", "prompt": "do next", "due_at": now(), "interval_seconds": 60, "condition_command": "printf '{\"trigger\":false}'"}, session, None)
    await engine.tick()
    await asyncio.gather(*engine.watchers.values())
    assert len(await store.rows("SELECT * FROM wakes")) == 1
    assert (await store.rows("SELECT status FROM task_checks"))[0]["status"] == "unchanged"
    await store.execute("UPDATE tasks SET due_at=?,condition_command=? WHERE id=?", (now(), "printf '{\"trigger\":true,\"context\":\"changed\"}'", conditional["task_id"]))
    await engine.tick()
    await asyncio.gather(*engine.watchers.values())
    await engine.tick()
    assert len(await store.rows("SELECT * FROM wakes")) == 2
    assert (await store.rows("SELECT status FROM tasks WHERE id=?", (conditional["task_id"],)))[0]["status"] == "done"
    assert await store.rows("SELECT * FROM requests") == []


async def test_interrupt_preserves_partial_and_processes_new_input(engine, store, monkeypatch):
    started = asyncio.Event()
    count = 0
    async def provider(client, base, key, payload, on_text, on_reasoning=None):
        nonlocal count
        count += 1
        if count == 1:
            await on_text("已经完成一半")
            started.set()
            await asyncio.Event().wait()
        await on_text("新的事情完成了")
        return {"message": {"role": "assistant", "content": "新的事情完成了", "reasoning_content": "reason"}, "usage": {}}
    monkeypatch.setattr("athena.engine.complete", provider)
    session = await store.session()
    first = await engine.submit(session, "long")
    await asyncio.wait_for(started.wait(), 3)
    queued = await engine.submit(session, "queued")
    second = await engine.submit(session, "urgent", "interrupt")
    await asyncio.wait_for(asyncio.gather(*engine.actors.values()), 3)
    statuses = {row["id"]: row["status"] for row in await store.rows("SELECT * FROM runs")}
    assert statuses == {first: "interrupted", queued: "cancelled", second: "complete"}
    assert any(row["content"] == "已经完成一半" and row["status"] == "interrupted" for row in await store.history(session))
    context = await engine.context(session)
    assert all(row["content"] != "queued" for row in context)
    assert context[-1]["reasoning_content"] == "reason"
