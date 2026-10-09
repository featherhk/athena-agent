# Installed Mac companion acceptance

Build 16 was installed at `~/Applications/Athena.app`, replacing build 9.
The previous bundle and an SQLite backup were retained outside the repository.
Installation preserved the existing application data, preferences and WebKit
storage. It did not reset an account or copy operator credentials into the app.

## Build 16: actual installed-app checks

- Native launch displayed the companion and input. Existing login remained valid.
- A typed draft survived Hide and Reopen without resubmission. The backend's
  native observation recorded `presented=false` and `foreground=false` while
  hidden, then returned to the visible state on recall.
- A new installation-test conversation submitted a read-only Mac task from the
  native input. The live cloud model called local CLI, obtained macOS 15.4.1 and
  data-volume free space, and displayed a concise result. The durable run was
  complete and the tool receipt had exit code 0. Independent `sw_vers` and `df`
  output agreed; the reply distinguished GiB from approximate decimal GB.
- Quit shut down both the native process and its Python sidecar. Relaunch and
  recall succeeded; the authenticated account remained available.
- All 57 preexisting message records compared unchanged against the backup;
  both preexisting sessions remained present. SQLite `quick_check` returned `ok`.

## Build 16: reported crash

The crash shown during installation belongs to the old **build 9**, PID 37674,
launched on October 4. The report records `EXC_BAD_ACCESS` in
`applicationShouldHandleReopen`, messaging `makeKeyAndOrderFront:` on an invalid
window. The old closable-window lifetime is a likely cause; the report does not
record the preceding Close action.

Build 16 already sets `isReleasedWhenClosed=false` and intercepts closing as
hiding. A focused regression now forces `performClose` through the real window
delegate, followed by the real Reopen callback, for five cycles. It verifies
the same window, WebView, document and draft, plus background notification on
every close. The native fixture suite passes **19 checks**. No new build 16
crash report appeared during the installed acceptance.

## Build 16: limits at the end of that acceptance pass

The automation tool's Option–Space input did not trigger global recall, from
either the companion or Finder. This is **not a passing global shortcut test**;
targeted key delivery and shortcut conflicts are not distinguished by this
observation. The visible Hide button and application Reopen did work. Fixture
registration/lifecycle checks do not replace physical global-key acceptance.

Real microphone acoustics, cross-app pointer passthrough and arbitrary Mac
application control were not tested in this installation pass. The installed
app remained build 16 at the end of that pass; the added Close/Reopen regression
changed test code only.

Local receipt: `/tmp/athena-build16-installed-acceptance.json`. Raw user data,
the crash report, backups and authentication state remain outside source control.

## Build 17: interaction corrections

The build 17 candidate is based on clean source commit
`9c880d1913e5352f9738e565bd64d2675a0253a0`. This section records automated
acceptance of the interaction changes. It does not supersede the historical
build 16 installed-app results above.

- The reading surface retains ordered user questions and assistant replies.
  Sending a follow-up or queuing another request no longer replaces the previous
  answer or the reply still being generated. Tool messages remain in Activity;
  each run has one trace.
- The avatar reopens a collapsed reading surface. Hide preserves the chosen
  reading state, input and ongoing task. Collapsing an inline component retains
  its iframe document, form draft and selection rather than recreating it.
- Compact and expanded presentations keep the same width. Results grow above
  the avatar and composer; the controls keep their bottom anchors while streamed
  content grows. Only the reading area scrolls when its content exceeds the
  available height.
- The companion menu provides discoverable history and new-task controls.
  Escape closes the current dialog, then the reading surface, then hides the
  companion; it does not cancel work. IME composition is excluded from this
  shortcut. A new task uses a separate session, and returning to the previous
  task restores that session's unsent draft.
- Blocking dialogs hide the background avatar, reading surface and composer
  while preserving them. Login cancellation restores the original draft and
  controls. Activity now has an actual companion dialog, including when opened
  through an agent AppAction.
