"""Durable, account-owned application actions with browser execution receipts."""
import json
import re
from typing import Literal

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .auth import COOKIE, token_hash
from .errors import UserError
from .files import initialize_files, list_files, list_session_files, public_file
from .store import now, uid

TARGETS = ("menu", "history", "files", "account", "appearance", "developer", "password", "workspace", "preview")
OPEN_TARGETS = tuple(target for target in TARGETS if target != "preview")
ERROR_CODES = ("unavailable", "user_gesture_required", "file_not_found", "navigation_failed", "execution_failed", "dismissed", "reset", "interrupted")
IDENTIFIER = re.compile(r"[a-zA-Z0-9_-]{1,64}\Z")
CATALOG = [
    {"action": "list", "effect": "Discover application controls and current requests."},
    {"action": "read", "optional": ["id"], "effect": "Read pending requests and browser acknowledgements."},
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
    return item


def _event(db, row):
    db.execute("INSERT INTO events(session_id,type,payload,created_at) VALUES (?,?,?,?)",
               (row["session_id"], "app_action", json.dumps(_public(row), ensure_ascii=False), now()))


def _identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("Use a valid existing identifier.")
    return value


async def app_operation(store, session_id, run_id, args, config=None):
    await initialize_app_controls(store)
    action = args.get("action", "list")
    if action not in {item["action"] for item in CATALOG}:
        raise ValueError("Unknown application action; use app action=list to discover controls.")
    fields = {"op", "action"} | ({"id", "limit", "offset"} if action in ("list", "read") else {"target"} if action in ("open", "close") else {"session_id"} if action == "navigate" else {"file_id"} if action in ("attach", "detach", "preview", "download") else set())
    if set(args) - fields:
        raise ValueError("Unsupported application arguments; secret form values never belong in an application tool.")
    files = None
    if action in ("list", "read", "preview", "download", "attach", "detach"):
        await initialize_files(store)
    if action in ("list", "read") and not args.get("id"):
        limit, offset = args.get("limit", 50), args.get("offset", 0)
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            raise ValueError("Use file listing limit 1–200 and nonnegative offset.")
        user_id = await store.call(lambda db: _scope(db, session_id))
        files = (await list_session_files(store, config, user_id, session_id) if config is not None
                 else await list_files(store, user_id, session_id))
    def operate(db):
        user_id = _scope(db, session_id)
        if action in ("list", "read"):
            if args.get("id"):
                identifier = _identifier(args["id"])
                row = db.execute("SELECT * FROM app_actions WHERE id=? AND user_id=?", (identifier, user_id)).fetchone()
                if row is None:
                    raise UserError("not_found", "This application request was not found.")
                return {"request": _public(row), "completed": row["status"] == "applied"}
            rows = db.execute("SELECT * FROM app_actions WHERE user_id=? AND session_id=? ORDER BY created_at DESC,rowid DESC LIMIT 50", (user_id, session_id)).fetchall()
            return {"operations": CATALOG, "related_operations": RELATED_OPERATIONS,
                    "files": files[offset:offset + limit], "files_total": len(files), "offset": offset,
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
        # Opaque forms and file pickers remain in the fixed shell; no secret or path input.
        identifier = uid()
        db.execute("INSERT INTO app_actions(id,user_id,session_id,run_id,action,payload,status,created_at) VALUES (?,?,?,?,?,?,'pending',?)",
                   (identifier, user_id, session_id, run_id, action, json.dumps(payload, ensure_ascii=False), now()))
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


async def claim_app_action(store, session_id, user_id, action_id, client_id):
    _identifier(action_id)
    _identifier(client_id)
    await initialize_app_controls(store)
    def claim(db):
        _scope(db, session_id, user_id)
        row = db.execute("SELECT * FROM app_actions WHERE id=? AND user_id=? AND session_id=?", (action_id, user_id, session_id)).fetchone()
        if row is None:
            raise UserError("not_found", "This application request was not found.")
        if row["status"] != "pending":
            return {"accepted": False, "request": _public(row)}
        db.execute("UPDATE app_actions SET status='dispatched',client_id=?,dispatched_at=? WHERE id=?", (client_id, now(), action_id))
        row = db.execute("SELECT * FROM app_actions WHERE id=?", (action_id,)).fetchone()
        _event(db, row)
        return {"accepted": True, "request": _public(row)}
    return await store.call(claim)


async def acknowledge_app_action(store, session_id, user_id, action_id, client_id, status, error_code=None, logout_hash=None):
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
        if row["status"] != "dispatched":
            if row["status"] == status and row["error_code"] == error_code:
                return {"changed": False, "request": _public(row), "completed": status == "applied"}
            raise UserError("invalid_input", "This application request already has a different result.")
        if row["action"] == "sign_out" and status == "applied":
            if not logout_hash:
                raise ValueError("Sign out requires the receiving browser's authenticated session.")
            db.execute("DELETE FROM auth_sessions WHERE token_hash=? AND user_id=?", (logout_hash, user_id))
        db.execute("UPDATE app_actions SET status=?,error_code=?,resolved_at=? WHERE id=?", (status, error_code, now(), action_id))
        row = db.execute("SELECT * FROM app_actions WHERE id=?", (action_id,)).fetchone()
        _event(db, row)
        return {"changed": True, "request": _public(row), "completed": status == "applied"}
    return await store.call(acknowledge)


def cancel_app_actions(db, user_id, session_id=None, reason="reset"):
    """Reset must not replay a pending view/download action after recovery."""
    _initialize(db)
    if reason not in ("reset", "interrupted"):
        raise ValueError("Unsupported application cancellation reason.")
    where, values = "user_id=? AND status IN ('pending','dispatched')", [user_id]
    if session_id is not None:
        where += " AND session_id=?"
        values.append(session_id)
    rows = db.execute(f"SELECT * FROM app_actions WHERE {where}", values).fetchall()
    for row in rows:
        db.execute("UPDATE app_actions SET status='dismissed',error_code=?,resolved_at=? WHERE id=?", (reason, now(), row["id"]))
        _event(db, db.execute("SELECT * FROM app_actions WHERE id=?", (row["id"],)).fetchone())


class ClaimInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")


class AckInput(ClaimInput):
    status: Literal["applied", "failed", "dismissed"]
    error_code: str | None = Field(default=None, max_length=40)


def register_app_control_routes(app, exists):
    @app.post("/api/sessions/{session_id}/app-actions/{action_id}/claim")
    async def claim(session_id: str, action_id: str, body: ClaimInput, request: Request):
        await exists(session_id, request)
        return await claim_app_action(app.state.store, session_id, request.state.user["id"], action_id, body.client_id)

    @app.post("/api/sessions/{session_id}/app-actions/{action_id}/ack")
    async def acknowledge(session_id: str, action_id: str, body: AckInput, request: Request):
        await exists(session_id, request)
        result = await acknowledge_app_action(app.state.store, session_id, request.state.user["id"], action_id,
                                              body.client_id, body.status, body.error_code,
                                              token_hash(request.cookies[COOKIE]) if request.cookies.get(COOKIE) else None)
        response = JSONResponse(result)
        if result["changed"] and result["completed"] and result["request"]["action"] == "sign_out":
            response.delete_cookie(COOKIE)
        return response
