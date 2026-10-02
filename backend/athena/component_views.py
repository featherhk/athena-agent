"""Durable host layout, independent of agent-authored component source versions."""
import json
import math

from .errors import UserError
from .store import now


def read_view(db, session_id, component_id):
    row = db.execute("SELECT * FROM component_views WHERE session_id=? AND component_id=?", (session_id, component_id)).fetchone()
    return {"view": json.loads(row["state"]) if row else {},
            "view_revision": row["revision"] if row else 0,
            "view_updated_at": row["updated_at"] if row else None}


def active_components(db, session_id):
    # Include folded surfaces: the independent shell can always reopen them.
    rows = db.execute("SELECT c.* FROM components c WHERE session_id=? AND version=(SELECT MAX(version) FROM components n WHERE n.session_id=c.session_id AND n.id=c.id) AND active=1 ORDER BY created_at,id", (session_id,)).fetchall()
    return [{**dict(row), **read_view(db, session_id, row["id"])} for row in rows]


def validate_view(change):
    from .registry import COMPONENT_PLACEMENTS
    if not isinstance(change, dict) or not change or set(change) - {"hidden", "placement", "x", "y", "width", "height"}:
        raise UserError("invalid_input", "Supply supported component layout fields.")
    for key, value in change.items():
        if value is None:
            continue  # Clear an override, returning to the component's default.
        if key == "hidden":
            valid = type(value) is bool
        elif key == "placement":
            valid = value in COMPONENT_PLACEMENTS
        else:
            lower, upper = (0, 1) if key in ("x", "y") else (240, 1600) if key == "width" else (160, 1600)
            valid = type(value) in (int, float) and math.isfinite(value) and lower <= value <= upper
        if not valid:
            raise UserError("invalid_input", "Invalid component layout value.")


async def update_view(store, session_id, component_id, version, revision, change):
    validate_view(change)
    if type(version) is not int or version < 1 or type(revision) is not int or revision < 0:
        raise UserError("invalid_input", "Read the current component version and view_revision before changing its layout.")
    def update(db):
        component = db.execute("SELECT version,active FROM components WHERE session_id=? AND id=? ORDER BY version DESC LIMIT 1", (session_id, component_id)).fetchone()
        if component is None or not component['active']:
            raise UserError("not_found", "This component is no longer active.")
        current = read_view(db, session_id, component_id)
        if component['version'] != version or current['view_revision'] != revision:
            raise UserError("component_changed", "This component changed. Read its current layout before retrying.")
        state = {**current['view']}
        for key, value in change.items():
            if value is None:
                state.pop(key, None)
            else:
                state[key] = value
        # Even an identical 'open' brings an existing workspace to the front.
        stamp = now()
        db.execute("INSERT INTO component_views VALUES (?,?,?,?,?) ON CONFLICT(session_id,component_id) DO UPDATE SET state=excluded.state,revision=excluded.revision,updated_at=excluded.updated_at",
                   (session_id, component_id, json.dumps(state), revision + 1, stamp))
        result = {"id": component_id, "version": version, "view": state, "view_revision": revision + 1, "view_updated_at": stamp}
        # Audit and refresh are committed in the same transaction as the layout.
        db.execute("INSERT INTO events(session_id,type,payload,created_at) VALUES (?,?,?,?)",
                   (session_id, "component_view", json.dumps(result), stamp))
        return result
    return await store.call(update)
