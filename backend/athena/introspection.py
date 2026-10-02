"""Provider usage and recoverable context projections, never invented token counts."""
from datetime import datetime, timedelta, timezone
import json
import math
from .metrics import summarize
from .prompt import system_prompt
from .store import now, uid
from .context_projection import project_messages


def _json_chars(value):
    return len(json.dumps(value, ensure_ascii=False))


def _prompt_tokens(request):
    usage = json.loads(request["usage"]) if request and request.get("usage") else {}
    value = usage.get("prompt_tokens")
    return value if type(value) is int and value >= 0 else None


def _same_profile(request, profile):
    if not request or request["model"] != profile.model:
        return False
    snapshot = json.loads(request["profile_snapshot"]) if request.get("profile_snapshot") else {}
    return snapshot.get("id", "default") == profile.id


def _history_chars(history, clips):
    return sum(len(clips.get(row["id"], row["content"])) + len(row["reasoning"]) + len(row["tool_calls"] or "")
               for row in history if row["status"] == "complete")


def _last_context(request, profile):
    window = getattr(profile, "context_window_tokens", None)
    return {"actual_prompt_tokens": _prompt_tokens(request),
            "last_request_at": request["created_at"] if request else None,
            "request_id": request["id"] if request else None,
            "model": request["model"] if request else None,
            "context_chars_limit": profile.context_chars,
            "context_window_tokens": window if type(window) is int and window > 0 else None,
            "context_window_source": getattr(profile, "context_window_source", None),
            "context_window_measurement": "configured_metadata_not_tokenizer_measurement",
            "limits_for_profile": profile.id,
            "limit_kind": "application_character_guard"}


def _estimate(snapshot, profile):
    latest = snapshot["latest_request"]
    previous = json.loads(latest["input"]) if latest else {}
    known_tools = isinstance(previous.get("tools"), list)
    tools = previous["tools"] if known_tools else []
    messages = project_messages(snapshot["history"], snapshot["clips"], profile, snapshot["prefix"])
    payload_chars = _json_chars(profile.payload(messages, tools))
    sample = snapshot["calibration_request"]
    tokens = _prompt_tokens(sample)
    ratio = len(sample["input"]) / tokens if sample and tokens and _same_profile(sample, profile) else 1.0
    basis = "last_reported_request_ratio" if sample and tokens and _same_profile(sample, profile) else "one_token_per_character_heuristic"
    limitations = ["Token estimates are not tokenizer measurements or guaranteed upper bounds.",
                   "In-flight output and the result of this inspection tool are not included.",
                   "The next request may refresh its prefix or use changed tool definitions."]
    if not known_tools:
        limitations.append("No previous request supplies tool-schema overhead; the estimated target cannot be confirmed.")
    return {"payload_chars": payload_chars, "estimated_tokens": math.ceil(payload_chars / ratio),
            "chars_per_token": ratio, "estimate_basis": basis,
            "calibration_request_id": sample["id"] if basis == "last_reported_request_ratio" else None,
            "tool_schema_chars": _json_chars(tools) if known_tools else None,
            "tool_schema_source": "last_request" if known_tools else "unknown",
            "known_tool_overhead": known_tools, "limitations": limitations}


def _clip(original, identifier, budget):
    marker = f"\n[工具结果已裁剪；runtime read_message message_id={identifier} 可恢复原文]\n"
    retained = max(0, budget - len(marker))
    head = retained // 3
    tail = retained - head
    return (original[:head] if head else "") + marker + (original[-tail:] if tail else "")


def _validate_target(target_tokens):
    if type(target_tokens) is not int or not 1000 <= target_tokens <= 1_000_000:
        raise ValueError("target_tokens must be an integer from 1000 to 1000000")


def latest_tool_round(history):
    """Fresh observations must reach the model before automatic clipping."""
    for row in reversed(history):
        if row['role'] == 'assistant' and row['status'] == 'complete' and row.get('tool_calls'):
            calls = {item['id'] for item in json.loads(row['tool_calls'])}
            return {item['id'] for item in history if item.get('tool_call_id') in calls}
    tools = [row['id'] for row in history if row['role'] == 'tool' and row['status'] == 'complete']
    return set(tools[-1:])


