"""Account preferences with transactional, replayable change events."""
import json
import re
from .store import now


UI_LANGUAGES = ("ar", "en", "es", "fr", "ja", "ko", "pt", "zh-Hans", "zh-Hant")
_FIELDS = {
    "ui_language": (("system", *UI_LANGUAGES), "system", "界面语言；system 跟随设备。不改变回复语言。"),
    "ui_font": (("system", "humanist", "serif", "mono"), "system", "界面字体：Athena 默认书卷衬线、人文无衬线、书籍衬线或等宽；system 为 Athena 默认，中文优先宋体。"),
    "ui_palette": (("dream", "rose", "ocean", "forest"), "dream", "界面配色：可用预设，或任意 #RGB / #RRGGBB 背景色。黑色用 #000000；文本、控件和面板自动适配对比度，预设不是颜色限制。"),
    "reduced_motion": (("system", "reduce"), "system", "动效：跟随设备偏好，或减少动画。"),
}
DEFAULT_PREFERENCES = {name: definition[1] for name, definition in _FIELDS.items()}
DEFAULT_PREFERENCES["presence_size"] = 84
DEFAULT_PREFERENCES["developer_mode"] = False


def preference_schema():
    """A detached discovery/validation schema shared by tools and the client API."""
    fields = {
        name: {"type": "string", "enum": list(choices), "default": default, "description": description}
        for name, (choices, default, description) in _FIELDS.items()
    }
    presets = fields["ui_palette"].pop("enum")
    fields["ui_palette"]["anyOf"] = [
        {"enum": presets},
        {"pattern": r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$"},
    ]
    fields["presence_size"] = {
        "type": "integer", "minimum": 32, "maximum": 240, "default": 84,
        "description": "顶部 Athena 标识展开时的尺寸（像素，32–240）；阅读时自动收起。任意样式用 update_frontend 注册 presence 位置的组件。",
    }
    fields["developer_mode"] = {
        "type": "boolean", "default": False,
        "description": "打开或关闭 Athena 的开发者用量浮窗，显示 token、费用和缓存率。不是浏览器或操作系统的开发者工具。",
    }
    return {
        "type": "object",
        "properties": fields,
        "additionalProperties": False,
    }


def _validate(changes):
    if not isinstance(changes, dict) or changes.keys() - DEFAULT_PREFERENCES.keys():
        raise ValueError("Unsupported application preference")
    validated = dict(changes)
    for name, value in changes.items():
        if name == "developer_mode":
            if type(value) is not bool:
                raise ValueError("Developer mode must be true or false")
            continue
        if name == "presence_size":
            if type(value) is not int or not 32 <= value <= 240:
                raise ValueError("Presence size must be an integer between 32 and 240 pixels")
            continue
        if name == "ui_palette" and isinstance(value, str) and re.fullmatch(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})", value):
            color = value[1:].lower()
            validated[name] = "#" + ("".join(char * 2 for char in color) if len(color) == 3 else color)
            continue
        if not isinstance(value, str) or value not in _FIELDS[name][0]:
            raise ValueError("Unsupported application preference value")
    return validated


def _read(db, user_id):
    user = db.execute("SELECT developer_mode FROM users WHERE id=?", (user_id,)).fetchone() if user_id else None
    if user is None:
        raise ValueError("Application preferences require an account")
    row = db.execute("SELECT value FROM settings WHERE key=?", (f"user_preferences:{user_id}",)).fetchone()
    saved = _validate(json.loads(row[0])) if row else {}
    return {**DEFAULT_PREFERENCES, **saved, "developer_mode": bool(user["developer_mode"])}


async def read_preferences(store, user_id):
    return await store.call(lambda db: _read(db, user_id))


async def update_preferences(store, user_id, changes, source_run_id=None):
    changes = _validate(changes)

    def update(db):
        previous = _read(db, user_id)
        if source_run_id is not None and not db.execute(
            "SELECT 1 FROM runs r JOIN sessions s ON s.id=r.session_id WHERE r.id=? AND s.user_id=?",
            (source_run_id, user_id),
        ).fetchone():
            raise ValueError("Preference source does not belong to this account")
        preferences = {**previous, **changes}
        if preferences == previous:
            return preferences
        if "developer_mode" in changes:
            db.execute("UPDATE users SET developer_mode=? WHERE id=?", (int(preferences["developer_mode"]), user_id))
        db.execute(
            "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (f"user_preferences:{user_id}", json.dumps(preferences)),
        )
        payload = json.dumps({**preferences, "source_run_id": source_run_id})
        timestamp = now()
        db.execute(
            "INSERT INTO events(session_id,type,payload,created_at) "
            "SELECT id,'preferences_changed',?,? FROM sessions WHERE user_id=?",
            (payload, timestamp, user_id),
        )
        return preferences

    return await store.call(update)
