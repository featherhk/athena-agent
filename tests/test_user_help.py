"""Requirement tests for asking a person without holding/replaying an agent run."""
import asyncio
from dataclasses import replace
import json

import pytest

from athena.auth import Auth
from athena.engine import Engine
from athena.errors import UserError
from athena.store import Store
from athena.user_help import activate_help, answer_help, list_help, request_help


QUESTIONS = [{"id": "city", "question": "Which city's weather should I show?", "options": [{"label": "Shanghai"}, {"label": "Tokyo", "description": "Japan"}]}]


async def waiting(store, user_id=None):
    session = await store.session(user_id=user_id)
    run = await store.enqueue(session, "Show my weather")
    await store.claim(session)
    request = await request_help(store, session, run, {"questions": QUESTIONS})
    await activate_help(store, session, run, request["request"]["id"], [])
    return session, run, request["request"]["id"]


async def drain(engine):
    await asyncio.wait_for(asyncio.gather(*engine.actors.values()), 5)


def install_provider(monkeypatch, engine):
    calls, executed = [], []
    async def provider(client, base, key, payload, on_text, on_reasoning=None):
        calls.append(payload)
        if len(calls) == 1:
            return {"message": {"role": "assistant", "content": "", "reasoning_content": "Need the user's city.", "tool_calls": [
                {"id": "done", "type": "function", "function": {"name": "already_done", "arguments": "{}"}},
                {"id": "question", "type": "function", "function": {"name": "runtime", "arguments": json.dumps({"op": "user_help", "args": {"questions": QUESTIONS}})}},
                {"id": "later", "type": "function", "function": {"name": "must_not_run_yet", "arguments": "{}"}},
            ]}, "usage": {}}
        return {"message": {"role": "assistant", "content": "Weather for the city you selected.", "reasoning_content": ""}, "usage": {}}
    async def execute(name, args, session_id, run_id, bindings=None):
        executed.append(name)
        if name == "runtime":
            return await request_help(engine.store, session_id, run_id, args["args"])
        return {"effect": "completed once"}
    monkeypatch.setattr("athena.engine.complete", provider)
    monkeypatch.setattr(engine.tools, "execute", execute)
    return calls, executed


async def test_help_releases_actor_preserves_pairing_and_resumes_once(engine, store, monkeypatch):
    calls, executed = install_provider(monkeypatch, engine)
    session = await store.session()
    run = await engine.submit(session, "Show weather")
    await drain(engine)
    assert not engine.active_runs and len(calls) == 1
    assert (await store.rows("SELECT status,finished_at FROM runs WHERE id=?", (run,)))[0] == {"status": "waiting_user", "finished_at": None}
    pending = await list_help(store, session, None)
    assert len(pending) == 1
    context = await engine.context(session)
    assistant_index = next(i for i, item in enumerate(context) if item.get("tool_calls"))
    assert [item["tool_call_id"] for item in context[assistant_index + 1:]] == ["done", "question", "later"]
    assert json.loads(context[-1]["content"])["status"] == "not_executed"
    assert executed == ["already_done", "runtime"]
    await store.execute("INSERT INTO memories(id,scope,owner_id,content,source_run_id,created_at) VALUES ('later-memory','global','global','Memory changed while waiting',?,datetime('now'))", (run,))
    await engine.answer_help(session, None, pending[0]["id"], {"city": "Kyoto"})
    await drain(engine)
    assert len(calls) == 2 and executed == ["already_done", "runtime"]
    assert calls[1]["messages"][:-1] == context
    assert "Kyoto" in calls[1]["messages"][-1]["content"]
    assert (await store.rows("SELECT status FROM runs WHERE id=?", (run,)))[0]["status"] == "complete"
    assert not await list_help(store, session, None)
    await engine.answer_help(session, None, pending[0]["id"], {"city": "Kyoto"})
    assert len(calls) == 2


