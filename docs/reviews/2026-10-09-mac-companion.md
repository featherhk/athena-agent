# Mac companion acceptance

Status: partial verification. The complete release gates below remain open;
focused and offline checks are recorded separately with their limits.
This change makes the Mac a working environment for Athena; a movable emblem
alone does not establish desktop control or useful task completion.

## One natural interaction

Athena is quietly available on the desktop. Click its current appearance or use
the global shortcut to speak or type. The same conversation opens at the point
where the user left it. Athena works while the user returns to another app.
A finished result or a request for help is indicated without stealing focus.
Opening Athena reveals that exact result or question, including usable files.
Users need not learn names for window modes. A richer result may take more room;
the input and the independent native recovery controls remain easy to reach.

The first installation opens the ordinary account form when needed. It never
asks customers for an API key. Closing it leaves a clear way back. A custom
appearance cannot impersonate an authenticated account form or become the only
path to Stop, Reset, sign-out or task history.

## Release gates

Use disposable data and synthetic files for native tests. Do not replace the
user's installed application or move their real files for acceptance.

| User scenario | Required observable result | Evidence still needed |
| --- | --- | --- |
| First launch, logged out or expired login | Shortcut and appearance open the real login form; cancel and reopen work; successful login returns to the intended task and preserves the draft. | Native host + real frontend fixture. |
| Type, dismiss, recall | Text, attachments, selection and selected conversation survive; one send produces one accepted input. Empty recall never creates duplicate conversations. | Native keyboard and durable request identity. |
| Work in another app | Passive appearance/status changes do not activate Athena or consume clicks outside its interactive region. Only explicit recall moves keyboard focus to input. | Native input fixture, including an unrelated foreground window. |
| Send a long task, then hide | The task continues once, can be stopped from a reachable native control, and returns its result to the original conversation. Reopen does not resubmit it. | Durable run state and one external-effect receipt. |
| Task asks a question while hidden | A pending indicator remains until the actual question is viewed. Recall reveals the question; answer resumes the same run exactly once. | Help record, attention receipt and focus observation. |
| View a detailed answer, image, Word or workbook | The result is readable at a useful size; files remain reachable and downloadable. Opening and closing a preview preserves draft and conversation. No required content is clipped into the small appearance. | Actual preview and download in native WebKit. |
| Start voice, dismiss or switch account | No invisible microphone capture. Until a separate always-visible call indicator and hang-up are implemented, dismissing the interaction ends capture/playback without cancelling an already submitted task. Recalling does not silently restart recording. | Audio track/worklet shutdown, late-ASR rejection, native visibility transition. |
| Stop during voice/tool execution | Stop ends capture/playback and prevents further undispatched work. Dispatched external effects are reported honestly, never replayed or described as undone. | Cancellation and terminal state, including a delayed response. |
| Component fails or covers the screen | Native Stop/Reset remains available without executing the component. Reset removes generated presentation, retains chats/files and restores a usable input. | Failure fixture, not just a successful render callback. |
| Change screens, scale or workspace | The appearance/input remain recoverable on a connected screen. Coordinates and hit regions follow the same display mapping; resize never discards input. | Multiple-display/native coordinate tests. |
| Suspend, restart or reconnect | Existing run/result state reconciles without duplicating effects; interrupted execution remains explicit. No promise that local work continues while the Mac is asleep. | Restart/reconnect fixture and persisted audit. |
| Perform a useful Mac task | At least one real task finishes: inspect available storage, or copy user-selected test files into a named folder and reveal the result. Registration/animation alone cannot pass. | Filesystem or OS effect, independent verification and user-visible result. |

## Integration traps to inspect

- Frontend source affinity currently requires a fresh, foreground observation.
  Hiding the input must not be misreported as an interactive foreground client.
  Conversely, continuing work must not rely on an expired source and fail every
  subsequent interface operation. Define which desktop surface is actually
  visible, retain its client identity where valid, and test the full hidden-task
  round trip. Never silently retarget another account, session or browser.
- The host owns placement and focus; generated content owns its visual form.
  Report desired registration separately from mounted/visible/native state.
  A receipt from the previous source version cannot bless its replacement.
- Only the trusted application main frame may request privileged native host
  operations. A generated component uses the existing bounded bridge; injecting
  a native script handler into every frame is not an implementation shortcut.
- Window resize or compact presentation must not remount the application,
  recreate its audio state, discard a custom renderer's draft, or lose modal
  focus restoration. Login, help and rich previews need expansion on demand.
- Keep passive status distinct from an unread outcome. Merely fetching or
  mounting hidden content must not acknowledge a result as read.
- A hide action is not Stop or Quit. Native command labels and actual effects
  must agree, including when no web process is responsive.

## Scope and unresolved proof

Before this change, the Mac host was a single `NSWindow` containing
`WKWebView`, with the session surface bounded by the app work area.
The registered-component and independent recovery mechanisms are reusable.
Native companion placement, global recall, actual desktop observations and the
above acceptance results must be verified against the implementation that lands.

