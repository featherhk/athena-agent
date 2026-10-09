# macOS companion host

Athena is a menu-bar companion on macOS. Its first launch shows a transparent compact panel; later launches stay in the menu bar until recalled. Option–Space toggles the companion and its desktop components. A shortcut registration conflict is shown in the native menu, which remains usable. The app has no default Dock entry or conventional chat window. The Python sidecar, CLI execution and existing data directory are unchanged.

## Native contract

Only the local sidecar's trusted main document receives the frozen `window.__ATHENA_DESKTOP__ = {version: 1}` marker. A query parameter alone cannot enable the bridge. `athenaDesktop` messages check the main frame, current view and local origin. Generated iframe content cannot invoke native operations directly.

The companion loads `/?desktop=companion` and accepts:

- `resize` with `mode: compact | expanded`; desired sizes are 560×260 and 760×680 macOS points, clamped to the current screen's visible area. Expanded mode accepts a measured `height` from 260 to 680 points to avoid excess space around short answers.
- `hide`, `observe`, and `hitRegions` (DOM viewport rectangles).
- `scene` with `scope: owner:session`, `session_id`, and up to 32 component specifications. An empty scope/session with an empty list clears the scene. Replacing the scope closes the previous account/session's panels.

Scene items carry `id`, `version`, `view_revision`, optional `title`, `x`, `y`, `width`, `height`, `screen_id`, and `hidden`. The native host loads a trusted `/?desktop=widget&session=…&component=…&version=…&scope=…` document; that document loads the registered component in its existing sandboxed iframe. Native source code and bundled application files are not rewritten by components.

`x` and `y` are normalized travel coordinates within the chosen screen's visible area: `(0,0)` places the panel at its top left; `(1,1)` places it against the bottom right. The distance is the visible area's size minus the clamped component size. Width and height are macOS points, defaulting to 320×240 and bounded by the screen. Layout-only updates retain the component's document, focus and drafts. A new component version receives a new WebKit view so delayed callbacks from the previous version cannot act as the new one.

Child documents may relay `widgetHealth`, `submit`, `navigate`, `hideWidget`, and `hitRegions`. Identity is assigned from the native WebKit-view binding, never from IDs supplied by the child. A component cannot resize or hide the companion. Media capture is denied in child panels. Main-page events use `athena:desktop` with `show`, `hidden`, `history`, `menu`, `stop`, `reset`, `state`, and identity-bound child interactions.

`state` events carry the current outer `scope` so the parent can reject asynchronous observations from a previous account or session. `state.desktop` reports screen geometry, native panel presentation, registry versions and health. Global screen rectangles use top-left coordinates relative to the primary display; component rectangles use top-left coordinates within their screen's visible area. `mounted` means the registered iframe has reported successful mounting. It does not mean unobstructed pixels or a successful user task.

## Input and recovery

Hiding retains the WebKit documents, unsent text and sidecar. It sends `athena:background`, stops microphone capture with the native WebKit capture API and pauses media playback. Recall does not start recording. Native sheets prevent hiding their owner so a file chooser, download or recovery confirmation cannot become unreachable.

Transparent panels use reported interactive rectangles to select `ignoresMouseEvents`; local/global pointer movement updates immediately, with a 50 ms fallback for cursor movement that does not produce a monitor event. The timer stops when all panels are hidden and restarts explicitly on recall. Until a document reports rectangles, the whole panel is interactive. An empty list makes the panel transparent to pointer input. There is no event injection or Accessibility permission request. Very fast boundary transitions and underlying third-party applications still require hands-on acceptance; native geometry tests alone do not prove every click-through interaction.

The menu always exposes recall, conversations, account/settings, stop, restore, update check and quit. Stop routes to the live application's existing stop operation; it is **not** an independent cancellation channel when WebKit is unresponsive. Native restore closes all custom panels and performs a safe GET in recovery mode without replaying a task or deleting chats/files. Same-origin main-document file links (`/api/files/{id}/content`) use WebKit's authenticated download flow, including links targeting a new tab, rather than sending the loopback URL to an unrelated browser. The native save panel leaves the current document and draft intact; non-success responses and external redirects are not saved as files. The sidecar is terminated only when Athena quits, not when panels hide. Unsent text may be lost during explicit recovery.

## Local acceptance

The isolated fixture executable uses a distinct bundle ID, loopback server and synthetic data; it never starts a real backend, signs in, changes the installed Athena app or asks for microphone access.

```sh
python3 desktop/macos/Tests/run-native-companion-checks.py --xcode /Applications/Xcode.app
python3 desktop/macos/Tests/run-native-recovery-checks.py --xcode /Applications/Xcode.app
```

The companion suite checks the transparent host, hotkey registration/conflict/release, origin and iframe isolation, screen clamping, draft continuity, status-menu events, real child-panel mounting, layout continuity, version replacement, empty-scene cleanup and native reset. Pointer tests check native routing state and hit geometry. If the fixture already has macOS event-posting permission, an additional check uses two windows belonging to that same fixture, verifies their stacking, and exercises actual WindowServer routing. Without permission it records an explicit untested result and does not prompt. It never clicks a third-party application. Download checks use one temporary authenticated cookie, retain the original document/draft, compare downloaded bytes, and bypass only the save-destination picker inside the test build.

To inspect the actual compiled UI in this host with a synthetic signed-out API:

```sh
python3 desktop/macos/Tests/run-native-companion-checks.py --xcode /Applications/Xcode.app --preview-dist dist
```

This writes `companion.png` and `companion-settings.png` alongside an acceptance receipt outside the checkout. It is a rendering test, not real-account/model/voice acceptance. Full-screen spaces, multiple physical displays, input methods and real microphone calls remain separate device tests.

For the full production main page, native component and iframe interaction chain against a synthetic API:

```sh
python3 desktop/macos/Tests/run-native-companion-checks.py --xcode /Applications/Xcode.app --integration-dist dist
```

This verifies real application scene registration, component mounting and body transparency, an SSE layout update that preserves the iframe's document/draft/selection, and a component-originated message going through the parent's normal intent and foreground-observation flow exactly once. It records component reports and submission receipts and writes both main-panel and widget screenshots. The fixture does not call a model or claim real task execution.