async def test_waiting_request_survives_restart_without_model_or_action_replay(config, monkeypatch):
    path = config.data_dir / "help-restart.sqlite3"
    store = Store(path)
    await store.initialize()
    engine = Engine(config, store)
    calls, executed = install_provider(monkeypatch, engine)
    session = await store.session()
    run = await engine.submit(session, "Show weather")
    await drain(engine)
    request_id = (await list_help(store, session, None))[0]["id"]
    await engine.close()
    await store.close()
    restored = Store(path)
    await restored.initialize()
    resumed = Engine(config, restored)
    try:
        assert (await list_help(restored, session, None))[0]["id"] == request_id
        assert (await restored.rows("SELECT status FROM runs WHERE id=?", (run,)))[0]["status"] == "waiting_user"
        assert await restored.claim(session) is None
        await resumed.tick()
        assert not resumed.actors and len(calls) == 1
        await resumed.answer_help(session, None, request_id, {"city": "Shanghai"})
        await drain(resumed)
        assert len(calls) == 2 and executed == ["already_done", "runtime"]
    finally:
        await resumed.close()
        await restored.close()


async def test_answers_are_owned_validated_and_concurrently_idempotent(engine, store, monkeypatch):
    user, _ = await Auth(store).register("help-owner", "test-password")
    session, run, request_id = await waiting(store, user["id"])
    kicked = []
    monkeypatch.setattr(engine, "kick", kicked.append)
    with pytest.raises(UserError) as error:
        await engine.answer_help(session, "someone-else", request_id, {"city": "Tokyo"})
    assert error.value.code == "not_found"
    with pytest.raises(UserError):
        await list_help(store, session, "someone-else")
    with pytest.raises(UserError) as error:
        await engine.answer_help(session, user["id"], request_id, {"wrong-id": "Tokyo"})
    assert error.value.code == "invalid_answer"
    answers = await asyncio.gather(*(engine.answer_help(session, user["id"], request_id, {"city": "Tokyo"}) for _ in range(20)))
    assert all(answer["status"] == "answered" for answer in answers)
    assert kicked == [session]
    assert len(await store.rows("SELECT * FROM messages WHERE run_id=? AND role='user'", (run,))) == 2
    with pytest.raises(UserError) as error:
        await engine.answer_help(session, user["id"], request_id, {"city": "Shanghai"})
    assert error.value.code == "help_resolved"


async def test_queue_waits_until_answer_and_stop_cancels_help_without_fork_copy(engine, store):
    session, run, request_id = await waiting(store)
    queued = await store.enqueue(session, "An unrelated later task")
    assert await store.claim(session) is None
    with pytest.raises(UserError) as error:
        await store.fork(session)
    assert error.value.code == "fork_busy"
    await engine.stop(session)
    assert not await list_help(store, session, None)
    assert {row["status"] for row in await store.rows("SELECT * FROM runs WHERE id IN (?,?)", (run, queued))} == {"cancelled"}
    with pytest.raises(UserError) as error:
        await engine.answer_help(session, None, request_id, {"city": "Tokyo"})
    assert error.value.code == "help_resolved"
    fork = await store.fork(session)
    assert not await list_help(store, fork, None)


async def test_reset_cancels_waiting_question(engine, store):
    user, _ = await Auth(store).register("reset-help", "test-password")
    session, run, request_id = await waiting(store, user["id"])
    await engine.reset_user(user["id"])
    assert not await list_help(store, session, user["id"])
    assert (await store.rows("SELECT status FROM user_help WHERE id=?", (request_id,)))[0]["status"] == "cancelled"
    assert (await store.rows("SELECT status FROM runs WHERE id=?", (run,)))[0]["status"] == "cancelled"


async def test_answer_stop_race_never_strands_pending_help(engine, store, monkeypatch):
    session, run, request_id = await waiting(store)
    monkeypatch.setattr(engine, "kick", lambda session_id: None)
    results = await asyncio.gather(engine.answer_help(session, None, request_id, {"city": "Tokyo"}), engine.stop(session), return_exceptions=True)
    assert not any(isinstance(result, BaseException) for result in results)
    assert not await list_help(store, session, None)
    assert (await store.rows("SELECT status FROM runs WHERE id=?", (run,)))[0]["status"] == "cancelled"


async def test_interrupted_question_preparation_is_not_resurrected(store):
    session = await store.session()
    run = await store.enqueue(session, "Need help")
    await store.claim(session)
    request = await request_help(store, session, run, {"questions": QUESTIONS})
    assert not await list_help(store, session, None)
    await store.initialize()
    assert (await store.rows("SELECT status FROM user_help WHERE id=?", (request["request"]["id"],)))[0]["status"] == "cancelled"
    assert (await store.rows("SELECT status FROM runs WHERE id=?", (run,)))[0]["status"] == "interrupted"


