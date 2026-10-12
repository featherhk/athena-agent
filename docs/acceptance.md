# Requirement-driven acceptance

Athena is tested by the state a user's request should produce, not whether its final sentence says “done”. Deterministic tests exercise the harness; an opt-in real-provider evaluation checks intent routing. Neither should use customer conversations as fixtures.

| User request | Observable result | Main checks |
| --- | --- | --- |
| Change this app to Simplified Chinese | Account preference changes and open sessions receive the durable event | `test_preferences`, `test_requirements`, live evaluation |
| Keep menus Chinese; answer in Japanese from now on | UI stays Chinese; a sourced global preference is recalled in a new conversation | `test_requirements`, live evaluation |
| Use a serif font and ocean colors | Shell consumes saved settings without a page reload | `test_preferences`, live evaluation; Safari and in-app visual review |
| Make the app black, or use an arbitrary color | Any opaque hex color persists; text/control contrast adapts; reset removes custom styles | `test_preferences`, `appearance.test.mjs`, real-provider black/custom-color cases |
| Keep a weather card or clock in the top corner while scrolling | Explicit anchored registration stays outside the chat scroller; registration, mount, visibility and functional correctness are distinct | `test_component_feedback`, real-provider clock case; browser observation |
| Show today's weather, but no location is known | Athena requests the missing city instead of inventing readings; the run releases its stream while waiting | `test_user_help`, real-provider missing-information case |
| Answer later, refresh, upload a missing file, or stop a question | The question survives restart; answers resume once in the same run; stop cancels waiting and queued work; other accounts cannot answer | `test_user_help`, `test_help_api` |
| Give yourself a reusable rectangle-area tool | Registered code lives outside the checkout and appears in a subsequent model request | `test_evolution`, live evaluation |
| Restore the previous version of a broken tool or component | A new registration points to the restored content; historical registrations remain immutable | `test_evolution` |
| Reuse a skill containing a script and a reference | Only metadata is initially supplied; named resources load on demand | `test_skillpacks`, `test_evolution` |
| Change my name or avatar | Authenticated account data changes and propagates; unrelated accounts cannot access it | `test_accounts`, live display-name evaluation |
| Change my password | Chat opens a fixed secret-input form; the password does not enter model tools or request snapshots | `test_accounts` |
| Show a Markdown reply and reasoning while working | One trace per run exposes every persisted round, reasoning, tool call and result, with paged original recovery | `test_trace`, provider parser, Safari user-help resume and trace review |
| Read this attachment; give me a workbook or document | Files are associated with messages, scoped to their owner, previewable and downloadable | `test_files` |
| Stop, reconnect, switch chats, or restart | Partial content survives; browser disconnect does not cancel a run; side effects are not silently replayed | `test_runtime`, `test_requirements` |
| See why Athena woke up without reading an internal dispatch prompt | Task title and trigger have durable provenance; the compact record expands into the complete activity; the actual reminder remains visible; ordinary matching text stays a user message | `test_task_events`, `message-origin.test.mjs`, migration and browser review |
| Keep long replies responsive and recover them after reconnecting | Batched writes retain pending reasoning/text on stop; lossless event encoding restores full frames from any cursor; archival preserves originals and a restorable backup | `test_event_log`, `test_shutdown`, private-copy migration/archive drill |
| Reset after a bad extension or model change | Fixed recovery restores defaults and disables extensions while keeping chats/files | `test_requirements`, `test_evolution` |
| Report context, cache hits, or the last hour's spending | Reported API usage is distinguished from estimates, unknowns and configured limits | `test_introspection` |
| Compact toward 20k tokens | Only tool projections are clipped; originals are recoverable; estimated target achievement is explicitly reported | `test_introspection` |

