from datetime import datetime, timedelta, timezone
import json
import sqlite3
import pytest
from athena.introspection import Introspection
from athena.profiles import Profile
from athena.store import now, uid


async def account(store, identifier):
    await store.execute("INSERT INTO users(id,username,password_hash,role,created_at) VALUES (?,?,?,'user',?)",
                        (identifier, identifier, "unused-test-hash", now()))
    return identifier


async def request(store, session, profile, usage, seconds_ago=10, payload=None, run=None):
    if run is None:
        run = await store.enqueue(session, "Synthetic request")
        await store.execute("UPDATE runs SET status='complete' WHERE id=?", (run,))
        await store.execute("UPDATE messages SET status='complete' WHERE run_id=?", (run,))
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()
    identifier = uid()
    encoded = json.dumps(payload or profile.payload([{"role": "user", "content": "A synthetic request"}], []), ensure_ascii=False)
    await store.execute(
        "INSERT INTO requests(id,run_id,model,input,status,usage,created_at,finished_at,profile_snapshot,context_epoch) "
        "VALUES (?,?,?,?,?,?,?,?,?,0)",
        (identifier, run, profile.model, encoded, "complete" if usage else "interrupted", json.dumps(usage) if usage is not None else None,
         stamp, stamp, json.dumps(profile.snapshot())),
    )
    return identifier


