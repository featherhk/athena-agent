# Critical installed-Mac user pass

The test subject is the explicitly installed `~/Applications/Athena.app`, using
natural-language requests and the normal UI. Runtime records were inspected only
to distinguish actual effects from confident replies. Private transcripts,
credentials, screenshots and databases stay outside the repository.

## Problems found in build 18

| Ordinary action | Observed failure | Required improvement |
| --- | --- | --- |
| Ask for a three-row daily-expense Excel workbook | The model encountered a missing Python library, attempted a global install, then could not enter its existing workspace. It eventually generated a valid file through a temporary environment. | The shipped interpreter, document library and permitted working directory must work together on the first attempt. |
| Select the currently open conversation in History | The result disappeared and the companion looked empty, although the database still contained the conversation. | Selecting the same conversation must not clear its snapshot, draft or operation state. |
| Ask for a ten-second reminder, then start another conversation | The scheduled run completed, but the collapsed companion offered no indication that another conversation needed attention. The reminder was only found by opening Tasks. | Reuse the existing status position for actionable background results and pending help. |
| Quit and reopen the native app | The backend chooses a fresh localhost port, so origin-bound browser storage cannot reliably preserve session selection or unsent drafts. | Persist authenticated companion state outside the browser origin, before side effects and normal quit. Never replay restored uncertain operations. |
| Ask whether a slow Mac is short of memory, without changing anything | The answer treated low free RAM, allocated swap and cumulative counters as proof of current memory exhaustion, with invented thresholds. | Explain what the evidence establishes and what remains uncertain; use bounded read-only diagnostics. |
| Inspect the reminder's companion label | Internal task ID and injected trigger instructions appeared in accessibility text. | Use task provenance/title for presentation, while retaining recoverable original records. |

The first Excel trial did eventually pass the artifact check: the native Save
dialog produced a workbook in Documents, its ZIP structure was valid, the amounts
were 20, 35 and 12, and the sum formula had a cached result of 67. This is evidence
of eventual delivery, not acceptance of the preceding installation detour.

## Additional failure-path review

File previews need an in-place retry after a temporary failure and must reject
late responses from a previous file. A generated full companion surface must not
remove trusted Back, Stop and Reset. These paths have production React/WKWebView
coverage in addition to isolated tests.

## Release gate

Each candidate must be checked in a clean checkout, packaged independently of
unrelated working-tree changes, installed with a rollback backup, and exercised
again in the actual app. Passing mock or native fixture checks alone is not the
installed acceptance result.

## Installed build 19: first improvement loop

Source `774bb5e` was installed after signature verification and an external bundle
and SQLite backup. All 163 existing message rows matched their pre-install hashes
across all columns. Login remained available.

- Typed an unsent sentence, attached the previously generated test workbook,
  edited the sentence and immediately used normal Cmd+Q. The process stopped
  after the draft receipt. Relaunch changed localhost port 62864 to 62953;
  the selected session, complete sentence and attachment returned without a
  message submission. Clicking the attachment opened its actual spreadsheet.
- Cleared that draft, deliberately selected a new blank conversation and quit
  again. Relaunch on port 63014 preserved the empty selection; it did not revive
  the previous conversation or its attachment.
- Generated the same expense workbook through ordinary language. Python and
  workspace entry both worked immediately, with no package installation. However,
  missing formula caches led the model to replace the total with a static 67 and
  explain LibreOffice internals. This is partial task delivery, not an acceptable
  default editable spreadsheet. Build 20 adds a verified formula-plus-cache path.
- Entered an unsent sentence, opened History and selected that exact current
  conversation. The existing result, spreadsheet preview and draft all remained.
- The diagnostic trial read the new reference and used bounded read-only samples.
  It dropped the invented compression threshold but still over-attributed old
  swap usage while admitting current paging was nearly zero. The reference was
  tightened again; this trial is not a full diagnostic-quality pass.
- Started a safe `sleep 30` action, then clicked the visible Stop. The run became
  `interrupted`, its phase became `stopped`, no completion reply was emitted, and
  the UI returned to an editable idle state. This does not test a hung WebView.
- Old background results incorrectly replaced the current task's working status.
  The next candidate preserves the current active state and shows background
  attention again when idle, without consuming its unread receipt.

## Installed build 20: second improvement loop