| Switch the visible conversation, open a view or preview a file | `runtime app` discovers real controls; a visible client claims once and returns an execution receipt | `test_app_controls`, `test_app_controls_integration`, `app-actions.test.mjs`, real-provider routing |
| Move an effect between messages or style individual characters | An isolated overlay receives bounded semantic geometry and can apply reversible text presentation | `effects.test.mjs`, real-provider registration, in-app overlay/text-style review |
| Read on a narrow screen or change to black | Input remains reachable, no horizontal overflow, readable theme tokens and a compact presence | 390px in-app viewport check; live theme change retains input |

## Activation and refresh

- Preferences and account state are stored before durable events. All open sessions catch up from SQLite in sequence; queues are wake signals, never the only copy of an event.
- Frontend registration increments a version. Structural snapshots merge independently of newer text chunks, so a continuous stream cannot starve component updates. A version replaces only its isolated frame; removal clears its text effects and retains the draft.
- Core frontend builds publish a checked version manifest. A safe client reload preserves the account/session draft; forms and active interactions defer reload. This is separate from component registration and does not cancel backend work.
- UI intents are account/session scoped, durably claimed before execution and acknowledged afterward. Uncertain dispatch is not replayed; opening a password form or starting a download is not proof of the subsequent external effect.
- A browser reports version-scoped mount/content/viewport observations and script/resource failures. An unobserved registration is not evidence that the user saw it; even a mounted frame is not proof its data or behavior is correct. Off-screen clients cannot claim visible success. Static and scripted widgets use the same mechanism.
- User help is a durable `waiting_user` state, not a busy loop. A single question uses the usual message composer; a short multi-question form is shown only when needed. Repeated identical answers are idempotent. Other input remains queued until an answer or explicit interruption. Pending help is not cloned into a fork.
- Backend extensions run in separate processes. A model request freezes its tool schema and code version together. A later registration affects the next request; disabling a tool blocks even previously frozen calls.
- Skill packages have immutable version directories. Core metadata and user skill metadata are captured at a persisted context boundary. Newly registered skills can be discovered immediately through the registry; stable prompt prefixes are not retroactively rewritten.
- Compaction persists a new projection boundary. A model switch creates an explicit context epoch. Request records retain their original profile, prompt and tool definitions.
- Core application deployments still require a controlled backend restart. Runtime extensions do not edit or hot-patch authentication, storage, the process supervisor or reset. A core restart interrupts active work and does not replay unknown side effects.

## Running the checks

```sh
npm run check
uv run --directory backend python -m athena.doctor
uv run --directory backend python ../scripts/evaluate_requirements.py --key-file /path/to/operator-key
```

The real-provider evaluation uses a temporary account/database and removes its files when finished. It prints verdicts and usage totals; failures may include a short synthetic reply and the selected operation names. Credentials and provider request recordings are never printed. It is an explicit paid check, excluded from CI.

## Honest limits

This is extensibility with recovery, not arbitrary safe self-rewriting of the application core. The new [managed-browser adapter](browser-execution.md) is separately tested on synthetic pages; it does not provide full desktop control. No claim of production billing, operating-system isolation across platforms, or exact local provider tokenization is made. XLSX previews show bounded cells and cached values, not a full spreadsheet engine. Safari and the in-app browser were used for the earlier visual acceptance; the earlier Chrome client-side block is distinct from the new executor's isolated Chrome tests. Browser observations are recorded separately from deterministic or model-routing checks. Generated sidebars and other components remain session scoped; they do not automatically become an account-wide shell. Live weather refresh still requires an explicit backend data/task implementation; registering a card does not prove automatic fresh data.

## Native pointer routing regression — 2026-10-10

Installed Build 23 exposed a gap in the native fixture: dragging a file work surface's title selected its text instead of moving the surface. Both the file gesture layer and owl gesture layer used top-left DOM rectangles in flipped views, but `NSView.hitTest` supplied points in their unflipped parent coordinates. The earlier tests called the gesture layer directly and missed that conversion.

