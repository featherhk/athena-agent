# macOS desktop surface contract

The desktop is another host for the existing registered interaction surface.
No new model-facing tool, source-code editing permission, database table, or
schema version is introduced. Windows is reserved for a later host implementation.

## Desired state

`runtime registry kind:component action:register` (or the existing
`update_frontend`) accepts `placement:"desktop"`. Source versions remain in the
external user file area and the existing components history. They never become
native Swift code or receive direct access to a WK script-message handler.

`action:layout` still requires the current component `version` and
`view_revision`; it merges these optional view overrides on the SQLite owner
thread:

| Field | Desktop meaning |
| --- | --- |
| `hidden` | Desired folded state, preserved through source updates |
| `placement` | Override source placement; `null` restores its registered placement |
| `screen_id` | Observed native screen identifier; omission lets the host choose |
| `x`, `y` | Fractions of available travel, physical left/top = 0, right/bottom = 1 |
| `width`, `height` | Logical points, 32–16384; the host clamps to its actual screen |

The native origin is the usable-area origin plus the relative fraction times
`usableSize - clampedComponentSize`. Returned coordinates describe the result,
not a promise that the requested geometry could be used. Screen removal may
cause host fallback; the receipt must identify the actual screen.

Web placements retain their existing width/height bounds (240/160–1600 CSS
pixels), and do not accept `screen_id`. Switching back from a desktop placement
must clear or adjust incompatible overrides. Registration and source restore
also validate the effective merged placement before committing a new version.
A rejected source change removes its unregistered file.

Forks inherit desired component versions/layouts but no frontend or native
observations. Source refresh does not unfold a hidden component. Existing reset
disables registered components, retains their history/files, and clears layouts.

## Observed state

The authenticated `/api/frontend/observation` endpoint accepts optional
`desktop`. Its absence is preserved when encoding old-client heartbeat
signatures. The entire existing observation remains bounded to 24 KiB and uses
the existing revision, authentication identity, session ownership, and 75-second
freshness lease.

```json
{
  "desktop": {
    "platform": "macos",
    "supported": true,
    "presented": true,
    "screens": [{
      "id": "1",
      "frame": {"x": 0, "y": 0, "width": 1728, "height": 1117},
      "visible_frame": {"x": 0, "y": 37, "width": 1728, "height": 1015.5},
      "scale": 2
    }],
    "components": [{
      "id": "collector", "version": 1, "view_revision": 2,
      "state": "mounted", "visible": true, "screen_id": "1",
      "rect": {"x": 120.5, "y": 32, "width": 80, "height": 64},
      "error_code": null
    }]
  }
}
```

All geometry uses logical points, not physical pixels. Screen frames use a
global top-left coordinate system; a component's `rect` is relative to its
screen's `visible_frame` top-left. Fractional points are valid. Lists are bounded
to 16 screens and 32 components, with unique IDs; every component must refer to
one of the reported screens.

Component states are `loading`, `mounted`, `hidden`, and `failed`. `visible` is
independent of successful loading; `hidden` must be false for visibility. Failed
components require one of the bounded native/document error codes. An observation
may truthfully contain an old component version during a refresh, but its source
version must exist in this owned session and its layout revision cannot be in the
future. Observations cannot import another account's or session's components.

For web/iOS, report `supported:false`, `presented:false`, and empty lists. They
must not create chat cards for desktop placements as a silent fallback. Only
macOS can claim desktop support in the current schema. An old client that omits
the field has unknown capability, not desktop support.

`app list` exposes just `{platform,supported,presented}` in each client's
`desktop` summary for inexpensive host discovery. `app snapshot` exposes the
full `state.desktop` alongside `desktop_registration`, the desired current
components/layouts without their HTML. `registry read/list` document reports now
state `render.scope:"document_load_only"`. HTML document loading, native window
presentation, and successful task effects are separate facts.

These are client-reported semantic observations, not screenshots, attestation,
or proof of functional success. The model must compare source/layout revisions
and freshness before reporting a display change as observed. Hidden presentation
must not be treated as user attention or cancellation of running work.
In this version the native host hides the companion and all generated panels
together. A `presented:false` native state therefore cannot claim visible
components or `foreground:true`, even if its WKWebView still considers its
document visible. Contradictory observations are rejected before persistence.

## Backend validation

`tests/test_desktop_surfaces.py` exercises the actual runtime envelope and HTTP
observation endpoint, durable layout events, immutable source versions, fork
isolation, ownership, unsupported hosts, revision races, native failures, old
heartbeat compatibility, and incompatible geometry on placement/source changes.
The native widget page additionally validates its host marker, URL scope,
authenticated owner, source version, and effective placement before rendering
the existing sandboxed Widget. It opens no independent event stream and relays
user interactions through the native parent. `desktop-widget-loader.test.mjs`
and `desktop-widget-page.test.mjs` cover that path, including late cancellation,
owner/source changes, single-write layout conflicts, and retention of the same
document draft/selection when a layout request fails.
These tests validate the contract; they do not constitute native window,
accessibility, voice, or physical multi-monitor acceptance. Native and web-host
receipts must be evaluated separately before distribution.