Clean source `3d303e3` passed the complete repository check: 1,033 backend tests
(7 skipped), 403 frontend tests, TypeScript/Vite, and 67 production WKWebView
checks. The new bundle passed fresh native integration, two relocated sidecar
starts and an actual bundled-Python formula/cache/preview check with the GIL off.
The separate 25 native and 6 recovery checks were reused from build 19 because
native sources did not change; they were not counted as a new run.

The bundle was installed at the explicit application path after another verified
bundle/SQLite backup. All 193 pre-install messages remained identical. The prior
selected conversation reappeared on the new localhost port 64417.

The unchanged expense request now produced the requested workbook without an
installation detour or formula removal. Independent inspection verified
`SUM(B3:B5)` and cached result `67.0`; the actual app preview showed `67.00`.
The native Save dialog delivered the 5,592-byte workbook to Documents, identical
to the generated file, SHA256
`2db25a49be20bbf5c5ba602b9c51666122f8436097d7e864621d580a07b2aa5f`.
The reply described the amount and formula without the earlier LibreOffice
explanation. During the run the installed status showed thinking, despite old
background results; after completion the unread-result entry returned.

The existing scheduled reminder now showed its ordinary request, task title and
reply without internal trigger instructions in the default UI. Expanding its
Activity revealed the original trigger and reply, confirming that the information
was hidden progressively rather than discarded.

**The fresh diagnostic trial failed.** The model skipped the available skills,
executed its own diagnostic sequence and again claimed memory exhaustion from
free memory, historical swap allocation and cumulative counters. It also
misattributed denied process inspection to a macOS permission requirement.
Updating a progressive reference did not establish reliable evidence grounding.
The diagnostics scenario remains open; neither the passing software tests nor
the stronger reference are a passing model-behavior acceptance result.

### Preview distribution

The installed build 20 is also available as an immutable preview:

https://app.athena.featherhk.com/downloads/3d303e3/Athena-0.1.0-macos-arm64-preview.dmg

The complete public HTTPS download matched the local package: 55,705,146 bytes,
SHA256 `56145339e0ed4a1653e55d63f1cb7ae88cc2bbef76325233cd0f6b95396be5aa`.
It targets Apple Silicon/macOS 13 or later and has an ad-hoc integrity signature,
without Developer ID signing or notarization. The default update feed was checked
and remains build 15. This is a separate test candidate, not a claim of general
production readiness. The website, existing New API service and cloud execution
runtime were not redeployed.

Private installed acceptance receipt: `/tmp/athena-build20-installed-acceptance.json`.
Package/test receipt: `/tmp/athena-acceptance/athena-companion-release-20-g3hell0l/acceptance.json`.

## Follow-ups

The diagnostic failure above is reproduced in build 20. The first three items
below come from source review and a deterministic voice probe; the fourth is a
separate installed build 20 presentation observation.

1. **A reminder still needs a delivery path while Athena is hidden.**
   `src/TaskContinuity.tsx` pauses continuity polling when the companion is not
   visible. The new attention status helps when the user returns, but there is
   no native OS notification implementation. A scheduled run completing in the
   database is not evidence that a user working in another app received it.
   Follow up with hidden-app reminder and pending-help delivery, including a
   durable unread state; avoid notifications for old cancelled work.
2. **Voice energy can stop work before speech recognition confirms speech.**
   `src/voice-core.ts` uses an energy endpoint detector. A deterministic probe
   with 180 ms of RMS 0.06 energy during output playback emitted `start` at
   160 ms and `end` at 940 ms. In `src/voice-session.ts`, `start` interrupts busy
   work before ASR; an empty ASR result releases the output hold but cannot undo
   that interruption. This proves the control path, not the frequency of real
   acoustic false positives. Real-device tests with playback, background noise
   and deliberate speech are still needed before calling this reliable phone
   conversation behavior.
3. **The native Stop action still depends on responsive WebKit JavaScript.**
   `desktop/macos/Athena.swift` routes `stopWork` through `sendDesktopAction`
   and `evaluateJavaScript`. Trusted controls are reachable with a functioning
   renderer, but a hung WebKit context may not process that event. Native
   recovery can close panels and reload the page; that alone does not prove
   background execution stopped. An independent native-to-backend stop path
   with an execution receipt remains a follow-up. No renderer hang was injected
   into the installed app during this review.
4. **Intermediate files still leak into ordinary delivery.** The successful
   expense trial also exposed its generator script as an output-file card. The
   script is recoverable and belongs in the file area, but a nontechnical user
   asking for one workbook should not need to distinguish it from the deliverable.
   File discovery and intentional presentation need separate semantics; blindly
   hiding every script would break legitimate code-delivery requests.