def default_compaction_plan(history, clips, estimate, target_tokens, protected=frozenset()):
    """Pure default policy. The caller owns validation and durable application."""
    _validate_target(target_tokens)
    payload_chars = estimate["payload_chars"]
    target_chars = math.floor(target_tokens * estimate["chars_per_token"])
    plan = {}
    # Equal completed outputs contain no new evidence. Keep the latest full copy
    # and spend the budget on unique material before clipping its head/tail.
    representatives = {}
    duplicates = []
    for row in reversed(history):
        if row["status"] != "complete" or row["role"] != "tool" or row['id'] in clips:
            continue
        content = row['content']
        if content in representatives:
            if row['id'] not in protected:
                duplicates.append((row, representatives[content]))
        else:
            representatives[content] = row['id']
    for row, representative in reversed(duplicates):
        if payload_chars <= target_chars:
            break
        replacement = f"[工具输出与 message_id={representative} 完全相同；runtime read_message message_id={row['id']} 可恢复本次原文]"
        saved = _json_chars(row['content']) - _json_chars(replacement)
        if saved > 0:
            plan[row['id']] = replacement
            payload_chars -= saved
    for row in history:
        if payload_chars <= target_chars:
            break
        if row["status"] != "complete" or row["role"] != "tool" or row['id'] in protected:
            continue
        current = plan.get(row['id'], clips.get(row["id"], row["content"]))
        marker = _clip(row["content"], row["id"], 0)
        if len(current) <= len(marker) or _json_chars(current) <= _json_chars(marker):
            continue
        needed = payload_chars - target_chars
        desired_chars = max(_json_chars(marker), _json_chars(current) - needed)
        low, high = len(marker), len(current) - 1
        replacement = marker
        # Keep as much original head/tail as fits the remaining estimate.
        while low <= high:
            middle = (low + high) // 2
            candidate = _clip(row["content"], row["id"], middle)
            if _json_chars(candidate) <= desired_chars:
                replacement, low = candidate, middle + 1
            else:
                high = middle - 1
        saved = _json_chars(current) - _json_chars(replacement)
        if saved > 0:
            plan[row["id"]] = replacement
            payload_chars -= saved
    return plan


def validate_compaction_plan(history, clips, plan, protected=frozenset()):
    """Reject unsafe strategy output before any projection is written."""
    if not isinstance(plan, dict):
        raise ValueError("Compaction plan must map message IDs to text")
    eligible = {row["id"]: row for row in history if row["status"] == "complete" and row["role"] == "tool"}
    for identifier, content in plan.items():
        row = eligible.get(identifier)
        if row is None or not isinstance(content, str):
            raise ValueError("Compaction may only replace completed tool content")
        if identifier in protected:
            raise ValueError("Automatic compaction must retain the latest tool round.")
        current = clips.get(identifier, row["content"])
        if len(content) >= len(current) or _json_chars(content) >= _json_chars(current):
            raise ValueError("Compaction must shorten each current tool projection")
        if f"message_id={identifier}" not in content:
            raise ValueError("Compaction must retain a recoverable message_id marker")
    return dict(plan)


