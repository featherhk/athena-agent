"""All SQLite work is owned by one dedicated thread, never by the event loop.

No connection or cursor crosses the boundary. Operations return detached values.
Transactions serialize session claims and timer wakeups, independent of the GIL.
"""
from .errors import UserError
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Callable, TypeVar
from uuid import uuid4

T = TypeVar("T")
SCHEMA_VERSION = 16


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid() -> str:
    return uuid4().hex


class Store:
    def __init__(self, path: Path):
        self.path = path
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="athena-db")
        self._connection: sqlite3.Connection | None = None

    def _conn(self) -> sqlite3.Connection:
        if self._connection is None:
            self._connection = sqlite3.connect(self.path)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA busy_timeout=5000")
            self._connection.execute("PRAGMA foreign_keys=ON")
        return self._connection

    async def call(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        def execute():
            connection = self._conn()
            with connection:
                return operation(connection)
        return await asyncio.get_running_loop().run_in_executor(self._pool, execute)

    async def initialize(self):
        schema = Path(__file__).with_name("schema.sql").read_text()
        def migrate(db):
            if db.execute('PRAGMA user_version').fetchone()[0] > SCHEMA_VERSION:
                raise ValueError('This database requires a newer Athena version')
            db.executescript(schema)
            # Additive v1 -> v2 migration: retain all pre-login local data.
            for table, column, definition in (
                ("sessions", "user_id", "TEXT REFERENCES users(id)"), ("components", "file_path", "TEXT"),
                ("components", "placement", "TEXT NOT NULL DEFAULT 'inline'"),
                ("components", "file_id", "TEXT"),
                ("sessions", "model_profile_id", "TEXT NOT NULL DEFAULT 'default'"),
                ("sessions", "model_epoch", "INTEGER NOT NULL DEFAULT 0"),
                ("sessions", "submission_revision", "INTEGER NOT NULL DEFAULT 0"),
                ("requests", "profile_snapshot", "TEXT"), ("requests", "context_epoch", "INTEGER"),
                ("requests", "response_model", "TEXT"), ("requests", "error_code", "TEXT"),
                ("runs", "error_code", "TEXT"),
                ("runs", "phase", "TEXT NOT NULL DEFAULT 'idle'"),
                ("messages", "attachments", "TEXT NOT NULL DEFAULT '[]'"),
                ("messages", "context_note", "TEXT NOT NULL DEFAULT ''"),
                ("messages", "help_reply", "TEXT"),
                ("messages", "origin", "TEXT"),
                ("messages", "context_after_seq", "INTEGER"),
                ("requests", "tool_versions", "TEXT"),
                ("events", "encoding", "TEXT NOT NULL DEFAULT 'json'"),
                ("events", "base_seq", "INTEGER REFERENCES events(seq)"),
            ):
                if column not in {row[1] for row in db.execute(f"PRAGMA table_info({table})")}:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            db.execute("""CREATE TABLE IF NOT EXISTS context_prefixes (
                session_id TEXT NOT NULL REFERENCES sessions(id), epoch INTEGER NOT NULL,
                boundary INTEGER NOT NULL, prompt TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(session_id,epoch,boundary))""")
            if db.execute("PRAGMA user_version").fetchone()[0] < 7:
                from .user_help import backfill_help_replies
                backfill_help_replies(db)
            from .memory import migrate_memories
            migrate_memories(db)
            if db.execute('PRAGMA user_version').fetchone()[0] < 11:
                from .task_events import backfill_task_origins
                backfill_task_origins(db)
            db.execute('CREATE INDEX IF NOT EXISTS events_archive ON events(type,encoding,seq)')
            from .activity import initialize as initialize_activity
            initialize_activity(db)
            from .frontend_state import initialize as initialize_frontend_state
            initialize_frontend_state(db)
            from .desktop_resume import initialize as initialize_desktop_resume
            initialize_desktop_resume(db)
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        await self.call(migrate)
        from .context_controls import initialize_context_controls
        await initialize_context_controls(self)
        self.path.chmod(0o600)
        # Never replay a possibly side-effecting interrupted run automatically.
        def recover(db):
            from .browser import recover_browser_actions
            recover_browser_actions(db)
            db.execute("UPDATE tasks SET status='active' WHERE status='checking'")
            db.execute("UPDATE task_checks SET status='interrupted',finished_at=? WHERE status='running'", (now(),))
            interrupted = db.execute("SELECT id,session_id FROM runs WHERE status='running'").fetchall()
            for run in interrupted:
                db.execute("UPDATE runs SET status='interrupted',error_code='restarted',error=?,finished_at=? WHERE id=?", ("服务重启，请确认后继续。", now(), run["id"]))
                db.execute("UPDATE messages SET status='interrupted' WHERE run_id=? AND status='streaming'", (run["id"],))
                # Complete missing tool-result slots so future provider requests stay valid.
                for message in db.execute("SELECT tool_calls FROM messages WHERE run_id=? AND tool_calls IS NOT NULL", (run["id"],)).fetchall():
                    for tool in json.loads(message[0]):
                        if not db.execute("SELECT 1 FROM messages WHERE run_id=? AND tool_call_id=?", (run["id"], tool["id"])).fetchone():
                            self._message(db, run["session_id"], run["id"], "tool", '{"status":"unknown_after_restart","message":"Check actual state before retrying."}', "complete", tool_call_id=tool["id"])
            # Only a fully committed waiting transition is resumable. A crash while
            # preparing a question must not strand an invisible, unanswered form.
            db.execute("UPDATE user_help SET status='cancelled',resolved_at=? WHERE status='preparing'", (now(),))
            from .agents import recover_agents
            recover_agents(db)
        await self.call(recover)

    async def close(self):
        if self._connection is not None:
            await asyncio.get_running_loop().run_in_executor(self._pool, self._connection.close)
        self._pool.shutdown(wait=True)

    async def rows(self, sql, args=()):
        return await self.call(lambda db: [dict(row) for row in db.execute(sql, args).fetchall()])

    async def execute(self, sql, args=()):
        return await self.call(lambda db: db.execute(sql, args).rowcount)

    async def session(self, title="新的事情", user_id=None):
        identifier = uid()
        project = f"personal:{user_id}" if user_id else "personal"
        def create(db):
            db.execute("INSERT OR IGNORE INTO projects VALUES (?, '我的事情')", (project,))
            db.execute("INSERT INTO sessions(id,project_id,title,created_at,user_id) VALUES (?,?,?,?,?)", (identifier, project, title, now(), user_id))
        await self.call(create)
        return identifier

    @staticmethod
    def _message(db, session_id, run_id, role, content, status, tool_calls=None, tool_call_id=None):
        if role == "tool" and tool_call_id:
            existing = db.execute("SELECT id FROM messages WHERE run_id=? AND tool_call_id=?", (run_id, tool_call_id)).fetchone()
            if existing:
                return existing[0]
        identifier = uid()
        db.execute("INSERT INTO messages(id,session_id,run_id,role,content,status,tool_calls,tool_call_id,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (identifier, session_id, run_id, role, content, status, json.dumps(tool_calls) if tool_calls else None, tool_call_id, now()))
        return identifier

    async def message(self, session_id, run_id, role, content="", status="complete", **kwargs):
        return await self.call(lambda db: self._message(db, session_id, run_id, role, content, status, **kwargs))

    @staticmethod
    def _enqueue(db, session_id, text, attachments=None, *, origin=None, frontend_source=None):
        if origin is not None and frontend_source is not None:
            raise ValueError("Autonomous task inputs cannot inherit a browser source.")
        identifier = uid()
        db.execute("INSERT INTO runs(id,session_id,status,created_at) VALUES (?,?,'queued',?)", (identifier, session_id, now()))
        message_id = Store._message(db, session_id, identifier, "user", text, "queued")
        from .frontend_state import bind_source
        bind_source(db, session_id, identifier, message_id, frontend_source)
        if attachments:
            db.execute("UPDATE messages SET attachments=? WHERE id=?", (json.dumps(attachments, ensure_ascii=False), message_id))
        if origin is not None:
            db.execute('UPDATE messages SET origin=? WHERE id=?', (json.dumps(origin, ensure_ascii=False), message_id))
        from .memory import snapshot_note
        note = snapshot_note(db, session_id)
        if note:
            # Freeze recall with this input: later memory updates never rewrite a prefix.
            db.execute("UPDATE messages SET context_note=? WHERE id=?", (note, message_id))
        count = db.execute("SELECT COUNT(*) FROM messages WHERE session_id=? AND role='user' AND origin IS NULL AND help_reply IS NULL", (session_id,)).fetchone()[0]
        if origin is None and count == 1:
            db.execute("UPDATE sessions SET title=? WHERE id=? AND title='新的事情'", (text[:24], session_id))
        return identifier

    async def enqueue(self, session_id, text, attachments=None, *, frontend_source=None,
                      submission=None, admission_revision=None):
        if submission is None:
            return await self.call(lambda db: self._enqueue(db, session_id, text, attachments, frontend_source=frontend_source))
        from .submissions import accept
        return await self.call(lambda db: accept(db, session_id, text, attachments, frontend_source,
                                                submission, admission_revision))

    async def claim(self, session_id):
        def claim(db):
            if db.execute("SELECT 1 FROM runs WHERE session_id=? AND status IN ('running','waiting_user','waiting_agents')", (session_id,)).fetchone():
                return None
            row = db.execute("SELECT id FROM runs WHERE session_id=? AND status='queued' ORDER BY rowid LIMIT 1", (session_id,)).fetchone()
            if not row:
                return None
            job = db.execute('SELECT j.cancel_requested,r.status FROM agent_jobs j JOIN runs r ON r.id=j.parent_run_id WHERE j.child_run_id=?', (row[0],)).fetchone()
            if job and (job['cancel_requested'] or job['status'] not in ('running','queued','waiting_user','waiting_agents')):
                return None
            db.execute("UPDATE runs SET status='running' WHERE id=?", (row[0],))
            # A queued input has never entered the provider context. Admit it
            # after all prior output, preserving raw chronology and old prefixes.
            boundary = db.execute('SELECT COALESCE(MAX(seq),0) FROM messages').fetchone()[0]
            db.execute("UPDATE messages SET context_after_seq=? WHERE run_id=? AND status='queued'", (boundary, row[0]))
            db.execute("UPDATE messages SET status='complete' WHERE run_id=? AND status='queued'", (row[0],))
            return row[0]
        return await self.call(claim)

    @staticmethod
    def _history(db, session_id, depth=0):
        if depth > 100:
            raise ValueError("会话分支层级过深")
        session = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if session is None:
            raise ValueError("找不到这段对话")
        prior = []
        if session["parent_id"]:
            prior = [row for row in Store._history(db, session["parent_id"], depth + 1) if row["seq"] <= session["fork_seq"]]
        return prior + [dict(row) for row in db.execute("SELECT * FROM messages WHERE session_id=? ORDER BY seq", (session_id,))]

    async def history(self, session_id):
        return await self.call(lambda db: self._history(db, session_id))

    @staticmethod
    def _context_history(db, session_id):
        from .context_controls import apply_exclusions
        history = apply_exclusions(db, session_id, Store._history(db, session_id))
        return sorted(history, key=lambda row: (row['context_after_seq'] if row.get('context_after_seq') is not None else row['seq'],
                                               int(row.get('context_after_seq') is not None), row['seq']))

    async def context_history(self, session_id):
        return await self.call(lambda db: self._context_history(db, session_id))

    async def artifact(self, session_id, artifact_id):
        def visible(db):
            row = db.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
            if row is None:
                return None
            if row["session_id"] == session_id:
                return dict(row)
            runs = {item["run_id"] for item in self._history(db, session_id)}
            return dict(row) if row["run_id"] and row["run_id"] in runs else None
        return await self.call(visible)

    async def fork(self, session_id):
        def fork(db):
            if db.execute("SELECT 1 FROM runs WHERE session_id=? AND status IN ('running','queued','waiting_user','waiting_agents')", (session_id,)).fetchone():
                raise UserError("fork_busy", "请先停下当前任务，再从这里另开对话。")
            history = self._history(db, session_id)
            original = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            identifier = uid()
            db.execute("INSERT INTO sessions(id,project_id,title,parent_id,fork_seq,created_at,user_id,model_profile_id,model_epoch) VALUES (?,?,?,?,?,?,?,?,?)", (identifier, original["project_id"], original["title"] + " · 新方向", session_id, max((m["seq"] for m in history), default=0), now(), original["user_id"], original["model_profile_id"], original["model_epoch"]))
            db.execute("INSERT INTO components(session_id,id,version,title,html,active,created_at,file_path,placement,file_id) SELECT ?,id,version,title,html,active,created_at,file_path,placement,file_id FROM components WHERE session_id=?", (identifier, session_id))
            db.execute("INSERT INTO component_views SELECT ?,component_id,state,revision,updated_at FROM component_views WHERE session_id=?", (identifier, session_id))
            db.execute("INSERT INTO context_prefixes SELECT ?,epoch,boundary,prompt,created_at FROM context_prefixes WHERE session_id=?", (identifier, session_id))
            db.execute("INSERT INTO context_clips SELECT ?,message_id,content,created_at FROM context_clips WHERE session_id=?", (identifier, session_id))
            db.execute("INSERT INTO compactions SELECT lower(hex(randomblob(16))),?,through_seq,before_chars,after_chars,created_at FROM compactions WHERE session_id=?", (identifier, session_id))
            from .context_controls import copy_exclusions
            copy_exclusions(db, session_id, identifier)
            # Task definitions are copied paused. Occurrences and runs stay with their owner.
            for task in db.execute("SELECT * FROM tasks WHERE session_id=? AND status!='done'", (session_id,)).fetchall():
                db.execute("INSERT INTO tasks(id,session_id,title,prompt,due_at,interval_seconds,status,created_at,condition_command) VALUES (?,?,?,?,?,?,'paused',?,?)", (uid(), identifier, task["title"], task["prompt"], task["due_at"], task["interval_seconds"], now(), task["condition_command"]))
            from .memory import copy_session_memories
            copy_session_memories(db, session_id, identifier)
            return identifier
        return await self.call(fork)

    async def event(self, session_id, kind, payload):
        from .event_log import append_event
        return await self.call(lambda db: append_event(db, session_id, kind, payload, now()))

    async def stream_message(self, session_id, payload):
        from .event_log import append_event
        def write(db):
            changed = db.execute("UPDATE messages SET content=?,reasoning=? WHERE id=? AND session_id=? AND status='streaming'",
                                 (payload['content'], payload['reasoning'], payload['id'], session_id)).rowcount
            if changed != 1:
                raise ValueError('Message stream is no longer active in this conversation')
            return append_event(db, session_id, 'message', payload, now())
        return await self.call(write)

    async def events(self, session_id, after=0, limit=500):
        from .event_log import read_events
        return await self.call(lambda db: read_events(db, session_id, after, limit))

    async def components(self, session_id):
        from .component_views import active_components
        return await self.call(lambda db: active_components(db, session_id))
