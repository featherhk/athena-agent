# Keep input continuity when a flexible surface opens host controls

The existing full-session renderer already owns arbitrary HTML/CSS/Canvas
layout. The gap in this iteration was interaction continuity: opening history or
settings did not isolate the renderer's focus, and merely restoring focus to an
opaque iframe did not restore its inner textarea in real WebKit.

The host now owns a small modal-focus lifecycle. It identifies the current host
dialog, temporarily isolates background branches with `inert`, keeps Tab in its
enabled controls, and returns focus without remounting the renderer. It preserves
pre-existing inert state, handles nested and replaced dialogs, and releases
closed dialog references. Pointer-induced blur to the document body retains the
last application control as the return destination. The image lightbox's native
top layer remains managed by the browser, including its normal ability to move
keyboard focus to browser chrome.

An instance-checked bridge message restores the child's remembered control.
The child checks that it still owns document focus and that the control remains
connected. No child input contents or selection cross the bridge. Account,
stop, and recovery retain their independent host paths. Escape also closes the
activity and surface-list dialogs; native image-lightbox Escape is not consumed
by the lower host dialog.

## Evidence

- Ten DOM lifecycle cases cover nested/replaced dialogs, ordinary Tab boundaries,
  autofocus, hidden/empty dialogs, pointer blur, pre-existing inert, and cleanup.
- Bridge tests reject an old instance, a forged parent, a late restore after
  document focus moved away, and a removed input element.
- The production App passed **48 real WKWebView checks** at 1080 × 780 and
  390 × 500. Native input was calibrated by an actual trusted input event before
  checking covered-input isolation. Closing history restored the same iframe,
  selection `[3,8]`, scroll and draft, and the next native keystroke reached that
  textarea once. Streaming and a component view revision did not remount it.
- Menu-to-appearance replacement and a production Markdown image lightbox above
  the host dialog were checked. The latter used synthetic image-load completion
  to test actual `showModal`, keyboard and return-focus behavior; it is not an
  image-download or decoding test. Native Tab events that moved out to browser
  chrome were recorded, not mislabeled as focus trapped inside the lightbox.
- The existing whole-App WebKit suite passed **36 checks**; the spreadsheet
  suite passed **17**. The older synthetic transport needed the current durable
  submission/Stop receipt fields. Its unknown-send test still requires one POST,
  `delivery_unknown`, and no automatic replay; no success assertion was relaxed.

Receipts stay outside source control:

- `/tmp/athena-acceptance/athena-surface-modal-webkit-rKzCAY/receipt.json`
- `/tmp/athena-acceptance/athena-surface-webkit-kwKaG1/receipt.json`
- `/tmp/athena-acceptance/athena-spreadsheet-webkit-w1Eb9H/receipt.json`

These checks use synthetic sessions and a dedicated test window. They establish
neither physical iPhone keyboard/VoiceOver behavior nor recovery after a hung
WebKit process. No new fixed animation/effect enumeration was introduced and no
existing user renderer, draft or file was changed by these tests.