async def long_context(engine, store, user_text="Keep the result", tool_length=90000, schema_length=1000):
    session = await store.session()
    run = await store.enqueue(session, user_text)
    await store.claim(session)
    assistant = await store.message(session, run, "assistant", "I will inspect it", tool_calls=[
        {"id": "original-call", "type": "function", "function": {"name": "exec", "arguments": '{"command":"synthetic"}'}}
    ])
    await store.execute("UPDATE messages SET reasoning='Preserve this reasoning' WHERE id=?", (assistant,))
    original = "A" * (tool_length // 2) + "Z" * (tool_length - tool_length // 2)
    message = await store.message(session, run, "tool", original, tool_call_id="original-call")
    profile, _ = await engine.models.selected(session)
    tools = [{"type": "function", "function": {"name": "exec", "description": "X" * schema_length,
              "parameters": {"type": "object", "properties": {}}}}]
    payload = profile.payload(await engine.context(session), tools)
    actual_tokens = max(1, len(json.dumps(payload, ensure_ascii=False)) // 4)
    await request(store, session, profile, {"prompt_tokens": actual_tokens, "completion_tokens": 10}, payload=payload, run=run)
    await store.execute("UPDATE runs SET status='complete' WHERE id=?", (run,))
    return session, message, original


async def test_usage_filters_owned_requests_by_time_and_keeps_unknown_cost_unknown(engine, store):
    first = await account(store, "first")
    second = await account(store, "second")
    session = await store.session(user_id=first)
    same_owner = await store.session(user_id=first)
    foreign = await store.session(user_id=second)
    profile, _ = await engine.models.selected(session)
    usage = {"prompt_tokens": 1000, "completion_tokens": 100, "prompt_cache_hit_tokens": 500, "prompt_cache_miss_tokens": 500}
    measured_id = await request(store, session, profile, usage)
    await request(store, session, profile, usage, seconds_ago=3700)
    await request(store, session, profile, None, seconds_ago=5)
    await request(store, same_owner, profile, {"prompt_tokens": 2000, "completion_tokens": 200})
    await request(store, foreign, profile, usage)
    inspector = Introspection(store, engine.models)
    own = await inspector.usage(session)
    assert own["total"]["request_count"] == 2
    assert own["total"]["input_tokens"] == 1000
    assert own["total"]["unreported_requests"] == own["total"]["unpriced_requests"] == 1
    assert own["total"]["cache_rate"] == .5
    assert own["total"]["cost_usd_low"] == pytest.approx((500 * .003 + 500 * .15 + 100 * .6) / 1_000_000)
    assert own["total"]["cost_usd_high"] == own["total"]["cost_usd_low"] * 2
    assert own["cost_measurement"] == "estimated_not_billed"
    assert own["last_context_from_request"]["request_id"] == measured_id
    assert own["last_context_from_request"]["actual_prompt_tokens"] == 1000
    all_owned = await inspector.usage(session, scope="account")
    assert all_owned["total"]["request_count"] == 3 and all_owned["total"]["input_tokens"] == 3000
    assert all_owned["total"]["unpriced_requests"] == 2
    assert all_owned["window"]["time_basis"] == "request_started_at"
    assert (await inspector.usage(session, window_seconds=1))["total"]["cost_usd_low"] is None


async def test_context_status_does_not_invent_tokenizer_or_model_window(store):
    session = await store.session()
    profile = Profile(id="default", provider="Example", model="example", base_model="Example",
                      api_base="https://example.invalid", context_chars=180000)
    class Models:
        async def selected(self, session_id):
            return profile, 0
    result = await Introspection(store, Models()).context_status(session)
    assert result["measurement"] == "estimated"
    assert result["estimate_basis"] == "one_token_per_character_heuristic"
    assert result["estimated_input_tokens"] == result["estimated_payload_chars"]
    assert result["raw_chars"] == result["clipped_chars"] == 0
    assert result["estimated_tool_schema_chars"] is None
    assert result["context_chars_limit"] == 180000 and result["limit_kind"] == "application_character_guard"
    assert result["last_context_from_request"]["actual_prompt_tokens"] is None
    assert result["last_context_from_request"]["context_window_tokens"] is None


async def test_compact_preserves_originals_pairing_reasoning_and_can_shrink_existing_clips(engine, store):
    session, message, original = await long_context(engine, store)
    inspector = Introspection(store, engine.models)
    before = await store.history(session)
    status = await inspector.context_status(session)
    assert status["estimate_basis"] == "last_reported_request_ratio"
    assert status["estimated_tool_schema_chars"] > 1000
    first = await inspector.compact(session, target_tokens=20000)
    assert first["measurement"] == "estimated" and first["target_met"] is True
    assert first["after"]["estimated_tokens"] <= 20000 < first["before"]["estimated_tokens"]
    assert first["changed_tool_messages"] == [message]
    first_clip = (await store.rows("SELECT content FROM context_clips WHERE session_id=?", (session,)))[0]["content"]
    second = await inspector.compact(session, target_tokens=5000)
    second_clip = (await store.rows("SELECT content FROM context_clips WHERE session_id=?", (session,)))[0]["content"]
    assert second["target_met"] is True and second["after"]["estimated_tokens"] <= 5000
    assert len(second_clip) < len(first_clip) and message in second_clip
    assert second_clip.startswith("A") and second_clip.endswith("Z")
    assert await store.history(session) == before
    assert (await store.history(session))[-1]["content"] == original
    projected = await engine.context(session)
    assert projected[-1]["content"] == second_clip
    assert projected[-1]["tool_call_id"] == "original-call"
    assert projected[-2]["tool_calls"][0]["id"] == "original-call"
    assert projected[-2]["reasoning_content"] == "Preserve this reasoning"
    assert len(await store.rows("SELECT * FROM compactions WHERE session_id=?", (session,))) == 2
    again = await inspector.compact(session, target_tokens=5000)
    assert again["compaction_id"] is None and again["changed_tool_messages"] == []


async def test_unclippable_content_and_schema_prevent_false_target_success(engine, store):
    session, _, _ = await long_context(engine, store, user_text="Keep " * 4000, schema_length=12000)
    inspector = Introspection(store, engine.models)
    originals = await store.history(session)
    result = await inspector.compact(session, target_tokens=1000)
    assert result["target_met"] is False and result["after"]["estimated_tokens"] > 1000
    assert result["estimated_tool_schema_chars"] > 12000
    assert result["remaining_limitations"]
    assert await store.history(session) == originals
    assert (await inspector.context_status(session))["raw_chars"] > (await inspector.context_status(session))["clipped_chars"]


async def test_unknown_tool_overhead_cannot_confirm_target(engine, store):
    session = await store.session()
    await store.message(session, None, "tool", "x" * 30000, tool_call_id="synthetic-call")
    result = await Introspection(store, engine.models).compact(session, target_tokens=5000)
    assert result["after"]["estimated_tokens"] <= 5000
    assert result["target_met"] is False and result["tool_schema_source"] == "unknown"
    assert result["estimated_tool_schema_chars"] is None


async def test_fork_compaction_is_independent_and_history_is_recoverable(engine, store):
    parent, message, original = await long_context(engine, store)
    inspector = Introspection(store, engine.models)
    await inspector.compact(parent, target_tokens=20000)
    fork = await store.fork(parent)
    parent_clips = await store.rows("SELECT * FROM context_clips WHERE session_id=?", (parent,))
    await inspector.compact(fork, target_tokens=5000)
    assert await store.rows("SELECT * FROM context_clips WHERE session_id=?", (parent,)) == parent_clips
    child_clip = (await store.rows("SELECT content FROM context_clips WHERE session_id=? AND message_id=?", (fork, message)))[0]["content"]
    assert len(child_clip) < len(parent_clips[0]["content"])
    assert (await store.history(fork))[-1]["content"] == original


async def test_compaction_marker_failure_rolls_back_all_clips(engine, store):
    session, _, _ = await long_context(engine, store)
    await store.execute("CREATE TRIGGER reject_compaction BEFORE INSERT ON compactions BEGIN SELECT RAISE(ABORT,'test marker failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="marker failure"):
        await Introspection(store, engine.models).compact(session, target_tokens=5000)
    assert await store.rows("SELECT * FROM context_clips WHERE session_id=?", (session,)) == []
    assert await store.rows("SELECT * FROM compactions WHERE session_id=?", (session,)) == []


async def test_compaction_strategy_can_replace_the_plan_without_changing_originals(engine, store):
    session, message, original = await long_context(engine, store)
    inspector = Introspection(store, engine.models)
    plan = await inspector.compaction_plan(session, target_tokens=5000)
    assert message in plan and f"message_id={message}" in plan[message]
    assert await store.rows("SELECT * FROM context_clips WHERE session_id=?", (session,)) == []
    replacement = f"Strategy-selected excerpt. Recover original: message_id={message}"
    result = await inspector.compact(session, target_tokens=5000, plan_override={message: replacement})
    assert result["target_met"] is True
    assert (await store.rows("SELECT content FROM context_clips WHERE session_id=?", (session,)))[0]["content"] == replacement
    assert (await store.history(session))[-1]["content"] == original


async def test_invalid_compaction_strategy_output_is_rejected_atomically(engine, store):
    session, message, original = await long_context(engine, store)
    inspector = Introspection(store, engine.models)
    user_message = (await store.history(session))[0]["id"]
    valid = f"Recover original: message_id={message}"
    bad_plans = [
        {message: valid, user_message: f"message_id={user_message}"},
        {message: "No recovery marker"},
        {message: original + f"message_id={message}"},
        {message: None},
        [],
    ]
    for plan in bad_plans:
        with pytest.raises(ValueError):
            await inspector.compact(session, target_tokens=5000, plan_override=plan)
        assert await store.rows("SELECT * FROM context_clips WHERE session_id=?", (session,)) == []
        assert await store.rows("SELECT * FROM compactions WHERE session_id=?", (session,)) == []


@pytest.mark.parametrize("target", [True, False, None, 999, 1000001, 20000.0, "20000"])
async def test_compaction_target_requires_a_strict_integer(engine, store, target):
    with pytest.raises(ValueError, match="target_tokens"):
        await Introspection(store, engine.models).compact("not-looked-up", target_tokens=target)


async def test_usage_rejects_invalid_scope_window_and_legacy_account_scope(engine, store):
    inspector = Introspection(store, engine.models)
    for window in (True, 0, -1, 3.0, "3600"):
        with pytest.raises(ValueError, match="window_seconds"):
            await inspector.usage("not-looked-up", window_seconds=window)
    with pytest.raises(ValueError, match="scope"):
        await inspector.usage("not-looked-up", scope="another-account")
    session = await store.session()
    with pytest.raises(ValueError, match="account"):
        await inspector.usage(session, scope="account")
