"""Internal validation; kept out of the model's always-present tool definition."""
from .preferences import preference_schema

RUNTIME_PROPERTIES = {
        "op": {"type": "string", "enum": ["skill", "registry", "account", "sessions", "context", "compact", "usage", "app_settings", "schedule", "tasks", "cancel_task", "remember", "recall", "read_artifact", "read_message", "register_tool", "remove_tool", "state", "models", "switch_model"]},
        "kind": {"type": "string", "enum": ["tool", "component", "skill", "policy"]},
        "action": {"type": "string", "enum": ["list", "read", "new", "rename", "fork", "exclude_run", "restore_run", "register", "restore", "remove", "update", "change_password"]},
        "session_id": {"type": "string", "maxLength": 64}, "run_id": {"type": "string", "maxLength": 64},
        "profile": {"type": "object", "properties": {"display_name": {"type": "string", "maxLength": 60},
            "avatar_file_id": {"type": "string"}}, "additionalProperties": False},
        "id": {"type": "string", "pattern": "^[a-zA-Z0-9_-]{1,64}$"},
        "version": {"type": "integer", "minimum": 1},
        "path": {"type": "string", "maxLength": 240},
        "files": {"type": "object", "maxProperties": 64, "additionalProperties": {"anyOf": [
            {"type": "string"}, {"type": "object", "properties": {"encoding": {"type": "string", "enum": ["base64"]},
                "content": {"type": "string"}}, "required": ["encoding", "content"], "additionalProperties": False}]}},
        "placement": {"type": "string", "enum": ["inline", "workspace", "banner"]},
        "window_seconds": {"type": "integer", "minimum": 1, "maximum": 31536000},
        "target_tokens": {"type": "integer", "minimum": 1000, "maximum": 1000000},
        "settings": preference_schema(),
        "profile_id": {"type": "string", "maxLength": 64}, "reason": {"type": "string", "maxLength": 500},
        "name": {"type": "string"}, "title": {"type": "string", "maxLength": 100},
        "prompt": {"type": "string", "maxLength": 10000}, "due_at": {"type": "string"},
        "condition_command": {"type": "string", "maxLength": 30000},
        "interval_seconds": {"type": "integer", "minimum": 60}, "task_id": {"type": "string"},
        "scope": {"type": "string", "enum": ["global", "project", "session", "account"]},
        "content": {"type": "string", "maxLength": 4000}, "query": {"type": "string", "maxLength": 300},
        "artifact_id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
        "message_id": {"type": "string"},
        "description": {"type": "string", "maxLength": 300},
        "parameters": {"type": "object"}, "source": {"type": "string", "maxLength": 100000},
        "limit": {"type": "integer", "minimum": 1, "maximum": 20000},
    }

RUNTIME_SCHEMA = {"type": "object", "properties": RUNTIME_PROPERTIES, "required": ["op"], "additionalProperties": False}
