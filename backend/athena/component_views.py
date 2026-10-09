"""Durable host layout, independent of agent-authored component source versions."""
import json
import math
import re

from .errors import UserError
from .store import now


def read_view(db, session_id, component_id):
    row = db.execute("SELECT * FROM component_views WHERE session_id=? AND component_id=?", (session_id, component_id)).fetchone()
    return {"view": json.loads(row["state"]) if row else {},
            "view_revision": row["revision"] if row else 0,
            "view_updated_at": row["updated_at"] if row else None}


def active_components(db, session_id, *, include_source=True):
    # Include folded surfaces: the independent shell can always reopen them.
    # Selection/observation only needs metadata, not every potentially large HTML
    # document. Join layouts once rather than querying once per component.
    columns = 'c.*' if include_source else 'c.id,c.version,c.placement,c.created_at'
    rows = db.execute(f"""SELECT {columns},v.state AS _view_state,v.revision AS view_revision,v.updated_at AS view_updated_at
        FROM components c LEFT JOIN component_views v ON v.session_id=c.session_id AND v.component_id=c.id
        WHERE c.session_id=? AND c.version=(SELECT MAX(version) FROM components n WHERE n.session_id=c.session_id AND n.id=c.id)
        AND c.active=1 ORDER BY c.created_at,c.id""", (session_id,)).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item['view'] = json.loads(item.pop('_view_state') or '{}')
        item['view_revision'] = item['view_revision'] or 0
        items.append(item)
    candidates = [item for item in items if item['view'].get('placement', item['placement']) == 'surface'
                  and not item['view'].get('hidden', False)]
    selected = max(candidates, key=lambda item: (max(item['created_at'], item['view_updated_at'] or ''), item['id']), default=None)
    for item in items:
        item['surface_selected'] = item is selected
    return items


def surface_registration(db, session_id):
    """Desired renderer, separate from any browser's actual rendering receipt."""
    selected = next((item for item in active_components(db, session_id, include_source=False) if item['surface_selected']), None)
    if selected is None:
        return {'provider': 'default'}
    return {'provider': 'component', 'id': selected['id'], 'version': selected['version'],
            'view_revision': selected['view_revision']}


def surface_selected(db, session_id, component_id, version):
    selected = surface_registration(db, session_id)
    return selected.get('id') == component_id and selected.get('version') == version


def desktop_registration(db, session_id):
    """Desired native surfaces; registration is never evidence of display."""
    if session_id is None:
        return []
    return [{key: item[key] for key in ('id', 'version', 'view_revision', 'view')}
            for item in active_components(db, session_id, include_source=False)
            if item['view'].get('placement', item['placement']) == 'desktop']


def validate_view(change, *, placement=None):
    from .registry import COMPONENT_PLACEMENTS
    if not isinstance(change, dict) or (not change and placement is None) or set(change) - {"hidden", "placement", "x", "y", "width", "height", "screen_id"}:
        raise UserError("invalid_input", "Supply supported component layout fields.")
    for key, value in change.items():
        if value is None:
            continue  # Clear an override, returning to the component's default.
        if key == "hidden":
            valid = type(value) is bool
        elif key == "placement":
            valid = value in COMPONENT_PLACEMENTS
        elif key == "screen_id":
            valid = isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', value) is not None
            if placement is not None:
                valid = valid and placement == 'desktop'
        else:
            if key in ("x", "y"):
                lower, upper = 0, 1
            elif placement is None or placement == 'desktop':
                lower, upper = 32, 16384
            else:
                lower, upper = (240, 1600) if key == "width" else (160, 1600)
            valid = type(value) in (int, float) and math.isfinite(value) and lower <= value <= upper
        if not valid:
            raise UserError("invalid_input", "Invalid component layout value.")


async def update_view(store, session_id, component_id, version, revision, change):
    validate_view(change)
    if type(version) is not int or version < 1 or type(revision) is not int or revision < 0:
        raise UserError("invalid_input", "Read the current component version and view_revision before changing its layout.")
    def update(db):
        component = db.execute("SELECT version,active,placement FROM components WHERE session_id=? AND id=? ORDER BY version DESC LIMIT 1", (session_id, component_id)).fetchone()
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
        # Validate the merged, effective placement on the SQLite owner thread.
        # A concurrent layout cannot turn small desktop geometry into a browser
        # layout, and clearing an override returns to the registered placement.
        validate_view(state, placement=state.get('placement', component['placement']))
        # Even an identical 'open' brings an existing workspace to the front.
        stamp = now()
        db.execute("INSERT INTO component_views VALUES (?,?,?,?,?) ON CONFLICT(session_id,component_id) DO UPDATE SET state=excluded.state,revision=excluded.revision,updated_at=excluded.updated_at",
                   (session_id, component_id, json.dumps(state), revision + 1, stamp))
        result = {"id": component_id, "version": version, "view": state, "view_revision": revision + 1, "view_updated_at": stamp,
                  "surface_selected": surface_selected(db, session_id, component_id, version)}
        # Audit and refresh are committed in the same transaction as the layout.
        db.execute("INSERT INTO events(session_id,type,payload,created_at) VALUES (?,?,?,?)",
                   (session_id, "component_view", json.dumps(result), stamp))
        return result
    return await store.call(update)