This checklist does not claim arbitrary modification of Finder, third-party
application interfaces, protected system screens, or unrestricted cross-app
computer use. Windows and physical iPhone behavior require their own hosts and
acceptance. Existing unrelated execution-client work is outside this review.

## Independent review during implementation

These findings were reported to the implementation owners; they are not release
results. Verify the final changes and attach their test evidence before closing.

- A native panel reusing one WebKit document across source versions needs an
  activation identity. An old document's late health/submission message must not
  be attributed to the new version, and an old failed navigation must not mark
  the replacement failed. Origin equality alone does not establish this.
- An empty scene sent for sign-out or no selected conversation must actually
  close old native panels. Rejecting empty identifiers before handling disposal
  can leave the previous account's presentation on screen.
- A direct Node probe of the initial `readDesktopState` implementation returned
  a valid empty state but threw `TypeError` for `screens: [null]` and
  `components: [null]`. Native-state validation must return a rejected observation
  without breaking the UI, and numeric bounds should match the backend schema.
- An open native sheet must remain accessible when the recall shortcut is used.
  The host owner is adding a hide guard. Transparent hit testing and focus need
  native verification; rectangular panels alone do not prove pointer passthrough.
- The draft native Stop menu dispatches a trusted-page JavaScript event. It is
  useful during ordinary operation, but is not an independent cancellation path
  when WebKit is unresponsive. Native safe recovery remains a separate capability.

Independent focused check: `uv run pytest tests/test_desktop_surfaces.py -q`
passed 27 tests in 1.75 seconds. This verifies the backend's durable placement,
ownership, receipt validation and old-client compatibility; it does not establish
native display, focus, click passthrough or any end-to-end gate above.

The malformed-state parser and empty-scope projection were repaired in
`src/desktop-bridge.ts`. Its nine focused Node tests pass, including null inputs,
strict bounded fields, duplicate identities, screen references, state coherence,
scene metadata projection and native visibility overriding WebKit's page state.
TypeScript compilation passed after this change.

`node scripts/check-surface-webkit.mjs --companion` then passed 22 assertions in
a separate nonpersistent WebKit window running the production React App. It
checks the compact entry; sign-in, cancel and draft retention; one accepted
submission; hiding versus stopping; same input/focus on recall; a Markdown
result and its collapse; a pending question answered once in the same run;
history/menu; content-sized results; native scene metadata and observed feedback; and a visible
uncertain-send recovery with the draft retained. Screenshots cover idle, login,
result and uncertain delivery. The account, API transport, persisted receipt
simulation and native scene feedback are synthetic. The fixture does not
establish real backend persistence, provider quality, microphone acoustics,
native panel passthrough or actual Mac file operations.

Visual review of the first passing run found an oversized result card for a
short response and a clipped presence emblem behind the login form. The latest
run verifies both refinements: the short result uses a 506-pixel window height;
the login emblem is fully visible at y=8 with bottom=86, and app/body/root/window
scroll positions are all zero. Idle, login, result and uncertain-send screenshots
were captured; login and result were inspected. Functional assertions are not a
blanket aesthetic approval or proof of all release gates.
An earlier fixture revision timed out when loading a file URL with a query;
the offline runner now uses a dedicated WebKit URL-scheme handler. Another
fixture revision omitted the help-answer event; matching the real event flow
resolved that test timeout without changing production submission behavior.

## Integrated acceptance evidence

The native host suite passed 18 checks, including the shortcut lifecycle,
main-frame bridge, source-version replacement, account scene disposal, dynamic
height, hiding without destroying drafts, stopping its hit-region timer while
hidden, and authenticated file download without navigating the conversation.
The existing six native recovery checks also pass. An additional native
integration check loads the production main App and the real DesktopWidgetPage,
receives the Widget mount report, updates placement without replacing its iframe
or losing its draft/selection, then submits exactly once through the parent
App's receipt flow. Its account/server are isolated fixtures, not real accounts.

A separate production-backend check used the configured model API and disposable
local account. Athena completed two real read-only CLI calls to query macOS and
the data volume's available space, and persisted three completed model requests.
Independent `sw_vers` and `df` checks agree with the returned tool output. The
model's prose labeled a GiB value as GB; this is not an exact-unit answer-quality
pass. The evidence does establish actual local execution rather than simulated
UI activity. An error in the acceptance script's final SQL report was recovered
from the stored run; no model request or command was replayed.

Real WindowServer pointer passthrough was not tested: the isolated native
fixture lacked OS event-posting permission and did not request it or inject
events. Tests prove bounded hit-region geometry and the native routing flags,
not a real click received by another application. Microphone acoustics, physical
multiple displays, arbitrary cross-app accessibility automation and independent
Stop when WebKit hangs remain outside the verified claim. Windows is deferred.
