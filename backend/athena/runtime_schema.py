"""Internal validation; kept out of the model's always-present tool definition."""
import json
import re
import jsonschema
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
        "client_id": {"type": "string", "pattern": "^[a-zA-Z0-9_-]{1,64}$"},
        "expected_revision": {"type": "integer", "minimum": 1},
        "view_revision": {"type": "integer", "minimum": 0},
        "view": {"type": "object", "minProperties": 1, "properties": {
            "hidden": {"type": ["boolean", "null"]},
            "placement": {"enum": [*COMPONENT_PLACEMENTS, None]},
            "x": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
            "y": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
            "width": {"type": ["number", "null"], "minimum": 32, "maximum": 16384},
            "height": {"type": ["number", "null"], "minimum": 32, "maximum": 16384},
            "screen_id": {"type": ["string", "null"], "pattern": "^[a-zA-Z0-9_-]{1,64}$"},
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

SKILL_SCHEMA = {"type": "object", "properties": {
    **{key: RUNTIME_PROPERTIES[key] for key in ("op", "path", "offset", "limit")},
    "name": {"type": "string", "pattern": "^[a-z][a-z0-9_-]{0,63}$"},
}, "required": ["op", "name"], "additionalProperties": False}


def validate_arguments(value, schema, *, location="arguments", operation=None):
    """Return repairable contract errors without reflecting argument values or raw schemas."""
    try:
        jsonschema.validate(value, schema)
    except jsonschema.ValidationError as error:
        def fields(values):
            return [item for item in values if isinstance(item, str) and re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]{0,63}", item)][:16]
        cursor = schema
        for part in error.absolute_path:
            if isinstance(part, str) and part in cursor.get("properties", {}) and fields([part]):
                location += "." + part
                cursor = cursor["properties"][part]
            elif type(part) is int and isinstance(cursor.get("items"), dict):
                location += "[]"
                cursor = cursor["items"]
            else:
                break
        rule = error.validator
        if rule == "required":
            missing = fields(key for key in error.validator_value if not isinstance(error.instance, dict) or key not in error.instance)
            reason = "Required fields: " + ", ".join(missing)
        elif rule == "additionalProperties":
            reason = "Unexpected fields. Allowed fields here: " + ", ".join(fields(error.schema.get("properties", {})))
        elif rule == "type":
            reason = "Expected type: " + json.dumps(error.validator_value)
        elif rule == "enum":
            reason = "Use an allowed value from the operation's skill or tool definition"
        elif rule in ("minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"):
            reason = f"{rule}: {error.validator_value}"
        else:
            reason = "Value does not match the field's documented format"
        hint = (' Read skill resources through runtime {"op":"skill","args":{"name":"desktop","path":"SKILL.md"}}; '
                'replace name with the skill name and path with its relative resource path. Optional fields: offset, limit.'
                if operation == "skill" else " Read the relevant skill or tool definition for the accepted fields.")
        raise ValueError(f"Invalid tool arguments at {location}: {reason}. No action was executed.{hint}") from None

# Browser contracts are discovered through its skill, not expanded in the stable
# model-facing runtime envelope. Individual operations validate allowed fields.
RUNTIME_PROPERTIES['op']['enum'].append('browser')
RUNTIME_PROPERTIES['op']['enum'].append('capabilities')
for action in ('status', 'snapshot', 'click', 'fill', 'press', 'select', 'check', 'uncheck', 'upload',
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
    'file_ids': {'type': 'array', 'minItems': 1, 'maxItems': 10, 'uniqueItems': True,
                 'items': {'type': 'string', 'pattern': '^[a-zA-Z0-9_-]{1,64}$'}},
})

RUNTIME_PROPERTIES['op']['enum'].append('agents')
RUNTIME_PROPERTIES['op']['enum'].append('activity')
RUNTIME_PROPERTIES['op']['enum'].append('files')
RUNTIME_PROPERTIES.update({
    'content_revision': {'type': 'string', 'pattern': '^s1-[a-f0-9]{64}$'},
    'sheet_index': {'type': 'integer', 'minimum': 0, 'maximum': 5},
    'column_offset': {'type': 'integer', 'minimum': 0, 'maximum': 39},
    'column_limit': {'type': 'integer', 'minimum': 1, 'maximum': 40},
})
RUNTIME_PROPERTIES['action']['enum'].extend(['spawn', 'wait', 'cancel'])
RUNTIME_PROPERTIES['action']['enum'].append('ack')
RUNTIME_PROPERTIES.update({
    'attention_key': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'},
    'agent_id': {'type': 'string', 'maxLength': 64},
    'agent_ids': {'type': 'array', 'minItems': 1, 'maxItems': 8, 'uniqueItems': True,
                  'items': {'type': 'string', 'maxLength': 64}},
    'context': {'type': 'string', 'maxLength': 30000},
    'mode': {'enum': ['all', 'any']},
})