The regression now enters through the actual container's hit-test route. It checks the file title and bottom-right resize grip, passes table cells and header controls to WebKit, rejects vertically mirrored hit areas, and verifies that the owl does not capture the composer. The new parent-route test failed against the old code. After converting the incoming point from the superview, all 41 native companion checks passed, including resize coordinates, layout persistence acknowledgement/rollback and transient expiry.

This fixture does not inject global pointer events; OS event-posting access was unavailable to the isolated test executable. Installed-app acceptance subsequently found that the title was hit correctly, but `performDrag` did not move the window under delivered events. The final implementation explicitly tracks event screen coordinates, applies a three-point threshold, and commits only effective movement. Its 45 native checks enter through the parent hit-test route and cover no-op clicks, rollback, screen clamping, revision races and expiry.

### Installed Build 25 and public preview

The clean `c4cc0fa` macOS bundle was installed and exercised through Computer Use:

- Dragging the file title changed normalized position from `(0.5, 0.5)` to `(0.573260, 0.562112)` and incremented the durable layout revision exactly once. It did not select text.
- The resize grip changed the work surface from 420 × 300 to 820 × 510 points. Maximize and restore worked. Closing persisted `hidden=true`; restarting showed the companion without reopening that surface. Selecting the file in the current conversation reopened the same component at its saved size.
- Known legacy fireworks were retired through versioned registry removal with sources retained. They did not replay after restart. New transient expiry/Stop behavior is covered by backend/native tests; this session did not record a fresh live timed-fireworks run.
- All 312 pre-install message rows matched the backup hash before the follow-up synthetic acceptance request. Neither the installer nor duplicate-fixture cleanup removed chats or files.

A real hosted-model task used explicitly fictional quotes: A quoted 8,000 + 3,000 + 2,000; B quoted 7,600 + 3,400 with installation missing. Athena created an XLSX and displayed it as an independent work surface. A follow-up added B's installation price of 2,500 and requested an update to the original file and panel. The UI showed the changed values; an independent workbook read verified B's installation = 2,500, B's cached total = 13,500, A's cached total = 13,000, and no remaining installation omission. File ID and component ID/version remained unchanged, with exactly one active file component. This verifies continuity within the current conversation, not automatic routing among concurrent tasks.

The first real task exposed duplicate surfaces from agent registration plus automatic file delivery. Automatic presentation now transactionally reuses an existing surface for that session/file and preserves hidden/removed choices. Explicit registration can still intentionally create multiple views. Nine additional cases include independent SQLite-connection competition.

Release checks: 1,114 Python tests passed / 7 skipped; 449 Node tests passed; Python dependency imports retained GIL-free operation. The packaged app passed relocation, startup, auth/origin, restart/shutdown and credential-exclusion smoke checks. The public DMG was fully downloaded, hashed, mounted and checked against the clean source, app metadata, dependency notices and ad-hoc signature integrity. The update checker reports Build 15 → 25 available and matching Build 25 current. Developer ID signing and notarization remain unavailable. QuickTime reported an error during a recording attempt; no reviewed demonstration video is delivered by this acceptance.


## Local source setup — 2026-10-10

The later [Build 26 acceptance](releases/2026-10-10-build26.md) adds a real-provider
fresh-checkout path, independently checked workbook bytes, stop/restart without
replay, installed Quit/draft recovery and a browser-downloaded public DMG. The
fixture-only path below remains useful protocol coverage and is not conflated
with that later live acceptance.

The current production frontend and backend were started with a fresh temporary data directory, explicit loopback binding and no cloud gateway or provider credentials. A separate loopback HTTP server returned a synthetic Chat Completions SSE response in multiple chunks. No customer database or installed app was used and no paid model request was made.

The in-app browser completed this user journey:

