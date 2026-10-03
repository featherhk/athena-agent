"""Account-wide continuity, projected from durable runs rather than a second queue."""
import hashlib
import re

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from .errors import UserError
from .store import now

ACTIVE = ("queued", "running", "waiting_user", "waiting_agents")
TERMINAL = ("complete", "failed", "cancelled", "interrupted")


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS activity_reads (
        user_id TEXT NOT NULL REFERENCES users(id), run_id TEXT NOT NULL REFERENCES runs(id),
        attention_key TEXT NOT NULL, attention_state TEXT NOT NULL, acknowledged_at TEXT NOT NULL,
        PRIMARY KEY(user_id,run_id))""")
    # One durable rollout boundary, not a moving login watermark: work already
    # active at rollout can finish later and still become visible on another device.
    db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES ('activity_started_at',?)", (now(),))
    db.execute("CREATE INDEX IF NOT EXISTS runs_activity ON runs(session_id,status,finished_at)")
    db.execute("CREATE INDEX IF NOT EXISTS sessions_owner ON sessions(user_id)")


def _owner(db, user_id):
    if not user_id or not db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
        raise UserError("not_found", "This account was not found.")


def _query(db, user_id, *, run_id=None, limit=31):
    cutoff = db.execute("SELECT value FROM settings WHERE key='activity_started_at'").fetchone()[0]
    # Only root runs are listed. Descendant help is routed back to its visible
    # root; recursive ownership checks prevent a malformed job from leaking data.
    return db.execute("""WITH RECURSIVE roots AS (
        SELECT r.*,s.title FROM runs r JOIN sessions s ON s.id=r.session_id
        WHERE s.user_id=:user AND (:run IS NULL OR r.id=:run)
          AND NOT EXISTS(SELECT 1 FROM agent_jobs j WHERE j.child_run_id=r.id)
          AND (r.status IN ('queued','running','waiting_user','waiting_agents')
               OR (:run IS NOT NULL OR COALESCE(r.finished_at,r.created_at)>=:cutoff)
                  AND r.status IN ('complete','failed','cancelled','interrupted'))
    ), tree(root_id,run_id,level) AS (
        SELECT id,id,0 FROM roots WHERE status IN ('queued','running','waiting_user','waiting_agents')
        UNION ALL
        SELECT t.root_id,j.child_run_id,t.level+1 FROM tree t
        JOIN agent_jobs j ON j.parent_run_id=t.run_id
        JOIN runs cr ON cr.id=j.child_run_id JOIN sessions cs ON cs.id=cr.session_id
        WHERE t.level<3 AND j.cancel_requested=0 AND cs.user_id=:user
          AND cr.status IN ('queued','running','waiting_user','waiting_agents')
    ), help AS (
        SELECT t.root_id,h.id,h.created_at FROM tree t
        JOIN user_help h ON h.run_id=t.run_id AND h.status='pending'
        JOIN runs hr ON hr.id=h.run_id AND hr.status='waiting_user'
        JOIN sessions hs ON hs.id=h.session_id AND hs.user_id=:user
    ), projected AS (
        SELECT r.*, (SELECT MIN(id) FROM help WHERE root_id=r.id) AS help_id,
          (SELECT MAX(created_at) FROM help WHERE root_id=r.id) AS help_at,
          json_array(r.status,r.finished_at,r.error_code,
            (SELECT group_concat(id,',') FROM (SELECT id FROM help WHERE root_id=r.id ORDER BY id))) AS attention_state
        FROM roots r
    )
    SELECT p.* FROM projected p LEFT JOIN activity_reads a ON a.user_id=:user AND a.run_id=p.id
    WHERE :run IS NOT NULL OR p.status IN ('queued','running','waiting_user','waiting_agents')
       OR a.attention_state IS NULL OR a.attention_state!=p.attention_state
    ORDER BY CASE WHEN p.help_id IS NOT NULL THEN 0
                  WHEN p.status IN ('queued','running','waiting_user','waiting_agents') THEN 2 ELSE 1 END,
             COALESCE(p.finished_at,p.help_at,p.created_at) DESC,p.id DESC
    LIMIT :limit""", {"user": user_id, "run": run_id, "cutoff": cutoff, "limit": limit}).fetchall()


def _item(row):
    # Never expose raw provider/tool errors, response text, drafts or questions.
    title = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", row["title"]).split())[:120]
    key = hashlib.sha256((row["id"] + "\0" + row["attention_state"]).encode()).hexdigest()
    return {"run_id": row["id"], "session_id": row["session_id"], "title": title,
            "status": row["status"], "phase": row["phase"], "attention_key": key,
            "created_at": row["created_at"],
            "updated_at": row["finished_at"] or row["help_at"] or row["created_at"],
            "needs_user": bool(row["help_id"]), "help_id": row["help_id"],
            "error_code": row["error_code"] if row["error_code"] and re.fullmatch(r"[a-z0-9_]{1,80}", row["error_code"]) else None}


async def list_activity(store, user_id, limit=30):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("Activity limit must be between 1 and 100.")
    def read(db):
        _owner(db, user_id)
        rows = _query(db, user_id, limit=limit + 1)
        return {"owner_id": user_id, "items": [_item(row) for row in rows[:limit]], "has_more": len(rows) > limit}
    return await store.call(read)


async def acknowledge_activity(store, user_id, run_id, attention_key):
    if not isinstance(run_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", run_id):
        raise ValueError("Use an activity run ID.")
    if not isinstance(attention_key, str) or not re.fullmatch(r"[a-f0-9]{64}", attention_key):
        raise ValueError("Use the attention key returned by activity list.")
    def acknowledge(db):
        _owner(db, user_id)
        rows = _query(db, user_id, run_id=run_id, limit=1)
        if not rows:
            raise UserError("not_found", "This activity was not found.")
        row, item = rows[0], _item(rows[0])
        if item["attention_key"] != attention_key:
            return {"owner_id": user_id, "acknowledged": False, "item": item}
        # Comparing and writing share the SQLite owner transaction. A concurrent
        # transition either precedes the comparison or invalidates this exact key.
        db.execute("""INSERT INTO activity_reads VALUES (?,?,?,?,?)
            ON CONFLICT(user_id,run_id) DO UPDATE SET attention_key=excluded.attention_key,
            attention_state=excluded.attention_state,acknowledged_at=excluded.acknowledged_at""",
            (user_id, run_id, attention_key, row["attention_state"], now()))
        return {"owner_id": user_id, "acknowledged": True, "item": None if item["status"] in TERMINAL else item}
    return await store.call(acknowledge)


async def activity_operation(store, session_id, args, run_id=None, stop_session=None):
    rows = await store.rows("SELECT user_id FROM sessions WHERE id=?", (session_id,))
    user_id = rows[0]["user_id"] if rows else None
    action = args.get("action", "list")
    allowed = ({"op", "action", "limit"} if action == "list" else
               {"op", "action", "session_id"} if action == "stop" else
               {"op", "action", "run_id", "attention_key"})
    if set(args) - allowed:
        raise ValueError("Unexpected activity fields.")
    if action == "list":
        return await list_activity(store, user_id, args.get("limit", 30))
    if action == "ack":
        return await acknowledge_activity(store, user_id, args.get("run_id"), args.get("attention_key"))
    if action == "stop":
        target = args.get("session_id")
        if not isinstance(target, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", target):
            raise ValueError("Choose an owned conversation to stop.")
        def validate(db):
            _owner(db, user_id)
            if not db.execute("SELECT 1 FROM sessions WHERE id=? AND user_id=?", (target, user_id)).fetchone():
                raise UserError("not_found", "This conversation was not found.")
            if not db.execute("SELECT 1 FROM runs WHERE id=? AND session_id=? AND status='running'", (run_id, session_id)).fetchone():
                raise ValueError("Stop work through an active engine run.")
        await store.call(validate)
        if target == session_id:
            return {"status": "requested", "completed": False, "action": "stop",
                    "session_id": target, "control": {"stop_run": True}}
        if stop_session is None:
            raise ValueError("The engine stop capability is unavailable.")
        return await stop_session(session_id, run_id, target)
    raise ValueError("Activity supports list, ack and stop.")


class ActivityAcknowledgement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    attention_key: str = Field(pattern=r"^[a-f0-9]{64}$")


def register_activity_routes(app):
    @app.get("/api/activity")
    async def activity(request: Request, limit: int = 30):
        return await list_activity(app.state.store, request.state.user["id"], limit)

    @app.post("/api/activity/ack")
    async def acknowledge(body: ActivityAcknowledgement, request: Request):
        return await acknowledge_activity(app.state.store, request.state.user["id"], body.run_id, body.attention_key)