class Introspection:
    def __init__(self, store, models):
        self.store, self.models = store, models

    def _snapshot(self, db, session_id, profile, epoch):
        session = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if session is None:
            raise ValueError("Session not found")
        if session["model_profile_id"] != profile.id or session["model_epoch"] != epoch:
            raise ValueError("Model changed during inspection; inspect again")
        history = self.store._context_history(db, session_id)
        clips = {row["message_id"]: row["content"] for row in db.execute("SELECT * FROM context_clips WHERE session_id=?", (session_id,))}
        boundary = db.execute("SELECT COUNT(*) FROM compactions WHERE session_id=?", (session_id,)).fetchone()[0]
        saved_prefix = db.execute("SELECT prompt FROM context_prefixes WHERE session_id=? AND epoch=? AND boundary=?", (session_id, epoch, boundary)).fetchone()
        latest = db.execute("SELECT q.* FROM requests q JOIN runs r ON r.id=q.run_id WHERE r.session_id=? ORDER BY julianday(q.created_at) DESC,q.rowid DESC LIMIT 1", (session_id,)).fetchone()
        measured = db.execute(
            "SELECT q.* FROM requests q JOIN runs r ON r.id=q.run_id WHERE r.session_id=? "
            "AND json_valid(q.usage) AND json_type(q.usage,'$.prompt_tokens')='integer' "
            "AND json_extract(q.usage,'$.prompt_tokens')>=0 ORDER BY julianday(q.created_at) DESC,q.rowid DESC LIMIT 1", (session_id,),
        ).fetchone()
        # A different model's tokenizer ratio is not a valid calibration sample.
        calibration = dict(measured) if measured and _same_profile(dict(measured), profile) else None
        return {"history": history, "clips": clips,
                "excluded_runs": [dict(row) for row in db.execute("""SELECT e.run_id,e.version,e.reason FROM context_exclusions e
                    WHERE e.session_id=? AND e.excluded=1 AND e.version=(SELECT MAX(n.version)
                    FROM context_exclusions n WHERE n.session_id=e.session_id AND n.run_id=e.run_id)""", (session_id,))],
                "prefix": saved_prefix[0] if saved_prefix else system_prompt(profile, epoch),
                "latest_request": dict(latest) if latest else None,
                "measured_request": dict(measured) if measured else None,
                "calibration_request": calibration}

    async def usage(self, session_id, window_seconds=3600, scope="session"):
        if type(window_seconds) is not int or window_seconds < 1:
            raise ValueError("window_seconds must be a positive integer")
        if scope not in ("session", "account"):
            raise ValueError("Usage scope must be session or account")
        end = datetime.now(timezone.utc)
        try:
            start = end - timedelta(seconds=window_seconds)
        except OverflowError as exc:
            raise ValueError("Usage window is too large") from exc
        profile, epoch = await self.models.selected(session_id)

        def inspect(db):
            session = db.execute("SELECT user_id FROM sessions WHERE id=?", (session_id,)).fetchone()
            if session is None:
                raise ValueError("Session not found")
            if scope == "account" and not session["user_id"]:
                raise ValueError("Account usage requires an account")
            clause, owner = ("r.session_id=?", session_id) if scope == "session" else ("s.user_id=?", session["user_id"])
            rows = [dict(row) for row in db.execute(
                "SELECT q.* FROM requests q JOIN runs r ON r.id=q.run_id JOIN sessions s ON s.id=r.session_id WHERE "
                + clause + " AND julianday(q.created_at)>=julianday(?) AND julianday(q.created_at)<=julianday(?) "
                "ORDER BY julianday(q.created_at),q.rowid", (owner, start.isoformat(), end.isoformat()),
            )]
            snapshot = self._snapshot(db, session_id, profile, epoch)
            return rows, snapshot["measured_request"]

        rows, measured = await self.store.call(inspect)
        return {**summarize(rows), "window": {"scope": scope, "seconds": window_seconds,
                "from": start.isoformat(), "to": end.isoformat(), "time_basis": "request_started_at"},
                "usage_measurement": "provider_reported", "cost_measurement": "estimated_not_billed",
                "last_context_from_request": _last_context(measured, profile)}

    async def context_status(self, session_id):
        profile, epoch = await self.models.selected(session_id)
        snapshot = await self.store.call(lambda db: self._snapshot(db, session_id, profile, epoch))
        estimate = _estimate(snapshot, profile)
        return {"measurement": "estimated", "context_epoch": epoch,
                "excluded_runs": snapshot["excluded_runs"], "originals_preserved": True,
                "raw_chars": _history_chars(snapshot["history"], {}),
                "clipped_chars": _history_chars(snapshot["history"], snapshot["clips"]),
                "history_chars_kind": "complete_message_content_reasoning_and_tool_calls",
                "estimated_payload_chars": estimate["payload_chars"], "estimated_input_tokens": estimate["estimated_tokens"],
                "estimate_basis": estimate["estimate_basis"], "calibration_request_id": estimate["calibration_request_id"],
                "estimated_tool_schema_chars": estimate["tool_schema_chars"], "tool_schema_source": estimate["tool_schema_source"],
                "context_chars_limit": profile.context_chars, "limit_kind": "application_character_guard",
                "last_context_from_request": _last_context(snapshot["measured_request"], profile),
                "remaining_limitations": estimate["limitations"]}

    async def compaction_plan(self, session_id, target_tokens=20000, protect_latest=False):
        """Return the default plan for optional strategy selection, without writes."""
        _validate_target(target_tokens)
        profile, epoch = await self.models.selected(session_id)
        snapshot = await self.store.call(lambda db: self._snapshot(db, session_id, profile, epoch))
        protected = latest_tool_round(snapshot['history']) if protect_latest else frozenset()
        return default_compaction_plan(snapshot["history"], snapshot["clips"], _estimate(snapshot, profile), target_tokens, protected)

    async def compact(self, session_id, target_tokens=20000, plan_override=None, protect_latest=False):
        _validate_target(target_tokens)
        profile, epoch = await self.models.selected(session_id)

        def compact(db):
            snapshot = self._snapshot(db, session_id, profile, epoch)
            estimate = _estimate(snapshot, profile)
            history, clips = snapshot["history"], snapshot["clips"]
            protected = latest_tool_round(history) if protect_latest else frozenset()
            before_chars = _history_chars(history, clips)
            payload_chars = estimate["payload_chars"]
            proposed = default_compaction_plan(history, clips, estimate, target_tokens, protected) if plan_override is None else plan_override
            plan = validate_compaction_plan(history, clips, proposed, protected)
            history_by_id = {row["id"]: row for row in history}
            changed = list(plan)
            timestamp = now()
            for message_id, replacement in plan.items():
                current = clips.get(message_id, history_by_id[message_id]["content"])
                payload_chars -= _json_chars(current) - _json_chars(replacement)
                clips[message_id] = replacement
                db.execute(
                    "INSERT INTO context_clips(session_id,message_id,content,created_at) VALUES (?,?,?,?) "
                    "ON CONFLICT(session_id,message_id) DO UPDATE SET content=excluded.content,created_at=excluded.created_at",
                    (session_id, message_id, replacement, timestamp),
                )
            after_chars = _history_chars(history, clips)
            identifier = uid() if changed else None
            if changed:
                db.execute("INSERT INTO compactions VALUES (?,?,?,?,?,?)", (
                    identifier, session_id, max((row["seq"] for row in history), default=0), before_chars, after_chars, timestamp,
                ))
            after_tokens = math.ceil(payload_chars / estimate["chars_per_token"])
            limitations = list(estimate["limitations"])
            if protected:
                limitations.append("Automatic compaction retains the latest tool round so newly read evidence reaches the model.")
            if after_tokens > target_tokens:
                limitations.append("Preserved user/assistant messages, reasoning, calls, prefix or schema exceed the target after eligible tool outputs were clipped.")
            return {"measurement": "estimated", "target_tokens": target_tokens,
                    "before": {"estimated_tokens": estimate["estimated_tokens"], "history_chars": before_chars, "estimated_payload_chars": estimate["payload_chars"]},
                    "after": {"estimated_tokens": after_tokens, "history_chars": after_chars, "estimated_payload_chars": payload_chars},
                    "target_met": after_tokens <= target_tokens and estimate["known_tool_overhead"],
                    "estimate_basis": estimate["estimate_basis"], "calibration_request_id": estimate["calibration_request_id"],
                    "estimated_tool_schema_chars": estimate["tool_schema_chars"], "tool_schema_source": estimate["tool_schema_source"],
                    "changed_tool_messages": changed, "compaction_id": identifier,
                    "effective": "next_model_request", "remaining_limitations": limitations}

        return await self.store.call(compact)
