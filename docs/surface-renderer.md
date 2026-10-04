# Replaceable session surfaces

Athena's conversation UI is a communication surface. A session can register a
complete renderer, including its own layout, typography, motion and input,
without modifying the application checkout. The independent host retains
account ownership, canonical messages, execution and recovery.

## Freedom inside the surface

The renderer owns ordinary HTML, CSS, SVG and Canvas inside the full session work
area. Coordinates, rotation, scale, opacity, typography, clipping and animation
are authored in that document; they do not require a new host effect enum or a
model call for each frame. A renderer can lay out canonical messages itself,
measure its own text nodes, and move them around a drawn object. For example, a
shark can displace the rendered words while the saved message text remains
unchanged. CSS transforms and Canvas drawing here are not restricted by the
separate bounded `Athena.styleText` helper for decorating the default renderer.

The geometric boundary is the session work area. Independent account, stop,
recovery, attachment and voice controls remain outside it. Resource access still
follows the iframe CSP: this freedom does not imply remote scripts, arbitrary
network requests, online fonts or access to the parent DOM. A convincing drawing
also does not establish that a backend operation or external service exists.
The bridge's explicit host-action list is the current implemented capability
boundary. Rendering loops stay local and should respect reduced motion.

## Registration and selection

A renderer is an ordinary versioned component with `placement: "surface"`.
There is no second registry, plugin table or model-facing tool. Both
`update_frontend` and `runtime registry` use the existing registration path.
Source files remain under the account's external extension area. Source changes
and `registry_changed` events commit together; clients replace the matching
frame instead of reloading the application.

The effective placement is `view.placement` when present, otherwise the source
registration's placement. Among current, active, non-hidden components whose
effective placement is `surface`, select the largest tuple
`(max(created_at, view_updated_at or ""), id)`. This makes selection deterministic
across clients, restores and forks. Registering a new source version or reopening
an existing renderer brings it forward. Updating a hidden component never
reopens it. Other eligible renderers remain registered but are not mounted as
the current session renderer. If none remain eligible, use the default UI.

The session snapshot gives every component a `surface_selected` boolean. The
registry's read/list/register/restore/remove results, component layout responses
and accepted component reports expose the same selection fact. Selection is
desired state, not proof that a frame loaded. A hidden override is retained
across source changes; clearing a placement override returns to the registered
placement. Layout changes use the existing optimistic `version` and
`view_revision` check.

Removing a renderer adds a disabling version. Reset disables custom versions and
clears layout overrides while retaining messages and files. Fork copies the
registered versions and layout at the fork boundary, but never copies browser
render receipts or frontend client observations. Later changes are independent.

## Actual state and failure

`POST /api/frontend/observation` accepts an optional semantic `surface` field:

```json
{"provider":"component","id":"canvas","version":3,"view_revision":2,"state":"mounted"}
```

For a component, `state` is `loading`, `mounted` or `failed`; only `failed` must
include `error_code`, using the existing bounded component error codes. A
default renderer reports `{"provider":"default","state":"mounted"}`. A client
that does not implement this observation reports nothing, rather than implying
that the default UI is visible. Existing clients' unchanged revision heartbeats
remain valid across this additive protocol update.

Only an authenticated account can report a renderer registered in its current
conversation. An existing historical version can be reported during a live
version-change race. `runtime app action:snapshot` returns both the actual
client-reported `state.surface` and the server's current
`surface_registration`. A report that version 2 mounted does not prove a newly
selected version 3 rendered. Likewise, a default fallback does not convert a
failed custom version into success; its separate component render report remains
failed. All observations retain the existing client revision and freshness
lease. They describe client semantics, not pixel or interaction verification.

A failed renderer falls back to the default UI. The local failure record is
bound to its source identity and failure-time layout revision. Explicitly
reopening the same source at a newer layout revision permits another mount;
ordinary layout changes do not erase a mounted renderer's observation. Reports
from an older source or activation cannot mark the replacement healthy, and a
late mounted report from a failed activation cannot undo that failure.
Mounted evidence also belongs to one continuous iframe lifetime: switching away
and back, returning to a conversation, or leaving recovery starts at `loading`
until that new instance reports success. Reusing the same source does not reuse
its previous mount receipt.

Returning to the default UI suppresses all then-registered surfaces immediately,
while their hide requests finish. Out-of-order hide receipts never display a
standby renderer. The suppression is scoped to the account and conversation;
a later visible layout activation or a new visible source version can reopen a
surface even if the original hide request failed.

## Host bridge