- An attached output and its selected preview share one display. Preview data
  resolves against the current canonical file entry, so updating a file does
  not leave the view on a captured attachment revision.
- Overlay components remain outside the reading card. Their declared interactive
  rectangles enter the native hit-region report; blank overlay space is not
  registered as a blocking surface.
- Repeated identical native state and geometry updates no longer trigger
  unnecessary state changes or window updates. Explicit application launch
  shows the companion. Shortcut registration and callback diagnostics remain
  separate from physical global-shortcut acceptance.

## Build 17: automated evidence

The final source check passed **1,012 backend tests with 7 skipped**, **374
frontend tests**, the TypeScript/Vite build, and the repository data guard.
The focused native-scope tests execute the production handler, including its
functional state updater and preservation of state identity for duplicate
native observations.

The production React application passed **48 checks in a real, nonpersistent
WKWebView** using synthetic accounts, transport and native scene receipts.
The fixture exercises the complete follow-up, queue, collapse, reopen, hide,
recall, help, history, new-task and error-recovery journey. It also checks:

- unchanged iframe document identity, textarea value and selection after
  collapse/reopen;
- exact fixed avatar/composer geometry across result expansion and sustained
  streamed growth, with **0 CSS-pixel drift** in the measured anchors;
- modal focus, hidden background controls and their restoration after cancel;
- an agent-requested Activity view with an `applied` receipt, followed by normal
  dismissal and draft recovery;
- one selected attached-file preview, followed by a canonical revision update;
- an overlay action's exact rectangle in the native hit-region report.

Local receipt:
`/tmp/athena-acceptance/athena-companion-webkit-Px5LhO/receipt.json`.
Idle, result and login screenshots from the fixture were visually inspected.
The login dialog no longer exposes the partially occluded avatar or composer
underneath it.

These checks do not contact a real model, mutate the installed application or
prove WindowServer delivery to an overlay. The native scene side effects are
synthetic in this fixture. The anchor measurements are geometry assertions,
not a frame-rate or animation-performance benchmark.

## Build 17: installed acceptance and follow-up findings

The exact candidate was installed at `~/Applications/Athena.app` after a normal
quit and a private application/SQLite backup. Signature verification passed.
The account remained signed in, and history showed the existing conversations.

Actual UI checks passed for opening history, reopening the previous conversation,
collapsing an active run, expanding it again through the avatar, stopping an
unproductive run, and opening/dismissing the Activity dialog. Ordered questions,
previous answers and the stopped run remained available after collapse/reopen.

A real-model fireworks request first exposed a progressive-discovery defect:
the model used `file` and then `reference` instead of the skill resource's `path`
parameter. The generic validation error offered no recovery guidance, and the
model fell back to an unnecessary whole-filesystem search. The run was stopped
through the installed UI. This was a failed unassisted acceptance attempt.

After an explicit parameter hint in the test conversation, the model read the
reference, observed the actual Mac screen, registered a new version of the
existing fireworks with `placement:desktop`, and set its layout to the screen's
visible frame. The native host reported the same version and layout revision
mounted and visible at `(0, 0, 1512, 944)` points. The previous framed/overlay
component disappeared from the companion. The generated source uses a transparent
canvas, empty interaction regions and a ten-second lifetime. This is a real
registration/geometry result, not a full-screen pixel, animation-frame-rate or
third-party click-through acceptance result. Menu bar and Dock are excluded.

A second real-use finding was that the mobile active-task composer grid also
applied to the 560-point native companion: Stop could be clipped after collapsing.
A real WKWebView regression reproduced the 13-pixel clipping. Multiline drafts
also exposed the native host ignoring preferred height in compact mode. These
follow-up corrections require a new candidate; they are not present in build 17.

Global Option-Space registration returned success, but the automation tool's
key delivery did not invoke the native handler. Physical global-key acceptance
remains unresolved. Real microphone acoustics, cross-app pointer passthrough and
arbitrary Mac application control were not tested in this pass.