async def test_answer_attachments_are_preserved_and_duplicate_checks_use_ids(store):
    session, run, request_id = await waiting(store)
    attachment = {"id": "file-id", "name": "weather.csv", "workspace_path": "uploads/weather.csv"}
    answer, changed = await answer_help(store, session, None, request_id, {"city": "See the attached file"}, [attachment])
    assert changed and answer["attachments"] == [attachment]
    history = await store.history(session)
    assert json.loads(history[-1]["attachments"]) == [attachment]
    _, changed = await answer_help(store, session, None, request_id, {"city": "See the attached file"}, [{**attachment, "name": "renamed.csv"}])
    assert not changed
    with pytest.raises(UserError):
        await answer_help(store, session, None, request_id, {"city": "See the attached file"}, [])


async def test_single_question_accepts_file_only_help_reply(store):
    session, run, request_id = await waiting(store)
    attachment = {"id": "file-id", "name": "cities.csv", "workspace_path": "uploads/cities.csv"}
    answer, changed = await answer_help(store, session, None, request_id, {"city": ""}, [attachment])
    assert changed and answer["answers"] == {"city": ""}
    assert (await store.history(session))[-1]["content"] == ""


async def test_legacy_receipts_migrate_without_rewriting_model_context(store, engine):
    session, run, first = await waiting(store)
    await answer_help(store, session, None, first, {"city": "Tokyo"})
    await store.claim(session)
    second = (await request_help(store, session, run, {"questions": QUESTIONS}))["request"]["id"]
    await activate_help(store, session, run, second, [])
    await answer_help(store, session, None, second, {"city": "Tokyo"})
    await store.execute("UPDATE runs SET status='complete' WHERE id=?", (run,))
    await store.execute("UPDATE messages SET help_reply=NULL WHERE session_id=?", (session,))
    before = await engine.context(session)
    prefix = await store.rows("SELECT * FROM context_prefixes WHERE session_id=?", (session,))
    await store.execute("PRAGMA user_version=6")
    await store.initialize()
    receipts = [json.loads(row['help_reply']) for row in await store.history(session) if row['help_reply']]
    assert [item['id'] for item in receipts] == [first, second]
    assert await engine.context(session) == before
    assert await store.rows("SELECT * FROM context_prefixes WHERE session_id=?", (session,)) == prefix
    await store.initialize()
    assert len([row for row in await store.history(session) if row['help_reply']]) == 2


@pytest.mark.parametrize("mismatch", ["ambiguous", "attachments"])
async def test_legacy_receipts_leave_uncertain_matches_untouched(store, mismatch):
    session, run, request_id = await waiting(store)
    await answer_help(store, session, None, request_id, {"city": "Tokyo"})
    await store.execute("UPDATE runs SET status='complete' WHERE id=?", (run,))
    await store.execute("UPDATE messages SET help_reply=NULL WHERE session_id=?", (session,))
    if mismatch == 'ambiguous':
        await store.call(lambda db: Store._message(db, session, run, 'user', 'Tokyo', 'complete'))
    else:
        await store.execute("UPDATE user_help SET attachments='[{\"id\":\"missing-file\"}]' WHERE id=?", (request_id,))
    await store.execute("PRAGMA user_version=6")
    await store.initialize()
    assert not any(row['help_reply'] for row in await store.history(session))


async def test_user_answer_does_not_reset_total_model_step_budget(config, store, monkeypatch):
    engine = Engine(replace(config, max_steps=1), store)
    calls, _ = install_provider(monkeypatch, engine)
    try:
        session = await store.session()
        run = await engine.submit(session, "Show weather")
        await drain(engine)
        request = (await list_help(store, session, None))[0]
        await engine.answer_help(session, None, request["id"], {"city": "Tokyo"})
        await drain(engine)
        assert len(calls) == 1
        assert (await store.rows("SELECT status,error_code FROM runs WHERE id=?", (run,)))[0] == {"status": "failed", "error_code": "step_limit"}
    finally:
        await engine.close()


@pytest.mark.parametrize("questions", [[], QUESTIONS * 2, [{"id": "x", "question": ""}], [{"id": "x", "question": "Why?", "options": [{"label": "Yes"}, {"label": "Yes"}]}]])
async def test_invalid_questions_have_no_durable_effect(store, questions):
    session = await store.session()
    run = await store.enqueue(session, "Need help")
    await store.claim(session)
    with pytest.raises(ValueError):
        await request_help(store, session, run, {"questions": questions})
    assert not await store.rows("SELECT * FROM user_help")