Only the selected renderer receives `window.Athena.surface`. Ordinary component
placements retain their existing geometry bridge. The protocol is scoped to the
current account, session and component instance; replacement or navigation
invalidates the old instance. It exposes canonical data through explicit bounded
projections, not arbitrary API requests or parent DOM access.

The first bridge contract is:

| Method | Contract |
| --- | --- |
| `snapshot()` | Read protocol version 1, session ID, revision, phase, locale, reduced-motion preference, run/help/file projections. |
| `messages({before?, limit?})` | Read canonical message pages, at most 50 items, with `items`, `before` and `has_more`. |
| `file(fileId)` | Read a visible owned file's typed preview for the renderer's own layout; bounded raster images include a `data_url`. |
| `draft()` | Read shared input text, attachment projection and input revision. |
| `writeDraft({text, expected_revision})` | Compare-and-set shared input, retaining the user's newer text on conflict. |
| `subscribe(callback)` | Notify state revision changes; return an unsubscribe function. |
| `request({action, ...})` | Invoke the explicit host actions below and await their receipt. |

Actions initially include `send` with `expected_revision` and optional
`queue`/`interrupt` mode; `open` for history, files, account, appearance, activity
or surfaces; `preview` with a file ID; `stop`; and `default`. Other help, voice and
attachment-selection controls remain accessible through the fixed host footer.
This is a replaceable renderer with a bounded host API, not an arbitrary backend
execution API. Source code remains confined to the component sandbox and its
resource restrictions.

### Files inside a renderer

`file(fileId)` accepts an ID present in the current session's file list or a
canonical user/assistant message attachment. The server independently checks
account ownership. It returns `id`, `name`, `size`, `media_type`,
`content_revision` and one of the existing preview shapes:

- `kind: "image"`, `data_url`: PNG, JPEG, GIF, WebP or AVIF, at most 8 MiB of
  source bytes. Set an image's `src` to this URL or draw it in Canvas.
- `kind: "document"`, `blocks`, `truncated`: DOCX paragraphs/headings and nested
  tables; text remains text, with no original Word pagination guarantee.
- `kind: "spreadsheet"`, `sheets`, `truncated`: worksheet names, rows, original
  row numbers and truncation markers. `rows` contains display strings; optional
  aligned `cells` retains stored values, number formats, formatting status and
  formula/cache provenance. `formula_status: "missing"` means no calculated
  result, not blank or zero. `"saved"` is an existing cache, including an empty
  string, and is never proof of a fresh calculation. Preserve the top-level
  `formula_results: "not_recalculated"` and `calculation_warning` notices, and
  identify cells with `format_status: "unsupported"` as raw-value fallbacks.
  Empty `display` with nonempty `raw` means formatting hides a stored value;
  do not label it an empty formula result. Supported dates/times use explicit
  ISO-style dates and a 24-hour clock, not Excel's regional layout.
  Previews do not provide a spreadsheet calculation/editing engine.
- `kind: "text" | "markdown" | "html"`, `content`, `truncated`: source text.
  HTML is **not sanitized executable markup**. Render it as text or use the
  host's `request({action: "preview", file_id})` scriptless preview; do not insert
  untrusted file content into the surface's live `innerHTML`.
- `kind: "unsupported"`: the original remains available in the host file view.

The renderer owns the position, rotation, opacity and layout of these returned
values. No fixed preview panel is needed to lay out a Word passage, worksheet or
image inside the custom surface. `request({action: "preview", file_id})` remains
the explicit host preview fallback for large images and unsupported embedding.

The host constructs only fixed `/api/files/{id}/preview` and `/content` paths;
neither callers nor returned preview URLs can make it fetch an arbitrary URL.
Redirects are rejected and image streams are bounded independently of metadata
or Content-Length. `image_too_large` and `preview_too_large` are explicit errors.
The source bytes and data URLs cross only the frame RPC, never the model tool
transcript or frontend observation. No network CSP exception is added.
At most two file reads may be in flight per renderer, within the existing total
32-request budget; additional reads return `surface_busy` rather than queueing.
Completed or failed file receipts are released immediately, while concurrent
duplicates share the original read. Old IDs remain rejected as `receipt_expired`.
The ordinary mutation receipt cache is unchanged. Renderers should read large
files as needed and release their own unused data URLs and DOM references.

An available `content_revision` is sent as the existing revision query. A
read-start conflict or a newly observed file revision returns `file_changed`;
the renderer must reread state before requesting again. Revisions are change
detectors, not immutable historical byte snapshots. Every async boundary checks
the account, session, action scope and continuous renderer activation. A late
read after navigation, Stop, reset or replacement returns `surface_closed` and
cannot deliver bytes into a new session.