## Separate continuity issue

A full native restart changes the loopback port. Selection is currently stored
by browser origin, and drafts/pending intents use tab storage. Existing account
and database history survive, but selected-session and unsent-draft restoration
across a complete application restart is not yet a reliable product guarantee.
A follow-up should use an account-scoped durable client recovery record, validate
session/file ownership, and reconcile existing operation IDs without replaying
possibly completed actions. Pinning the port alone would not solve this issue.

## Build 18 follow-up candidate

Source `a08a8271e9340c04dc2402dce30abe2b142d4c11` includes the failures found
during the installed build 17 pass:

- Skill reads expose an executable resource-reading shape. Validation rejects
  unexpected arguments with bounded repair instructions, without reflecting
  submitted values. An engine regression repairs the exact wrong-field failure
  and reads the reference without shell execution. Stable prompt/tool prefixes
  did not change.
- The compact desktop composer uses a single row even while a task is active.
  Both compact and expanded native layouts honor measured height; multiline
  input and Stop remain reachable, with the bottom anchor retained on recall.

The clean source passed **1,023 backend tests with 7 skipped**, **374 frontend
tests**, TypeScript/Vite and the repository data guard. Python 3.14.8T doctor
reported the GIL disabled after dependency imports. The native fixture passed
25 checks. The real WKWebView fixture passed **50 checks**, including both new
clipping regressions: single-line running input fits a 260-point panel; the
multiline running case uses 304 points and retains a 14-point bottom margin.
These are layout measurements, not frame-rate claims.

Local WKWebView receipt:
`/tmp/athena-acceptance/athena-companion-webkit-4EHDrp/receipt.json`.

The pre-build-17 SQLite backup and current database compared equal for all
71 preexisting message records; SQLite quick_check returned `ok`. Test messages
were appended through the UI, and the requested component gained a new
registered version. No old message was rewritten or deleted.

### Installed build 18 acceptance

The exact build 18 bundle was installed after a normal quit, with the previous
bundle and SQLite database backed up outside the repository. Signature integrity
passed; the installed Info.plist reports build 18. The authenticated account
remained available.

A fresh conversation asked for transparent, nonblocking Mac desktop fireworks
lasting ten seconds. No tool syntax or implementation hint was provided. The
real model completed the task with seven runtime calls, no tool errors and no
CLI search: read the desktop skill/reference, discover and inspect the client,
register the component, set its desktop layout, and inspect the actual host
receipt. The host reported the requested version mounted and visible across
the main screen's 1512×944-point visible area. No animation card appeared in the
companion. This demonstrates unassisted skill discovery and native placement;
it does not add full-screen pixel, frame-rate or third-party click-through proof.

During that real run, the reading surface was collapsed and a three-line draft
was typed without sending. An actual installed-app screenshot showed all three
lines, the voice control, interruption control, Stop and queued-send control
fully visible. The test draft was then cleared without submission. Expanding
the avatar restored the question and completed reply. A transient reconnect
notice cleared without losing the result. The app was left open with the
reading surface collapsed and an empty input.

The completion reply still exposes too much implementation detail for an
ordinary user; this remains a product follow-up, alongside the restart recovery
issue above. There is no claim that subjective responsiveness or all Mac
interaction problems are now solved.

Local installed receipt: `/tmp/athena-build18-installed-acceptance.json`.

### Distribution

The immutable build 18 preview is published at:

https://app.athena.featherhk.com/downloads/a08a827/Athena-0.1.0-macos-arm64-preview.dmg

The public HTTPS download was read in full: **51,819,552 bytes**, SHA256
`2a5d4659eecea34206b51db83326a6668dfa833cbca8dc18ec22afa280285115`, matching
the clean local manifest. The candidate is ad-hoc signed for integrity testing,
without Developer ID signing or Apple notarization. The public default feed
remains build 15; no website, New API service or cloud runtime was redeployed.
