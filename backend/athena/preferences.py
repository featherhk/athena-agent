"""Account preferences with transactional, replayable change events."""
import json
from .store import now


UI_LANGUAGES = ("ar", "en", "es", "fr", "ja", "ko", "pt", "zh-Hans", "zh-Hant")
_FIELDS = {
    "ui_language": (("system", *UI_LANGUAGES), "system", "界面语言；system 跟随设备。不改变回复语言。"),
    "ui_font": (("system", "humanist", "serif", "mono"), "system", "界面字体：系统、人文无衬线、衬线或等宽。"),
    "ui_palette": (("dream", "rose", "ocean", "forest"), "dream", "界面配色：梦幻、玫瑰、海洋或森林。"),
    "reduced_motion": (("system", "reduce"), "system", "动效：跟随设备偏好，或减少动画。"),
}
DEFAULT_PREFERENCES = {name: definition[1] for name, definition in _FIELDS.items()}


def preference_schema():
    """A detached discovery/validation schema shared by tools and the client API."""
    return {
        "type": "object",
        "properties": {
            name: {"type": "string", "enum": list(choices), "default": default, "description": description}
            for name, (choices, default, description) in _FIELDS.items()
        },
        "additionalProperties": False,
    }


def _validate(changes):
    if not isinstance(changes, dict) or changes.keys() - DEFAULT_PREFERENCES.keys():
        raise ValueError("Unsupported application preference")
    for name, value in changes.items():
        if not isinstance(value, str) or value not in _FIELDS[name][0]:
            raise ValueError("Unsupported application preference value")
    return dict(changes)


def _read(db, user_id):
    if not user_id or not db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
        raise ValueError("Application preferences require an account")
    row = db.execute("SELECT value FROM settings WHERE key=?", (f"user_preferences:{user_id}",)).fetchone()
    saved = _validate(json.loads(row[0])) if row else {}
    return {**DEFAULT_PREFERENCES, **saved}


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