The host continues to own drafts, input revisions, execution, ownership checks
and canonical message history. A custom send receipt cannot declare success
before the host confirms acceptance. An unknown delivery outcome must return an
error rather than silently retrying an action that may already have started.
Reading or changing presentation never mutates canonical messages. Frontend
semantic observations do not include the shared draft text exposed to the
active renderer.

## Verification boundary

`tests/test_surface_renderer.py` exercises real registry/tool operations,
authenticated HTTP observations and durable SQLite state. It covers deterministic
single selection, concurrent registration, layout overrides, source restoration,
old-client compatibility, old-version observations, account separation, fallback
evidence, fork isolation and reset retention. These backend checks alone do not
verify the renderer, its controls, or recovery from a browser process hang.

`surface-runtime`, `surface-bridge`, `surface-app-handlers` and `surface-files`
tests exercise production SDK scripts, mounted React widgets, real App handlers,
canonical drafts, asynchronous receipts, file versions and typed file data. They
cover instance retirement, deliberate retry, bounded concurrent file reads and
releasing large completed receipts. A failed POST response never automatically
replays a send.

`node scripts/check-surface-webkit.mjs` runs the complete production React App in
a dedicated offline WKWebView with synthetic transports and nonpersistent state.
The visible test window exercises real iframe loading, arbitrary message layout,
shared drafts, live registration, failed sends and recovery. It does not access
operator credentials, user conversations or real providers. Native recovery is
independently checked by the Mac and iOS test bundles documented in their platform
guides.

Default `workspace` registrations now render as work content within the conversation.
`fullscreen` is an explicit focus view; `surface` replaces the entire communication
area. Image, Markdown, HTML, DOCX body and XLSX previews are page content, loaded
as they approach the viewport. Opening a preview through a host action presents
it on the current page instead of opening a separate workspace. The observer can
confirm an inline preview without incorrectly reporting a modal covering the chat.
DOCX is a body preview, not full Word page fidelity; spreadsheets are cached-cell
previews, not calculation or editing engines.

The default renderer keeps its brand, presence emblem and composer outside the
conversation scroll container. Reading no longer shrinks the emblem or changes
the conversation's geometry. The root viewport cannot scroll or chain overscroll.
Explicit presence-size preferences still apply. The same controlled textarea
grows with the draft up to 140 px (96 px in a short viewport), retaining its bottom
edge, focus and selection; clearing it restores one line. Stream updates do not
remeasure it. The offline WebKit suite checks these effects at 1080×780,
390×844 and 390×500, including late document loads and composition events. A
resized test viewport does not establish physical iOS keyboard or trackpad
acceptance.

Web deployments pair the frontend with the same web backend; desktop bundles
pair it with their local backend. The central account/model gateway does not
receive frontend observations. When rolling the web service back to a version
that predates surface observations, a newer open frontend must reload: the old
API rejects the new field with a deliberately generic validation error. Do not
guess which field failed or automatically retry a user message. The existing
client-version check reloads at a safe point after preserving the draft.

Recovery mode (`?recovery=1`) starts without any registered component. Successful
Reset retains messages/files, clears custom registrations and exits recovery for
the current page. The native recovery route remains available even when the frame
script is unavailable. Independent stop and
recovery must remain reachable outside the replacement frame. A same-process
JavaScript timeout is not proof of recovery from a process-wide hang.

## Dialogs without losing a custom input

Host dialogs temporarily make their background inert, including the selected
surface. Focus and Tab move into the dialog; closing it returns to the original
control. Switching from the menu to appearance inherits the original return
destination. The iframe is not unmounted, so its draft, text selection, scroll,
and local animation state can survive the interruption. Canonical streaming and
layout reports continue while the dialog is open.

For an opaque iframe, the parent restores focus through its current instance's
bridge. The child remembers its own last focused element and only restores it
while its document still has focus. No text, password, selection or DOM is sent
to the parent. A late message cannot deliberately steal focus from another
window. Native `dialog.showModal()` image previews keep the browser's own focus
handling; the host manager does not redirect them into an underlying dialog.
These are interaction guarantees for functioning browser processes, not a
recovery mechanism for an infinite JavaScript loop.

`node scripts/check-surface-webkit.mjs --modal` exercises the production App and
bridge at desktop and narrow sizes with a disposable, nonpersistent WKWebView.
It sends native keyboard/mouse events to that test window and verifies actual
input, preservation, focus return, and nested image dialogs. The native browser
may move focus to window chrome; the check does not claim to prevent that or
establish real iPhone keyboard/VoiceOver behavior.
