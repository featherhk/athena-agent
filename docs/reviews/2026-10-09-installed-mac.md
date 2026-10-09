# Installed Mac companion acceptance

Build 16 was installed at `~/Applications/Athena.app`, replacing build 9.
The previous bundle and an SQLite backup were retained outside the repository.
Installation preserved the existing application data, preferences and WebKit
storage. It did not reset an account or copy operator credentials into the app.

## Actual installed-app checks

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

## Reported crash

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

## Limits

The automation tool's Option–Space input did not trigger global recall, from
either the companion or Finder. This is **not a passing global shortcut test**;
targeted key delivery and shortcut conflicts are not distinguished by this
observation. The visible Hide button and application Reopen did work. Fixture
registration/lifecycle checks do not replace physical global-key acceptance.

Real microphone acoustics, cross-app pointer passthrough and arbitrary Mac
application control were not tested in this installation pass. The installed
app remains build 16; the added regression changes test code only.

Local receipt: `/tmp/athena-build16-installed-acceptance.json`. Raw user data,
the crash report, backups and authentication state remain outside source control.
