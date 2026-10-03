"""Internal validation; kept out of the model's always-present tool definition."""
from .preferences import preference_schema
from .registry import COMPONENT_PLACEMENTS
from .app_controls import TARGETS

RUNTIME_PROPERTIES = {
        "op": {"type": "string", "enum": ["skill", "registry", "account", "app", "sessions", "context", "compact", "usage", "app_settings", "user_help", "schedule", "tasks", "cancel_task", "memory", "remember", "recall", "read_artifact", "read_message", "register_tool", "remove_tool", "state", "models", "switch_model"]},
        "questions": {"type": "array", "minItems": 1, "maxItems": 3, "items": {
            "type": "object", "properties": {
                "id": {"type": "string", "pattern": "^[a-zA-Z0-9_-]{1,64}$"},
                "question": {"type": "string", "minLength": 1, "maxLength": 1000},
                "options": {"type": "array", "maxItems": 6, "items": {
                    "type": "object", "properties": {"label": {"type": "string", "minLength": 1, "maxLength": 200},
                        "description": {"type": "string", "maxLength": 300}},
                    "required": ["label"], "additionalProperties": False}},
            }, "required": ["id", "question"], "additionalProperties": False}},
        "kind": {"type": "string", "enum": ["tool", "component", "skill", "policy"]},
        "action": {"type": "string", "enum": ["add", "forget", "history", "list", "read", "new", "rename", "fork", "exclude_run", "restore_run", "register", "restore", "remove", "layout", "update", "change_password", "open", "close", "navigate", "preview", "download", "attach", "detach", "sign_out", "stop", "reset"]},
        "memory_id": {"type": "string", "maxLength": 64},
        "restore_version": {"type": "integer", "minimum": 1},
        "include_forgotten": {"type": "boolean"},
        "target": {"type": "string", "enum": list(TARGETS)},
        "file_id": {"type": "string", "maxLength": 64},
        "session_id": {"type": "string", "maxLength": 64}, "run_id": {"type": "string", "maxLength": 64},
        "profile": {"type": "object", "properties": {"display_name": {"type": "string", "maxLength": 60},
            "avatar_file_id": {"type": "string"}}, "additionalProperties": False},
        "id": {"type": "string", "pattern": "^[a-zA-Z0-9_-]{1,64}$"},
        "version": {"type": "integer", "minimum": 1},
        "path": {"type": "string", "maxLength": 240},
        "files": {"type": "object", "maxProperties": 64, "additionalProperties": {"anyOf": [
            {"type": "string"}, {"type": "object", "properties": {"encoding": {"type": "string", "enum": ["base64"]},
                "content": {"type": "string"}}, "required": ["encoding", "content"], "additionalProperties": False}]}},
        "placement": {"type": "string", "enum": list(COMPONENT_PLACEMENTS)},
        "view_revision": {"type": "integer", "minimum": 0},
        "view": {"type": "object", "minProperties": 1, "properties": {
            "hidden": {"type": ["boolean", "null"]},
            "placement": {"enum": [*COMPONENT_PLACEMENTS, None]},
            "x": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
            "y": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
            "width": {"type": ["number", "null"], "minimum": 240, "maximum": 1600},
            "height": {"type": ["number", "null"], "minimum": 160, "maximum": 1600},
        }, "additionalProperties": False},
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

# Browser contracts are discovered through its skill, not expanded in the stable
# model-facing runtime envelope. Individual operations validate allowed fields.
RUNTIME_PROPERTIES['op']['enum'].append('browser')
RUNTIME_PROPERTIES['op']['enum'].append('capabilities')
for action in ('status', 'snapshot', 'click', 'fill', 'press', 'select', 'check', 'uncheck',
               'scroll', 'back', 'forward', 'reload', 'screenshot', 'handoff'):
    if action not in RUNTIME_PROPERTIES['action']['enum']:
        RUNTIME_PROPERTIES['action']['enum'].append(action)
RUNTIME_PROPERTIES.update({
    'tab_id': {'type': 'string', 'maxLength': 64},
    'observation_id': {'type': 'string', 'maxLength': 64},
    'url': {'type': 'string', 'maxLength': 4096},
    'ref': {'type': 'string', 'pattern': '^@e[0-9]+$'},
    'text': {'type': 'string', 'maxLength': 10000},
    'value': {'type': 'string', 'maxLength': 1000},
    'key': {'type': 'string', 'maxLength': 60},
    'direction': {'enum': ['up', 'down', 'left', 'right']},
    'pixels': {'type': 'integer', 'minimum': 1, 'maximum': 3000},
})

RUNTIME_PROPERTIES['op']['enum'].append('agents')
RUNTIME_PROPERTIES['action']['enum'].extend(['spawn', 'wait', 'cancel'])
RUNTIME_PROPERTIES.update({
    'agent_id': {'type': 'string', 'maxLength': 64},
    'agent_ids': {'type': 'array', 'minItems': 1, 'maxItems': 8, 'uniqueItems': True,
                  'items': {'type': 'string', 'maxLength': 64}},
    'context': {'type': 'string', 'maxLength': 30000},
    'mode': {'enum': ['all', 'any']},
})