1. Open the fresh instance. **Model connection** appears without asking for an Athena login.
2. Select the local preset, enter the fixture model and endpoint, and save with an empty key. The settings close; saving itself sends no model request.
3. Send a message. The real HTTP request reaches the fixture with the selected model and `stream: true`; the user message and completed assistant reply are visible.
4. Refresh. Both messages return. The menu offers **Model connection**, without hosted login or balance controls. Reopening it preserves provider, model and base URL; the key field remains empty.
5. Close settings with Escape. Focus returns to the menu button.

A read-only database check found exactly one local identity, one conversation, two completed messages and one completed model request. The persisted default profile and conversation both selected the local configuration. A guarded-DOM check of the actual frontend observation function confirmed that it never reads provider, model or password input values and reports only the host-owned account-view metadata.

Chrome's client blocked the specific `localhost` test origin in this run with `ERR_BLOCKED_BY_CLIENT`; its block was not bypassed. The existing authorized in-app browser successfully opened that same origin. This result is distinct from the managed-browser executor's tests and does not establish general provider compatibility, a paid cloud connection or installed-app acceptance.

The review also found and fixed an IPv6 loopback boundary: `[::1]` could start in local mode but its browser Origin was rejected. Targeted HTTP tests now cover same-origin bootstrap, session-cookie reuse, keyless local configuration and the selected profile in a new conversation. Alternate ports, HTTPS origins, mapped/other IPv6 addresses and deceptive hostnames remain rejected.

## Focused desktop interaction — 2026-10-13, Build 27

The installed Mac preview uses a compact header above the current exchange and
keeps the composer at the lower edge. Earlier messages can be expanded in place;
queued, running and waiting work remains visible. This is presentation only: no
model context or durable history is removed. Explicitly delivered files appear as
results; intermediate working files stay accessible in a collapsed disclosure.
Requested source code is still a result. Retained files from earlier exchanges
no longer appear to belong to an unrelated current reply.

A real hosted-model task used fictional quotes: A materials 800 plus installation
200, B materials 750 with installation missing. The installed app completed the
request while collapsed and showed the result when recalled. A follow-up supplied
B's installation of 180 and updated the original XLSX and the same logical desktop
component (a new component version, not a second panel). The native preview and an
independent read of workbook values/formula caches both showed A 1000, B 930 and
a difference of 70. The old conversation and its earlier workbook remained
reachable. Quit/relaunch restored an unsent synthetic draft without submitting it
or reopening a closed file panel; all 439 message rows in the native database
matched the pre-restart snapshot. The test draft was then cleared.

The native runtime uses its own **Athena Desktop** data directory. A separate
662-row development database was initially checked during upgrade preparation;
that check is not evidence about native data preservation. A consistent backup
of the actual native database was subsequently taken. The installed-app checks
above establish visible continuity and restart preservation, not a full
pre-upgrade hash comparison of every native record.

Validation: clean source `5f5bbb7`, 1118 Python tests passed / 7 skipped, 459 Node
tests passed, and dependency imports kept Python 3.14T's GIL disabled. Real
WebKit checks passed: 98 companion, 48 modal-focus, 37 surface and 17 spreadsheet.
Companion tests now deliver events to their own visible native view without
activating the application; dedicated modal tests retain activation to verify
real focus. Bundle relocation, startup, authentication/origin boundaries,
shutdown/restart and credential exclusion passed. The public artifact was fully
downloaded and its bytes, hash and download headers matched the local manifest.

[Build 27 download](https://app.athena.featherhk.com/downloads/5f5bbb7/Athena-0.1.0-macos-arm64-preview.dmg)
is an Apple Silicon/macOS 13+ preview, ad-hoc signed and **not notarized**.
Only static distribution artifacts and the Mac update feed changed in this
release; the hosted web runtime was not deployed or restarted.

Remaining product work: long prompts still consume too much reading space;
spreadsheet previews do not fully reproduce original workbook layout; closing a
result and resuming conversation still need a more direct relationship. This
release does not establish automatic task routing or reliable control of Finder
and arbitrary third-party application windows. Desktop component control must
not be presented as proof of full system control.
