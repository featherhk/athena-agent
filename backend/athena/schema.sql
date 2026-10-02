PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
 password_hash TEXT NOT NULL, role TEXT NOT NULL, developer_mode INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_sessions (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_attempts (
 username TEXT PRIMARY KEY, attempts INTEGER NOT NULL DEFAULT 0, last_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, title TEXT NOT NULL);
INSERT OR IGNORE INTO projects VALUES ('personal', '我的事情');
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), title TEXT NOT NULL,
 parent_id TEXT REFERENCES sessions(id), fork_seq INTEGER, created_at TEXT NOT NULL,
 user_id TEXT REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS messages (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
 session_id TEXT NOT NULL REFERENCES sessions(id), run_id TEXT REFERENCES runs(id),
 role TEXT NOT NULL, content TEXT NOT NULL DEFAULT '', reasoning TEXT NOT NULL DEFAULT '',
 tool_calls TEXT, tool_call_id TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), model TEXT NOT NULL,
 input TEXT NOT NULL, status TEXT NOT NULL, response TEXT, usage TEXT, error TEXT,
 created_at TEXT NOT NULL, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES sessions(id),
 type TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_session ON events(session_id, seq);
CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id, seq);
CREATE TABLE IF NOT EXISTS artifacts (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 run_id TEXT, path TEXT NOT NULL, size INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS components (
 session_id TEXT NOT NULL REFERENCES sessions(id), id TEXT NOT NULL, version INTEGER NOT NULL,
 title TEXT NOT NULL, html TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
 file_path TEXT,
 PRIMARY KEY(session_id, id, version)
);
CREATE TABLE IF NOT EXISTS component_views (
 session_id TEXT NOT NULL REFERENCES sessions(id), component_id TEXT NOT NULL,
 state TEXT NOT NULL, revision INTEGER NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(session_id,component_id)
);
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), title TEXT NOT NULL,
 prompt TEXT NOT NULL, due_at TEXT NOT NULL, interval_seconds INTEGER, status TEXT NOT NULL,
 occurrence INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
 condition_command TEXT, last_error TEXT
);
CREATE TABLE IF NOT EXISTS task_checks (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
 artifact_id TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS wakes (
 task_id TEXT NOT NULL REFERENCES tasks(id), occurrence INTEGER NOT NULL,
 run_id TEXT NOT NULL REFERENCES runs(id), PRIMARY KEY(task_id, occurrence)
);
CREATE TABLE IF NOT EXISTS memories (
 id TEXT PRIMARY KEY, scope TEXT NOT NULL, owner_id TEXT NOT NULL, content TEXT NOT NULL,
 source_run_id TEXT REFERENCES runs(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_revisions (
 memory_id TEXT NOT NULL REFERENCES memories(id), version INTEGER NOT NULL,
 content TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('active','forgotten')),
 action TEXT NOT NULL, source_session_id TEXT REFERENCES sessions(id),
 source_run_id TEXT REFERENCES runs(id), source_memory_id TEXT REFERENCES memories(id),
 source_version INTEGER, created_at TEXT NOT NULL,
 PRIMARY KEY(memory_id,version)
);
CREATE TABLE IF NOT EXISTS browser_instances (
 user_id TEXT PRIMARY KEY REFERENCES users(id), identity TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS browser_tabs (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 session_id TEXT NOT NULL REFERENCES sessions(id), target_id TEXT NOT NULL,
 status TEXT NOT NULL, url TEXT NOT NULL, observation_id TEXT,
 refs TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS browser_actions (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 run_id TEXT REFERENCES runs(id), tab_id TEXT REFERENCES browser_tabs(id),
 action TEXT NOT NULL, arguments TEXT NOT NULL, status TEXT NOT NULL,
 result TEXT, error_code TEXT, created_at TEXT NOT NULL, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS context_clips (
 session_id TEXT NOT NULL REFERENCES sessions(id), message_id TEXT NOT NULL REFERENCES messages(id),
 content TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(session_id, message_id)
);
CREATE TABLE IF NOT EXISTS compactions (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 through_seq INTEGER NOT NULL, before_chars INTEGER NOT NULL, after_chars INTEGER NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extensions (
 user_id TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL, version INTEGER NOT NULL,
 description TEXT NOT NULL, parameters TEXT NOT NULL, file_path TEXT NOT NULL,
 active INTEGER NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(user_id, name, version)
);
CREATE TABLE IF NOT EXISTS resets (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_profiles (
 id TEXT PRIMARY KEY, definition TEXT NOT NULL, enabled INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_switches (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), run_id TEXT REFERENCES runs(id),
 from_profile TEXT NOT NULL, to_profile TEXT NOT NULL, context_epoch INTEGER NOT NULL,
 reason TEXT NOT NULL, profile_snapshot TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_help (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 run_id TEXT NOT NULL REFERENCES runs(id), status TEXT NOT NULL,
 questions TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', answers TEXT,
 attachments TEXT NOT NULL DEFAULT '[]',
 created_at TEXT NOT NULL, resolved_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS user_help_open_session
 ON user_help(session_id) WHERE status IN ('preparing','pending');
