"""Durable, account-owned application actions with browser execution receipts."""
import asyncio
import hashlib
import json
import re
from typing import Literal

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .auth import COOKIE, token_hash
from .errors import UserError
from .files import initialize_files, list_files, list_session_files, public_file
from .store import now, uid
from . import frontend_state

TARGETS = ("menu", "history", "files", "account", "appearance", "developer", "password", "workspace", "preview", "surfaces", "activity")
OPEN_TARGETS = tuple(target for target in TARGETS if target != "preview")
ERROR_CODES = ("frontend_conflict", "frontend_unavailable", "unavailable", "user_gesture_required", "file_not_found", "navigation_failed", "execution_failed", "dismissed", "reset", "interrupted")
INACTIVE_RUN_STATUSES = ("failed", "cancelled", "interrupted", "stopped")
INPUT_CLEAR_ACTIONS = frozenset(("open", "close", "preview", "navigate"))
IDENTIFIER = re.compile(r"[a-zA-Z0-9_-]{1,64}\Z")
CATALOG = [
    {"action": "list", "optional": ["scope", "session_id", "limit", "offset"], "effect": "Discover application controls, origin requests and files in an owned conversation."},
    {"action": "snapshot", "required": ["client_id"], "effect": "Inspect one authenticated client semantic state, revision and freshness; no draft or form values."},
    {"action": "read", "optional": ["id", "scope", "session_id", "limit", "offset"], "effect": "Read request acknowledgements by id, or list owned files; id cannot combine with scope or session_id."},
    {"action": "open", "targets": list(OPEN_TARGETS), "effect": "Open a fixed application view."},
    {"action": "close", "targets": list(TARGETS), "effect": "Close a fixed application view."},
    {"action": "navigate", "required": ["session_id"], "effect": "Show another conversation owned by this account."},
    {"action": "preview", "required": ["file_id"], "effect": "Show an owned file in the preview pane."},
    {"action": "download", "required": ["file_id"], "effect": "Ask the browser to download an owned file; this does not verify the saved file."},
    {"action": "attach", "optional": ["file_id"], "effect": "Attach a current-conversation file, or show file selection when no file_id is given."},
    {"action": "detach", "required": ["file_id"], "effect": "Remove a current-conversation file from the input attachment tray; retain the file."},
    {"action": "sign_out", "effect": "Sign the receiving browser out after it acknowledges the request."},
    {"action": "stop", "effect": "Stop this conversation through the engine; inspect durable run status."},
    {"action": "reset", "effect": "Reset this account's customizations through the engine, retaining chats and files."},
]
RELATED_OPERATIONS = [
    {"op": "activity", "action": "list", "effect": "See active work, requests for help and recent unread outcomes across this account; application activity reference describes acknowledgement."},
    {"op": "sessions", "action": "new", "effect": "Create a conversation; then use app navigate with its returned id to display it."},
    {"op": "sessions", "action": "rename", "effect": "Rename an owned conversation."},
    {"op": "sessions", "action": "fork", "effect": "Fork a conversation; background tasks remain paused; use app navigate to display the returned id."},
    {"op": "app_settings", "effect": "Read or change language, fonts, colors, text/display settings and developer mode; query its current schema."},
    {"op": "account", "action": "update", "effect": "Change display name or avatar; open password UI instead of passing a password through a tool."},
    {"op": "cancel_task", "required": ["task_id"], "effect": "Pause a background task, like the task-card control."},
    {"op": "registry", "kind": "component", "action": "register", "effect": "Create or update a work surface; browser observations verify loading separately."},
    {"op": "user_help", "effect": "Ask for missing information or a necessary manual step without holding a model stream open."},
]


def _initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS app_actions (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
        session_id TEXT NOT NULL REFERENCES sessions(id), run_id TEXT REFERENCES runs(id),
        action TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
        client_id TEXT, error_code TEXT, created_at TEXT NOT NULL,
        dispatched_at TEXT, resolved_at TEXT)""")
    for column, definition in (("target_client_id", "TEXT"), ("expected_revision", "INTEGER"),
                               ("target_session_id", "TEXT"), ("receipt", "TEXT")):
        if column not in {row[1] for row in db.execute("PRAGMA table_info(app_actions)")}:
            db.execute(f"ALTER TABLE app_actions ADD COLUMN {column} {definition}")
    frontend_state.initialize(db)
    db.execute("CREATE INDEX IF NOT EXISTS app_actions_client ON app_actions(target_client_id,status)")
    db.execute("CREATE INDEX IF NOT EXISTS app_actions_pending ON app_actions(user_id,session_id,status,created_at)")


async def initialize_app_controls(store):
    await store.call(_initialize)


def _scope(db, session_id, user_id=None):
    session = db.execute("SELECT user_id FROM sessions WHERE id=?", (session_id,)).fetchone()
    if session is None or not session["user_id"] or (user_id is not None and session["user_id"] != user_id):
        raise UserError("not_found", "This conversation was not found.")
    return session["user_id"]


def _public(row):
    item = {key: row[key] for key in ("id", "session_id", "run_id", "action", "status", "error_code", "created_at", "dispatched_at", "resolved_at")}
    item["payload"] = json.loads(row["payload"])
    item["payload"].pop("_claim_guard", None)
    item["target_client_id"] = row["target_client_id"]
    item["expected_revision"] = row["expected_revision"]
    item["receipt"] = json.loads(row["receipt"]) if row["receipt"] else None
    return item


def _event(db, row):
    db.execute("INSERT INTO events(session_id,type,payload,created_at) VALUES (?,?,?,?)",
               (row["session_id"], "app_action", json.dumps(_public(row), ensure_ascii=False), now()))


def _identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("Use a valid existing identifier.")
    return value


def _input_clear_state_hash(state):
    """Compare all observed semantics except the single input-clear transition."""
    remaining = {key: value for key, value in state.items() if key != "has_unsent_changes"}
    return hashlib.sha256(json.dumps(remaining, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _input_clear_guard(db, user_id, session_id, run_id, selected, action, args):
    if action not in INPUT_CLEAR_ACTIONS or "expected_revision" in args:
        return None
    source = frontend_state.run_source(db, user_id, session_id, run_id)
    state = json.loads(selected["state"])
    if (source is None or source["client_id"] != selected["client_id"]
            or source["auth_hash"] != selected["auth_hash"] or not state["has_unsent_changes"]):
        return None
    return {"kind": "source_input_cleared", "revision": selected["revision"],
            "state_hash": _input_clear_state_hash(state)}


def _claim_revision(db, row, observed):
    expected = row["expected_revision"]
    guard = json.loads(row["payload"]).get("_claim_guard")
    if (row["action"] not in INPUT_CLEAR_ACTIONS or not isinstance(guard, dict)
            or guard.get("kind") != "source_input_cleared" or guard.get("revision") != expected
            or observed["revision"] != expected + 1):
        return expected
    source = frontend_state.run_source(db, row["user_id"], row["session_id"], row["run_id"])
    state = json.loads(observed["state"])
    if (source is None or source["client_id"] != row["target_client_id"]
            or source["client_id"] != observed["client_id"] or source["auth_hash"] != observed["auth_hash"]
            or observed["session_id"] != row["target_session_id"] or state["has_unsent_changes"]
            or _input_clear_state_hash(state) != guard.get("state_hash")):
        return expected
    # Only this one revision may be rebased. The guard stays private in durable
    # storage; the caller receives the new exact revision and still checks it.
    return observed["revision"]


async def app_operation(store, session_id, run_id, args, config=None):
    await initialize_app_controls(store)
    action = args.get("action", "list")
    if action not in {item["action"] for item in CATALOG}:
        raise ValueError("Unknown application action; use app action=list to discover controls.")
    fields = {"op", "action"} | ({"id", "scope", "session_id", "limit", "offset"} if action in ("list", "read") else {"target"} if action in ("open", "close") else {"session_id"} if action == "navigate" else {"file_id"} if action in ("attach", "detach", "preview", "download") else {"client_id"} if action == "snapshot" else set())
    if action not in ("list", "read", "snapshot", "stop", "reset"):
        fields |= {"client_id", "expected_revision"}
    if "client_id" in args:
        _identifier(args["client_id"])
    if "expected_revision" in args and (type(args["expected_revision"]) is not int or args["expected_revision"] < 1):
        raise ValueError("Use a positive frontend expected_revision.")
    if set(args) - fields:
        raise ValueError("Unsupported application arguments; secret form values never belong in an application tool.")
    if action in ("list", "read") and "id" in args and ("session_id" in args or "scope" in args):
        raise ValueError("Use either an application request id or a files session_id/scope, not both.")
    files_scope = args.get("scope", "session")
    if action in ("list", "read"):
        if files_scope not in ("session", "account"):
            raise ValueError("File scope must be session or account.")
        if files_scope == "account" and ("session_id" in args or "id" in args):
            raise ValueError("Account file scope cannot be combined with session_id or an application request id.")
    files = None
    files_session_id = session_id
    if action in ("list", "read", "preview", "download", "attach", "detach"):
        await initialize_files(store)
    if action in ("list", "read") and not args.get("id"):
        limit, offset = args.get("limit", 50), args.get("offset", 0)
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            raise ValueError("Use file listing limit 1–200 and nonnegative offset.")
        files_session_id = None if files_scope == "account" else _identifier(args.get("session_id", session_id))
        def file_scope(db):
            user_id = _scope(db, session_id)
            if files_session_id is not None:
                _scope(db, files_session_id, user_id)
            return user_id
        user_id = await store.call(file_scope)
        files = (await list_session_files(store, config, user_id, files_session_id) if config is not None and files_session_id is not None
                 else await list_files(store, user_id, files_session_id))
    def operate(db):
        user_id = _scope(db, session_id)
        if action == "snapshot":
            return frontend_state.snapshot(db, user_id, _identifier(args.get("client_id")))
        if action in ("list", "read"):
            if args.get("id"):
                identifier = _identifier(args["id"])
                row = db.execute("SELECT * FROM app_actions WHERE id=? AND user_id=?", (identifier, user_id)).fetchone()
                if row is None:
                    raise UserError("not_found", "This application request was not found.")
                return {"request": _public(row), "completed": row["status"] == "applied"}
            rows = db.execute("SELECT * FROM app_actions WHERE user_id=? AND session_id=? ORDER BY created_at DESC,rowid DESC LIMIT 50", (user_id, session_id)).fetchall()
            return {"operations": CATALOG, "related_operations": RELATED_OPERATIONS,
                    "clients": frontend_state.clients(db, user_id),
                    "source": frontend_state.source_summary(db, user_id, session_id, run_id),
                    "targeting": "UI actions default to this run's source client, even after navigation. An unavailable source never falls back. Without a source, exactly one fresh foreground client must show this conversation. Explicit client_id overrides the default.",
                    "files": files[offset:offset + limit], "files_scope": files_scope, "files_session_id": files_session_id, "files_total": len(files), "offset": offset,
                    "limit": limit, "has_more": offset + limit < len(files), "next_offset": min(offset + limit, len(files)),
                    "pending": [_public(row) for row in rows if row["status"] == "pending"],
                    "recent": [_public(row) for row in rows[:20]],
                    "receipt_scope": "A browser acknowledgement confirms the UI action, not a completed download, password change, or external effect."}
        if run_id is not None:
            run = db.execute("SELECT status FROM runs WHERE id=? AND session_id=?", (run_id, session_id)).fetchone()
            if run is None or run["status"] != "running":
                raise ValueError("Application actions from an agent require its active run.")
        if action in ("stop", "reset"):
            if run_id is None:
                raise ValueError("Stop or reset through the active engine run.")
            return {"status": "requested", "completed": False, "action": action,
                    "control": {"stop_run" if action == "stop" else "reset_user": True}}
        payload = {}
        if action in ("open", "close"):
            target = args.get("target")
            if target not in (OPEN_TARGETS if action == "open" else TARGETS):
                raise ValueError("Choose a supported application view.")
            payload["target"] = target
        elif action == "navigate":
            target = _identifier(args.get("session_id"))
            _scope(db, target, user_id)
            payload["session_id"] = target
        elif action in ("preview", "download", "attach", "detach"):
            identifier = args.get("file_id")
            if identifier is not None or action != "attach":
                identifier = _identifier(identifier)
                row = db.execute("SELECT f.* FROM file_entries f JOIN sessions s ON s.id=f.session_id WHERE f.id=? AND f.user_id=? AND s.user_id=?", (identifier, user_id, user_id)).fetchone()
                if row is None or (action in ("attach", "detach") and row["session_id"] != session_id):
                    raise UserError("file_not_found", "The file is unavailable in this scope.")
                payload["file"] = public_file(row)
                payload["file_id"] = identifier
        selected = frontend_state.target(db, user_id, session_id, args.get("client_id"), args.get("expected_revision"), run_id)
        if action in ("attach", "detach") and selected["session_id"] != session_id:
            raise UserError("frontend_conflict", "Attachment changes require the selected browser to show this conversation.")
        if guard := _input_clear_guard(db, user_id, session_id, run_id, selected, action, args):
            payload["_claim_guard"] = guard
        # Opaque forms and file pickers remain in the fixed shell; no secret or path input.
        identifier = uid()
        db.execute("INSERT INTO app_actions(id,user_id,session_id,run_id,action,payload,status,created_at,target_client_id,expected_revision,target_session_id) VALUES (?,?,?,?,?,?,'pending',?,?,?,?)",
                   (identifier, user_id, session_id, run_id, action, json.dumps(payload, ensure_ascii=False), now(), selected["client_id"], selected["revision"], selected["session_id"]))
        row = db.execute("SELECT * FROM app_actions WHERE id=?", (identifier,)).fetchone()
        _event(db, row)
        return {"status": "requested", "completed": False, "request": _public(row)}
    return await store.call(operate)


async def list_app_actions(store, session_id, user_id):
    await initialize_app_controls(store)
    def listing(db):
        _scope(db, session_id, user_id)
        return [_public(row) for row in db.execute("SELECT * FROM app_actions WHERE session_id=? AND user_id=? AND status='pending' ORDER BY created_at,rowid", (session_id, user_id))]
    return await store.call(listing)


async def claim_app_action(store, session_id, user_id, action_id, client_id, auth_hash=None):
    _identifier(action_id)
    _identifier(client_id)
    await initialize_app_controls(store)
    def claim(db):
        _scope(db, session_id, user_id)
        row = db.execute("SELECT * FROM app_actions WHERE id=? AND user_id=? AND session_id=?", (action_id, user_id, session_id)).fetchone()
        if row is None:
            raise UserError("not_found", "This application request was not found.")
        if row["target_client_id"] != client_id:
            raise UserError("frontend_unavailable", "This request is not addressed to this browser; old untargeted requests cannot be replayed.")
        observed = frontend_state.client(db, user_id, client_id, auth_hash)
        if row["status"] != "pending":
            return {"accepted": False, "request": _public(row)}
        if row["run_id"]:
            run = db.execute("SELECT status FROM runs WHERE id=? AND session_id=?", (row["run_id"], session_id)).fetchone()
            if run and run["status"] in INACTIVE_RUN_STATUSES:
                # Includes old persisted intents created before terminal cleanup.
                # Claim and run finalization share Store's serialized transaction.
                cancel_app_actions(db, user_id, session_id=session_id, run_id=row["run_id"], reason="interrupted", pending_only=True)
                row = db.execute("SELECT * FROM app_actions WHERE id=?", (action_id,)).fetchone()
                return {"accepted": False, "request": _public(row)}
        try:
            expected_revision = _claim_revision(db, row, observed)
            frontend_state.target(db, user_id, session_id, client_id, expected_revision)
            if observed["session_id"] != row["target_session_id"]:
                raise UserError("frontend_conflict", "The browser changed conversations before this action was applied.")
        except UserError as exc:
            db.execute("UPDATE app_actions SET status='failed',error_code=?,resolved_at=? WHERE id=?", (exc.code, now(), action_id))
            row = db.execute("SELECT * FROM app_actions WHERE id=?", (action_id,)).fetchone()
            _event(db, row)
            return {"accepted": False, "request": _public(row)}
        db.execute("UPDATE app_actions SET status='dispatched',client_id=?,dispatched_at=?,expected_revision=? WHERE id=?",
                   (client_id, now(), expected_revision, action_id))
        row = db.execute("SELECT * FROM app_actions WHERE id=?", (action_id,)).fetchone()
        _event(db, row)
        return {"accepted": True, "request": _public(row)}
    return await store.call(claim)


async def acknowledge_app_action(store, session_id, user_id, action_id, client_id, status, error_code=None, logout_hash=None, observed_revision=None, auth_hash=None):
    _identifier(action_id)
    _identifier(client_id)
    if status not in ("applied", "failed", "dismissed") or error_code not in (None, *ERROR_CODES) or (status == "applied" and error_code is not None):
        raise ValueError("Invalid application acknowledgement.")
    await initialize_app_controls(store)
    def acknowledge(db):
        _scope(db, session_id, user_id)
        row = db.execute("SELECT * FROM app_actions WHERE id=? AND user_id=? AND session_id=?", (action_id, user_id, session_id)).fetchone()
        if row is None:
            raise UserError("not_found", "This application request was not found.")
        if row["client_id"] != client_id:
            raise UserError("invalid_input", "This request was not claimed by this browser.")
        frontend_state.client(db, user_id, client_id, auth_hash)
        if row["status"] != "dispatched":
            if row["status"] == status and row["error_code"] == error_code:
                return {"changed": False, "request": _public(row), "completed": status == "applied"}
            raise UserError("invalid_input", "This application request already has a different result.")
        observed = frontend_state.client(db, user_id, client_id, auth_hash, fresh=status == "applied")
        receipt = None
        if status == "applied":
            # A newer report can arrive between report and ACK (for example a
            # preview finishing its load). Verify the latest semantic state while
            # accepting a receipt based on any observation since this claim.
            if type(observed_revision) is not int or not row["expected_revision"] <= observed_revision <= observed["revision"]:
                raise UserError("frontend_conflict", "An observation at or after the claimed revision is required to acknowledge a UI effect.")
            receipt = _verify_effect(row, observed)
            receipt["reported_revision"] = observed_revision
        if row["action"] == "sign_out" and status == "applied":
            if not logout_hash:
                raise ValueError("Sign out requires the receiving browser's authenticated session.")
            db.execute("DELETE FROM auth_sessions WHERE token_hash=? AND user_id=?", (logout_hash, user_id))
        db.execute("UPDATE app_actions SET status=?,error_code=?,resolved_at=?,receipt=? WHERE id=?", (status, error_code, now(), json.dumps(receipt) if receipt else None, action_id))
        row = db.execute("SELECT * FROM app_actions WHERE id=?", (action_id,)).fetchone()
        _event(db, row)
        return {"changed": True, "request": _public(row), "completed": status == "applied"}
    return await store.call(acknowledge)


def cancel_app_actions(db, user_id, session_id=None, reason="reset", *, run_id=None, pending_only=False):
    """Cancel unclaimed intent; cancellation cannot undo an already sent effect."""
    _initialize(db)
    if reason not in ("reset", "interrupted"):
        raise ValueError("Unsupported application cancellation reason.")
    where, values = "user_id=? AND " + ("status='pending'" if pending_only else "status IN ('pending','dispatched')"), [user_id]
    if session_id is not None:
        where += " AND session_id=?"
        values.append(session_id)
    if run_id is not None:
        where += " AND run_id=?"
        values.append(run_id)
    if reason == "reset":
        frontend_state.clear(db, user_id)
    rows = db.execute(f"SELECT * FROM app_actions WHERE {where}", values).fetchall()
    for row in rows:
        receipt = {"effect": "not_dispatched" if row["status"] == "pending" else "unknown",
                   "may_have_applied": row["status"] == "dispatched"}
        db.execute("UPDATE app_actions SET status='dismissed',error_code=?,resolved_at=?,receipt=? WHERE id=?", (reason, now(), json.dumps(receipt), row["id"]))
        _event(db, db.execute("SELECT * FROM app_actions WHERE id=?", (row["id"],)).fetchone())


class ClaimInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    client_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")


class AckInput(ClaimInput):
    status: Literal["applied", "failed", "dismissed"]
    error_code: str | None = Field(default=None, max_length=40)
    observed_revision: int | None = Field(default=None, ge=1, le=9007199254740991)


def _verify_effect(action, observation):
    state, payload = json.loads(observation["state"]), json.loads(action["payload"])
    kind = action["action"]
    views = state["open_views"]
    matches = True
    if kind == "navigate":
        matches = state["session_id"] == payload["session_id"]
    elif kind == "open":
        matches = payload["target"] in views
    elif kind == "close":
        matches = payload["target"] not in views
        if payload["target"] == "preview":
            matches = matches and state["preview"] is None
        if payload["target"] == "workspace":
            matches = matches and state["workspace"] is None
    elif kind == "preview":
        matches = bool(state["preview"] and state["preview"]["file_id"] == payload["file_id"])
    elif kind == "attach":
        matches = (payload["file_id"] in state["attachment_ids"] if "file_id" in payload
                   else any(item["kind"] == "file_selection" for item in state["waiting"]))
    elif kind == "detach":
        matches = payload["file_id"] not in state["attachment_ids"]
    if not matches:
        raise UserError("frontend_conflict", "The latest browser observation does not show the requested effect.")
    result = {"observed_revision": observation["revision"], "observed_at": observation["observed_at"],
              "effect": "download_requested" if kind == "download" else "sign_out_acknowledged" if kind == "sign_out" else "ui_state_observed"}
    if kind == "preview":
        result.update(file_ready=state["preview"]["status"] == "ready", file_status=state["preview"]["status"])
    return result


async def client_app_actions(store, user_id, client_id, auth_hash):
    await initialize_app_controls(store)
    def listing(db):
        frontend_state.client(db, user_id, client_id, auth_hash, fresh=True)
        return [_public(row) for row in db.execute("SELECT * FROM app_actions WHERE user_id=? AND target_client_id=? AND status='pending' ORDER BY created_at,rowid LIMIT 64", (user_id, client_id))]
    return await store.call(listing)


def register_app_control_routes(app, exists):
    @app.get("/api/frontend/clients/{client_id}/events")
    async def client_events(client_id: str, request: Request):
        _identifier(client_id)
        user_id, auth_hash = request.state.user["id"], token_hash(request.cookies[COOKIE])
        await client_app_actions(app.state.store, user_id, client_id, auth_hash)
        async def generate():
            delivered = set()
            ticks = 0
            while not app.state.engine.closing and not await request.is_disconnected():
                try:
                    rows = await client_app_actions(app.state.store, user_id, client_id, auth_hash)
                except UserError:
                    return
                for row in rows:
                    if row["id"] not in delivered or ticks % 5 == 0:
                        yield "data: " + json.dumps({"type": "app_action", "payload": row}, ensure_ascii=False) + "\n\n"
                delivered = {row["id"] for row in rows}
                ticks += 1
                if ticks % 15 == 0:
                    yield ": keepalive\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(generate(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.post("/api/sessions/{session_id}/app-actions/{action_id}/claim")
    async def claim(session_id: str, action_id: str, body: ClaimInput, request: Request):
        await exists(session_id, request)
        return await claim_app_action(app.state.store, session_id, request.state.user["id"], action_id, body.client_id, token_hash(request.cookies[COOKIE]))

    @app.post("/api/sessions/{session_id}/app-actions/{action_id}/ack")
    async def acknowledge(session_id: str, action_id: str, body: AckInput, request: Request):
        await exists(session_id, request)
        result = await acknowledge_app_action(app.state.store, session_id, request.state.user["id"], action_id,
                                              body.client_id, body.status, body.error_code,
                                              token_hash(request.cookies[COOKIE]) if request.cookies.get(COOKIE) else None,
                                              body.observed_revision, token_hash(request.cookies[COOKIE]))
        response = JSONResponse(result)
        if result["changed"] and result["completed"] and result["request"]["action"] == "sign_out":
            response.delete_cookie(COOKIE)
        return response
